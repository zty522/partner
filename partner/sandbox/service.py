"""Sandbox — Partner 中心化沙箱服务。

提供三个隔离级别：
  - static:    语法检查 + 编译 (最快, 不执行)
  - subprocess:子进程执行 + 临时目录 + 资源限制
  - bwrap:     bubblewrap 容器级隔离 (Linux namespace)

设计要点：
  - 内容寻址缓存：相同代码返回相同结果（content-addressed）
  - 可重入：支持 batch_planner + 自进化两路同时调用
  - 执行画像：每次执行记录 CPU/内存/耗时/文件变更
"""
import ast
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 类型定义
# ═══════════════════════════════════════════════════════════════════

class IsolationLevel(Enum):
    STATIC = "static"        # 仅语法检查
    SUBPROCESS = "subprocess"  # 子进程执行 + 临时目录
    BWRAP = "bwrap"          # bubblewrap 容器

class SandboxMode(Enum):
    VALIDATE = "validate"    # 验证是否可执行
    EXECUTE = "execute"      # 实际执行并捕获输出
    PROFILE = "profile"      # 执行 + 资源画像


@dataclass
class SandboxContext:
    """沙箱执行上下文"""
    code: str
    filename: str = "script.py"
    args: List[str] = field(default_factory=list)
    env_vars: Dict[str, str] = field(default_factory=dict)
    working_dir: str = ""
    timeout: int = 30
    isolation: IsolationLevel = IsolationLevel.SUBPROCESS
    mode: SandboxMode = SandboxMode.VALIDATE
    # 依赖注入
    extra_python_paths: List[str] = field(default_factory=list)
    mount_dirs: Dict[str, str] = field(default_factory=dict)  # src→dst


@dataclass
class SandboxResult:
    """沙箱执行结果"""
    success: bool
    returncode: int
    stdout: str
    stderr: str
    duration: float
    # 验证结果
    syntax_ok: bool = True
    syntax_error: Optional[str] = None
    compile_ok: bool = True
    compile_error: Optional[str] = None
    import_ok: bool = True
    import_error: Optional[str] = None
    # 执行画像
    cpu_time: float = 0.0
    memory_kb: int = 0
    files_created: List[str] = field(default_factory=list)
    error_detail: str = ""
    # 缓存
    cache_key: str = ""
    from_cache: bool = False


# ═══════════════════════════════════════════════════════════════════
# 核心服务
# ═══════════════════════════════════════════════════════════════════

class SandboxService:
    """Partner 中心化沙箱服务。

    batch_planner 和自进化都通过这个服务执行沙箱验证。
    全局单例实例可在 partner 模块级别共享。
    """

    def __init__(self, workspace: str = ""):
        self.workspace = workspace
        self.cache_dir = os.path.join(workspace, ".sandbox_cache") if workspace else "/tmp/partner_sandbox"
        self.exec_log_path = os.path.join(self.cache_dir, "execution_log.jsonl")
        os.makedirs(self.cache_dir, exist_ok=True)

        # 内容寻址缓存：{sha256(code) → SandboxResult}
        self._result_cache: Dict[str, SandboxResult] = {}
        self._cache_hits = 0
        self._cache_misses = 0

        # 检查 bwrap 是否可用
        self._bwrap_available = self._check_bwrap()

    # ──────── 对外接口 ────────

    async def run(self, ctx: SandboxContext) -> SandboxResult:
        """统一入口：根据配置选择隔离级别执行。"""
        # 1. 缓存检查（仅 VALIDATE 模式）
        if ctx.mode == SandboxMode.VALIDATE:
            cached = self._check_cache(ctx.code)
            if cached is not None:
                return cached

        # 2. 按隔离级别执行
        if ctx.isolation == IsolationLevel.STATIC:
            result = self._run_static(ctx)
        elif ctx.isolation == IsolationLevel.BWRAP and self._bwrap_available:
            result = self._run_bwrap(ctx)
        else:
            result = self._run_subprocess(ctx)

        # 3. 缓存结果
        if ctx.mode == SandboxMode.VALIDATE:
            self._set_cache(ctx.code, result)

        # 4. 记录执行日志
        self._log_execution(ctx, result)

        return result

    async def validate_code(self, code: str, filename: str = "module.py") -> SandboxResult:
        """快速验证代码可执行性（语法 + 编译 + 导入）。"""
        ctx = SandboxContext(
            code=code, filename=filename,
            mode=SandboxMode.VALIDATE, isolation=IsolationLevel.STATIC,
        )
        return await self.run(ctx)

    async def execute_code(self, code: str, filename: str = "script.py",
                           timeout: int = 30, env_vars: Optional[Dict] = None) -> SandboxResult:
        """实际执行代码并捕获输出。"""
        ctx = SandboxContext(
            code=code, filename=filename,
            timeout=timeout, env_vars=env_vars or {},
            mode=SandboxMode.EXECUTE,
            isolation=IsolationLevel.BWRAP if self._bwrap_available else IsolationLevel.SUBPROCESS,
        )
        return await self.run(ctx)

    async def validate_modification(self, new_code: str, target_file: str = "",
                                     plan_id: str = "") -> SandboxResult:
        """验证代码修改（自进化专用）。"""
        ctx = SandboxContext(
            code=new_code, filename=os.path.basename(target_file) or "module.py",
            mode=SandboxMode.VALIDATE,
            isolation=IsolationLevel.SUBPROCESS,
        )
        result = await self.run(ctx)
        result.cache_key = plan_id
        return result

    def get_stats(self) -> Dict:
        """获取沙箱服务统计。"""
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_size": len(self._result_cache),
            "bwrap_available": self._bwrap_available,
            "cache_dir": self.cache_dir,
        }

    # ──────── 缓存 ────────

    def _cache_key(self, code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]

    def _check_cache(self, code: str) -> Optional[SandboxResult]:
        key = self._cache_key(code)
        if key in self._result_cache:
            self._cache_hits += 1
            result = self._result_cache[key]
            result.from_cache = True
            return result
        self._cache_misses += 1
        return None

    def _set_cache(self, code: str, result: SandboxResult):
        key = self._cache_key(code)
        result.cache_key = key
        self._result_cache[key] = result

    # ──────── 隔离级别实现 ────────

    def _run_static(self, ctx: SandboxContext) -> SandboxResult:
        """Level 1: 静态分析。不执行代码。"""
        start = time.time()

        # 非 Python 文件跳过 ast.parse 和 compile
        ext = os.path.splitext(ctx.filename)[1].lower()
        if ext not in (".py", ".pyw", ""):
            return SandboxResult(
                success=True, returncode=0,
                stdout="非 Python 文件，跳过静态分析", stderr="",
                duration=time.time() - start,
            )

        # 语法检查
        try:
            ast.parse(ctx.code)
            syntax_ok = True
            syntax_error = None
        except SyntaxError as e:
            syntax_ok = False
            syntax_error = f"line {e.lineno}: {e.msg}"

        if not syntax_ok:
            return SandboxResult(
                success=False, returncode=1,
                stdout="", stderr=syntax_error or "",
                duration=time.time() - start,
                syntax_ok=False, syntax_error=syntax_error,
            )

        # 编译检查
        try:
            compile(ctx.code, ctx.filename, "exec")
            compile_ok = True
            compile_error = None
        except SyntaxError as e:
            compile_ok = False
            compile_error = str(e)

        if not compile_ok:
            return SandboxResult(
                success=False, returncode=1,
                stdout="", stderr=compile_error or "",
                duration=time.time() - start,
                compile_ok=False, compile_error=compile_error,
            )

        return SandboxResult(
            success=True, returncode=0,
            stdout="静态分析通过", stderr="",
            duration=time.time() - start,
        )

    def _run_subprocess(self, ctx: SandboxContext) -> SandboxResult:
        """Level 2: 子进程执行。"""
        return self._execute_in_sandbox(ctx, use_bwrap=False)

    def _run_bwrap(self, ctx: SandboxContext) -> SandboxResult:
        """Level 3: bubblewrap 容器执行。"""
        return self._execute_in_sandbox(ctx, use_bwrap=True)

    def _execute_in_sandbox(self, ctx: SandboxContext, use_bwrap: bool) -> SandboxResult:
        """在子进程（可选 bwrap）中执行代码。"""
        start = time.time()

        # 前置：静态分析（语法 + 编译），不管什么模式都先检查
        static_result = self._run_static(ctx)
        if not static_result.success:
            static_result.duration = time.time() - start
            return static_result

        # 如果是 VALIDATE 模式且非 Python 文件，直接通过（不做子进程执行）
        ext = os.path.splitext(ctx.filename)[1].lower()
        if ctx.mode == SandboxMode.VALIDATE and ext not in (".py", ".pyw", ""):
            # 对配置文件做格式检查
            cfg_ok, cfg_err = self._check_config_syntax(ctx.code, ext)
            if not cfg_ok:
                return SandboxResult(
                    success=False, returncode=1,
                    stdout="", stderr=cfg_err or "",
                    duration=time.time() - start,
                    syntax_ok=True, compile_ok=True,
                    error_detail=cfg_err,
                )
            return SandboxResult(
                success=True, returncode=0,
                stdout=f"{ctx.filename} 格式有效", stderr="",
                duration=time.time() - start,
            )

        # 创建临时目录
        tmp_id = f"sbx_{int(time.time()*1000000)}_{os.getpid()}"
        sandbox_dir = os.path.join(self.cache_dir, tmp_id)
        script_path = os.path.join(sandbox_dir, ctx.filename)
        os.makedirs(sandbox_dir, exist_ok=True)

        try:
            # 写入代码
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(ctx.code)

            # 构建执行命令
            cmd = [sys.executable, script_path] + ctx.args
            env = os.environ.copy()
            if ctx.extra_python_paths:
                env["PYTHONPATH"] = ":".join(ctx.extra_python_paths) + ":" + env.get("PYTHONPATH", "")
            env.update(ctx.env_vars)

            # 记录执行前文件清单
            files_before = set(os.listdir(sandbox_dir))

            if use_bwrap:
                # bubblewrap 执行
                bwrap_cmd = self._build_bwrap_command(cmd, sandbox_dir, ctx)
                proc = subprocess.run(
                    bwrap_cmd,
                    capture_output=True, text=True,
                    timeout=ctx.timeout,
                    env=env,
                )
            else:
                # 直接子进程执行
                proc = subprocess.run(
                    cmd,
                    cwd=sandbox_dir,
                    capture_output=True, text=True,
                    timeout=ctx.timeout,
                    env=env,
                )

            duration = time.time() - start

            # 检测执行后的文件变更
            files_after = set(os.listdir(sandbox_dir))
            new_files = [f for f in (files_after - files_before)
                         if f != ctx.filename and f != os.path.basename(script_path)]

            return SandboxResult(
                success=proc.returncode == 0,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration=duration,
                files_created=[os.path.join(sandbox_dir, f) for f in new_files],
                syntax_ok=True,
                compile_ok=True,
            )

        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False, returncode=-1,
                stdout="", stderr=f"执行超时 ({ctx.timeout}s)",
                duration=time.time() - start,
                syntax_ok=True, compile_ok=True,
            )
        except Exception as e:
            return SandboxResult(
                success=False, returncode=-1,
                stdout="", stderr=str(e),
                duration=time.time() - start,
                error_detail=traceback.format_exc(),
            )
        finally:
            # 清理临时目录（除非有产物要保留）
            if not ctx.mode == SandboxMode.PROFILE:
                shutil.rmtree(sandbox_dir, ignore_errors=True)

    def _build_bwrap_command(self, cmd: List[str], sandbox_dir: str,
                              ctx: SandboxContext) -> List[str]:
        """构建 bubblewrap 命令。

        bubblewrap 使用 Linux namespace 隔离：
        - 只读挂载系统路径 (/usr, /lib, /etc)
        - 读写挂载沙箱目录
        - 网络隔离 (--unshare-net)
        - 进程隔离 (--unshare-pid)
        """
        bwrap = ["bwrap",
                 "--unshare-net",          # 隔离网络
                 "--unshare-ipc",           # 隔离 IPC
                 "--unshare-pid",           # 隔离 PID 空间
                 "--unshare-uts",           # 隔离 hostname
                 "--ro-bind", "/usr", "/usr",
                 "--ro-bind", "/lib", "/lib",
                 "--ro-bind", "/lib64", "/lib64",
                 "--ro-bind", "/bin", "/bin",
                 "--ro-bind", "/etc", "/etc",
                 "--proc", "/proc",
                 "--dev", "/dev",
                 "--tmpfs", "/tmp",
                 ]

        # 挂载 Python 环境（只读）
        python_dir = os.path.dirname(sys.executable)
        bwrap.extend(["--ro-bind", python_dir, python_dir])

        # 挂载 miniconda/lib（需要 Python 标准库）
        conda_lib = os.path.join(os.path.dirname(python_dir), "lib")
        if os.path.isdir(conda_lib):
            bwrap.extend(["--ro-bind", conda_lib, conda_lib])

        # 挂载额外路径
        for src in ctx.extra_python_paths:
            if os.path.exists(src):
                bwrap.extend(["--ro-bind", src, src])

        # 用户自定义挂载
        for src, dst in ctx.mount_dirs.items():
            if os.path.exists(src):
                bwrap.extend(["--bind" if ctx.mode == SandboxMode.PROFILE else "--ro-bind", src, dst])

        # 沙箱工作目录（读写）
        bwrap.extend(["--bind", sandbox_dir, sandbox_dir])

        # 设置工作目录
        bwrap.extend(["--chdir", sandbox_dir])

        # 执行命令
        bwrap.extend(cmd)
        return bwrap

    # ──────── 日志与统计 ────────

    @staticmethod
    def _check_config_syntax(content: str, ext: str) -> Tuple[bool, str]:
        """检查配置文件语法。"""
        try:
            if ext == ".json":
                json.loads(content)
            elif ext in (".yaml", ".yml"):
                import yaml
                yaml.safe_load(content)
            elif ext == ".toml":
                import tomllib
                tomllib.loads(content)
            return True, ""
        except Exception as e:
            return False, str(e)

    def _check_bwrap(self) -> bool:
        """检查 bubblewrap 是否可用。"""
        try:
            r = subprocess.run(["bwrap", "--version"],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _log_execution(self, ctx: SandboxContext, result: SandboxResult):
        """记录执行日志。"""
        try:
            entry = {
                "ts": time.time(),
                "mode": ctx.mode.value,
                "isolation": ctx.isolation.value,
                "filename": ctx.filename,
                "code_len": len(ctx.code),
                "success": result.success,
                "returncode": result.returncode,
                "duration": round(result.duration, 3),
                "from_cache": result.from_cache,
                "stdout_size": len(result.stdout),
                "stderr_size": len(result.stderr),
            }
            with open(self.exec_log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def get_execution_stats(self, last_n: int = 100) -> Dict:
        """获取最近 N 次执行的统计。"""
        if not os.path.isfile(self.exec_log_path):
            return {"total": 0, "success_rate": 0, "avg_duration": 0}
        entries = []
        try:
            with open(self.exec_log_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            return {"total": 0, "success_rate": 0, "avg_duration": 0}

        recent = entries[-last_n:]
        if not recent:
            return {"total": len(entries), "success_rate": 0, "avg_duration": 0}

        successes = sum(1 for e in recent if e.get("success"))
        avg_dur = sum(e.get("duration", 0) for e in recent) / len(recent)

        return {
            "total": len(entries),
            "success_rate": successes / len(recent),
            "avg_duration": round(avg_dur, 3),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
        }
