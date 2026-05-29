"""Direct Executor — 直接动手执行模块。

当用户说"直接动手"、"自己去跑"、"别调研了，修泄漏"时，
跳过所有搜索和调研环节，直接：
1. 定位项目目录（从 context_broker 获取 project_path）
2. 根据用户指令选择要运行的脚本或命令
3. 在隔离环境中执行
4. 捕获输出和错误
5. 自动调用 QQ bridge 发送结果摘要
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 允许执行的目录白名单（安全限制）
ALLOWED_DIRS = [
    "/mnt/e/work/",
    "/mnt/d/work/",
    "/home/",
]

# 允许执行的脚本扩展名
ALLOWED_EXTENSIONS = {".py", ".sh", ".R", ".Rscript", ".pl"}


class DirectExecutor:
    """直接执行器：对"直接动手"指令跳过搜索、立即执行。"""

    def __init__(self, workspace: str, context_broker=None):
        """
        Args:
            workspace: Partner 工作区路径
            context_broker: ContextBroker 实例（获取项目上下文）
        """
        self.workspace = workspace
        self.context_broker = context_broker
        self._push_callback = None

    def set_push_callback(self, callback):
        """设置结果推送回调（由 QQ bridge 注册）。"""
        self._push_callback = callback

    # ── 主入口 ────────────────────────────────────────────────

    def execute(self, action: str, context: Optional[Dict] = None) -> Dict:
        """执行"直接动手"指令。

        Args:
            action: 用户指令中的动作描述（如"修泄漏"、"跑实验"、"试试交叉特征"）
            context: 任务上下文（可包含 project_path, known_issues, line_numbers 等）

        Returns:
            {
                "success": bool,
                "summary": str,
                "output": str,
                "error": str,
                "files_affected": [str],
                "metrics_change": {str: any},
            }
        """
        logger.info(f"[DirectExec] 开始执行: action='{action}'")

        if not context:
            context = {}

        result = {
            "success": False,
            "summary": "",
            "output": "",
            "error": "",
            "files_affected": [],
            "metrics_change": {},
        }

        # 1. 确定项目路径
        project_path = context.get("project_path", "")
        if not project_path:
            # 从 context_broker 获取
            if self.context_broker:
                ctx = self.context_broker.get_context_for_search(action)
                project_path = ctx.get("project_path", "")
                if not project_path and ctx.get("files"):
                    # 尝试从文件路径推断
                    for fp in ctx["files"]:
                        if os.path.exists(fp):
                            project_path = os.path.dirname(fp)
                            break

        if not project_path:
            result["error"] = "未找到项目路径。请先告诉我项目的具体路径。"
            logger.warning("[DirectExec] 无项目路径")
            return result

        # 安全验证
        is_allowed = any(
            os.path.abspath(project_path).startswith(os.path.abspath(d))
            for d in ALLOWED_DIRS
        )
        if not is_allowed:
            result["error"] = f"项目路径不在允许的执行范围内: {project_path}"
            logger.warning(f"[DirectExec] 路径不在白名单: {project_path}")
            return result

        if not os.path.isdir(project_path):
            result["error"] = f"项目目录不存在: {project_path}"
            return result

        # 2. 根据 action 选择要运行的命令
        cmd = self._select_command(action, project_path, context)
        if not cmd:
            result["error"] = f"无法根据描述「{action}」确定要执行的命令。"
            logger.warning(f"[DirectExec] 无法确定命令: {action}")
            return result

        # 3. 执行命令
        logger.info(f"[DirectExec] 执行命令: {cmd['description']}")
        logger.info(f"[DirectExec]   工作目录: {project_path}")
        logger.info(f"[DirectExec]   命令: {cmd['cmd']}")

        result["files_affected"] = cmd.get("files_affected", [])
        result["summary"] = cmd["description"]

        try:
            output = self._run_command(
                cmd["cmd"],
                workdir=project_path,
                timeout=cmd.get("timeout", 300),
            )
            result["output"] = output
            result["success"] = True
            result["summary"] = f"{cmd['description']} ✅ 已完成"

            # 尝试从输出中提取指标变化
            metrics = self._extract_metrics_from_output(output)
            if metrics:
                result["metrics_change"] = metrics
                result["summary"] += f"\n{metrics.get('summary', '')}"

            logger.info(f"[DirectExec] 执行成功: {result['summary'][:100]}")

        except subprocess.TimeoutExpired:
            result["error"] = f"执行超时（>{cmd.get('timeout', 300)}秒）"
            logger.warning(f"[DirectExec] 超时: {cmd['description']}")
        except subprocess.CalledProcessError as e:
            result["error"] = f"执行失败 (exit={e.returncode}): {e.output[:500] if e.output else ''}"
            logger.warning(f"[DirectExec] 失败 (exit={e.returncode})")
        except Exception as e:
            result["error"] = f"执行异常: {e}"
            logger.error(f"[DirectExec] 异常: {e}", exc_info=True)

        # 4. 推送结果
        self._push_result(result, context)

        return result

    # ── 命令选择 ──────────────────────────────────────────────

    def _select_command(
        self, action: str, project_path: str, context: Dict
    ) -> Optional[Dict]:
        """根据用户指令和项目路径选择要执行的命令。

        Args:
            action: 用户指令
            project_path: 项目路径
            context: 上下文（行号、脚本名等）

        Returns:
            {"cmd": [...], "description": str, "timeout": int, "files_affected": [str]}
            或 None 无法确定命令
        """
        action_lower = action.lower()

        # 获取项目中的 .py 和 .sh 文件
        scripts = self._find_executables(project_path)
        context_scripts = context.get("scripts", [])
        line_numbers = context.get("line_numbers", [])
        known_issues = context.get("known_issues", []) or context.get("issues", [])

        # 匹配规则（按优先级）：
        # 1. 用户提到了具体脚本名
        for cs in context_scripts:
            if cs in action_lower:
                found = next(
                    (s for s in scripts if s["name"] == cs or s["name"].endswith(cs)),
                    None,
                )
                if found:
                    return {
                        "cmd": [sys.executable, found["path"]],
                        "description": f"运行 {found['name']}",
                        "timeout": 600,
                        "files_affected": [found["path"]],
                    }

        # 2. 用户提到修泄漏/leak
        if any(kw in action_lower for kw in ["泄漏", "leak", "漏"]):
            # 查找可能修复泄漏的脚本
            leak_fixers = [
                s for s in scripts
                if any(kw in s["name"].lower() for kw in ["leak", "fix", "correct", "correct", "batch"])
            ]
            if leak_fixers:
                f = leak_fixers[0]
                return {
                    "cmd": [sys.executable, f["path"]],
                    "description": f"修复泄漏: 运行 {f['name']}",
                    "timeout": 600,
                    "files_affected": [f["path"]],
                }

        # 3. 用户提到跑实验/试/实验
        if any(kw in action_lower for kw in ["跑", "试", "实验", "experiment", "train"]):
            # 找 main/train/run 脚本
            runners = [
                s
                for s in scripts
                if any(kw in s["name"].lower() for kw in ["main", "train", "run", "experiment"])
            ]
            if runners:
                f = runners[0]
                return {
                    "cmd": [sys.executable, f["path"]],
                    "description": f"运行实验: {f['name']}",
                    "timeout": 900,
                    "files_affected": [f["path"]],
                }

        # 4. 用户提到交叉特征/feature
        if any(kw in action_lower for kw in ["交叉特征", "特征工程", "feature"]):
            feat_scripts = [
                s
                for s in scripts
                if any(kw in s["name"].lower() for kw in ["feature", "feat", "cross"])
            ]
            if feat_scripts:
                f = feat_scripts[0]
                return {
                    "cmd": [sys.executable, f["path"]],
                    "description": f"执行特征工程: {f['name']}",
                    "timeout": 600,
                    "files_affected": [f["path"]],
                }

        # 5. 有行号：尝试以 line 为参数运行脚本
        if line_numbers:
            candidates = [s for s in scripts if s["name"].endswith(".py")]
            if candidates:
                # 取最近的主要脚本
                target = candidates[0]
                line_args = ",".join(str(n) for n in line_numbers[:5])
                return {
                    "cmd": [sys.executable, target["path"], "--fix-lines", line_args],
                    "description": f"修复 {target['name']} 的第 {line_args} 行",
                    "timeout": 600,
                    "files_affected": [target["path"]],
                }

        # 6. 泛化：取第一个 .py 脚本
        py_scripts = [s for s in scripts if s["name"].endswith(".py")]
        if py_scripts:
            f = py_scripts[0]
            return {
                "cmd": [sys.executable, f["path"]],
                "description": f"执行项目脚本: {f['name']}",
                "timeout": 600,
                "files_affected": [f["path"]],
            }

        return None

    # ── 辅助方法 ──────────────────────────────────────────────

    def _find_executables(self, project_path: str) -> List[Dict]:
        """查找项目中的可执行脚本。"""
        results = []
        try:
            for fname in os.listdir(project_path):
                fpath = os.path.join(project_path, fname)
                if os.path.isfile(fpath):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in ALLOWED_EXTENSIONS:
                        results.append({"name": fname, "path": fpath})
        except PermissionError:
            pass
        results.sort(key=lambda x: x["name"])
        return results

    def _run_command(
        self, cmd: List[str], workdir: str, timeout: int = 300
    ) -> str:
        """运行命令并捕获输出。"""
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result.check_returncode()
        output = result.stdout
        if result.stderr:
            output += "\n[STDERR]\n" + result.stderr
        return output[:5000]  # 限制输出长度

    def _extract_metrics_from_output(self, output: str) -> Dict:
        """从命令输出中提取关键指标变化。"""
        metrics = {"summary": ""}
        changes = []

        # 常见指标模式
        patterns = [
            (r"MAE[=：:]\s*([\d.]+)", "MAE"),
            (r"accuracy[=：:]\s*([\d.]+)", "accuracy"),
            (r"loss[=：:]\s*([\d.]+)", "loss"),
            (r"R2[=：:]\s*([\d.]+)", "R2"),
            (r"RMSE[=：:]\s*([\d.]+)", "RMSE"),
            (r"F1[=：:]\s*([\d.]+)", "F1"),
            (r"AUC[=：:]\s*([\d.]+)", "AUC"),
        ]
        for pat, name in patterns:
            m = re.search(pat, output, re.IGNORECASE)
            if m:
                metrics[name] = m.group(1)
                changes.append(f"{name}={m.group(1)}")

        # 检查关键事件
        if re.search(r"(improve|提升|提高|better|减少|下降|decrease)", output, re.IGNORECASE):
            changes.append("(有改进)")

        if re.search(r"(error|traceback|exception|failed)", output, re.IGNORECASE):
            changes.append("(有错误)")

        if changes:
            metrics["summary"] = " | ".join(changes)

        return metrics

    def _push_result(self, result: Dict, context: Dict):
        """推送执行结果。"""
        if not self._push_callback:
            return

        try:
            if result["success"]:
                parts = [f"✅ {result['summary'][:200]}"]
                if result.get("metrics_change", {}).get("summary"):
                    parts.append(
                        f"📊 {result['metrics_change']['summary']}"
                    )
                if result["files_affected"]:
                    parts.append(
                        f"📄 文件: {', '.join(os.path.basename(f) for f in result['files_affected'][:3])}"
                    )
                message = "\n".join(parts)
            else:
                message = f"❌ 执行失败: {result.get('error', '未知错误')[:200]}"

            self._push_callback(message)

        except Exception as e:
            logger.warning(f"[DirectExec] 推送结果失败: {e}")
