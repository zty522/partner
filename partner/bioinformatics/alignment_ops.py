"""比对操作模块 — MUSCLE, MAFFT, BLAST。

所有工具均为免费开源，通过 conda install 安装。
当前系统已安装：MUSCLE, MAFFT, BLAST (blastp/blastn)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

from .bio_adapter import BioResult

logger = logging.getLogger(__name__)


class AlignmentOps:
    """多序列比对和序列搜索封装。"""

    def execute(self, task: str = "", **kwargs: Any) -> BioResult:
        op = kwargs.get("op") or _infer_op_from_task(task)
        if not op:
            return BioResult(ok=False, status="error", error="未指定操作。可用: muscle, mafft, blastp, blastn, makeblastdb")

        handlers = {
            "muscle": self._muscle_align,
            "mafft": self._mafft_align,
            "blastp": self._blastp,
            "blastn": self._blastn,
            "makeblastdb": self._makeblastdb,
        }
        handler = handlers.get(op)
        if not handler:
            return BioResult(ok=False, status="error", error=f"未知操作: {op}")
        return handler(**kwargs)

    def _muscle_align(self, **kwargs: Any) -> BioResult:
        """MUSCLE 多序列比对。"""
        input_path = kwargs.get("input") or kwargs.get("fasta") or ""
        return self._run_aligner("muscle", input_path, extra_args=["-maxiters", kwargs.get("maxiters", "2")], **kwargs)

    def _mafft_align(self, **kwargs: Any) -> BioResult:
        """MAFFT 多序列比对。"""
        input_path = kwargs.get("input") or kwargs.get("fasta") or ""
        opts = kwargs.get("opts", "--auto")
        if not isinstance(opts, list):
            opts = opts.split()
        return self._run_aligner("mafft", input_path, extra_args=opts, **kwargs)

    def _run_aligner(self, cmd: str, input_path: str, extra_args: list | None = None, **kwargs: Any) -> BioResult:
        if not shutil.which(cmd):
            return BioResult(ok=False, status="error", error=f"'{cmd}' 未安装。安装: conda install -c bioconda {cmd}")

        # 如果传的是序列文本而非文件路径，写到临时文件
        seq_data = kwargs.get("sequences") or kwargs.get("data") or ""
        temp_dir = None
        try:
            if not input_path and seq_data:
                temp_dir = tempfile.mkdtemp()
                input_path = os.path.join(temp_dir, "input.fasta")
                with open(input_path, "w") as f:
                    f.write(seq_data)

            if not input_path or not os.path.isfile(input_path):
                return BioResult(ok=False, status="error", error=f"输入文件不存在: {input_path}。可直接传 sequences 参数（FASTA 文本）")

            output = kwargs.get("output") or ""
            if not output:
                output = os.path.join(temp_dir or os.path.dirname(input_path), "aligned.fasta")

            cmd_args = [cmd]
            if extra_args:
                cmd_args.extend(extra_args)
            cmd_args.extend([input_path])

            # MAFFT 输出到 stdout
            if cmd == "mafft":
                r = subprocess.run(cmd_args, capture_output=True, text=True, timeout=kwargs.get("timeout", 300))
                if r.returncode != 0:
                    return BioResult(ok=False, status="error", error=f"MAFFT 失败: {r.stderr[:500]}")
                if output:
                    with open(output, "w") as f:
                        f.write(r.stdout)
                return BioResult(ok=True, status="success", text=f"📋 MAFFT 比对完成\n  输出: {output}\n  大小: {len(r.stdout)} bytes\n  (前500字) {r.stdout[:500]}", data={"aligned": r.stdout, "output": output, "method": "mafft"}, method="cli_tool")

            # MUSCLE 输出到文件
            cmd_args.extend(["-output", output])
            r = subprocess.run(cmd_args, capture_output=True, text=True, timeout=kwargs.get("timeout", 300))
            if r.returncode != 0:
                return BioResult(ok=False, status="error", error=f"MUSCLE 失败: {r.stderr[:500]}")
            result_text = r.stdout or ""
            with open(output) as f:
                result_text = f.read()
            return BioResult(ok=True, status="success", text=f"📋 MUSCLE 比对完成\n  输出: {output}\n  大小: {len(result_text)} bytes\n  (前500字) {result_text[:500]}", data={"aligned": result_text, "output": output, "method": "muscle"}, method="cli_tool")

        except subprocess.TimeoutExpired:
            return BioResult(ok=False, status="error", error=f"{cmd} 超时 (>300s)")
        except Exception as exc:
            return BioResult(ok=False, status="error", error=f"{cmd} 失败: {exc}")
        finally:
            if temp_dir:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _blastp(self, **kwargs: Any) -> BioResult:
        """BLASTP — 蛋白质序列搜索。"""
        return self._run_blast("blastp", **kwargs)

    def _blastn(self, **kwargs: Any) -> BioResult:
        """BLASTN — 核酸序列搜索。"""
        return self._run_blast("blastn", **kwargs)

    def _run_blast(self, program: str, **kwargs: Any) -> BioResult:
        if not shutil.which(program):
            return BioResult(ok=False, status="error", error=f"'{program}' 未安装。安装: conda install -c bioconda blast")

        query = kwargs.get("query") or kwargs.get("input") or ""
        db = kwargs.get("db") or ""
        if not query or not db:
            return BioResult(ok=False, status="error", error=f"需要 query (序列/文件) 和 db (BLAST 数据库路径)")

        # query 可以是序列文本或文件路径
        temp_dir = None
        query_path = query
        if not os.path.isfile(query):
            temp_dir = tempfile.mkdtemp()
            query_path = os.path.join(temp_dir, "query.fasta")
            with open(query_path, "w") as f:
                f.write(f">query\n{query}\n" if not query.startswith(">") else query)

        outfmt = kwargs.get("outfmt", 6)  # 默认表格输出
        output = kwargs.get("output") or ""
        if not output and temp_dir:
            output = os.path.join(temp_dir, "blast_result.txt")

        try:
            cmd = [program, "-query", query_path, "-db", db, "-outfmt", str(outfmt)]
            if kwargs.get("evalue"):
                cmd.extend(["-evalue", str(kwargs["evalue"])])
            if kwargs.get("max_target_seqs"):
                cmd.extend(["-max_target_seqs", str(kwargs["max_target_seqs"])])
            if kwargs.get("num_threads"):
                cmd.extend(["-num_threads", str(kwargs["num_threads"])])

            r = subprocess.run(cmd, capture_output=True, text=True, timeout=kwargs.get("timeout", 300))
            if r.returncode != 0:
                return BioResult(ok=False, status="error", error=f"{program} 失败: {r.stderr[:500]}")

            result_text = r.stdout[:5000]
            lines = result_text.strip().split("\n")
            return BioResult(ok=True, status="success", text=f"🔍 {program} 搜索结果\n  数据库: {db}\n  找到 {len(lines)} 条命中\n  (前500字) {result_text[:500]}", data={"hits": result_text, "db": db, "count": len(lines)}, method="cli_tool")

        except subprocess.TimeoutExpired:
            return BioResult(ok=False, status="error", error=f"{program} 超时 (>300s)")
        except Exception as exc:
            return BioResult(ok=False, status="error", error=f"{program} 失败: {exc}")
        finally:
            if temp_dir:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _makeblastdb(self, **kwargs: Any) -> BioResult:
        """构建 BLAST 数据库。"""
        if not shutil.which("makeblastdb"):
            return BioResult(ok=False, status="error", error="makeblastdb 未安装")
        fasta = kwargs.get("input") or kwargs.get("fasta") or ""
        dbtype = kwargs.get("dbtype", "prot")  # prot 或 nucl
        title = kwargs.get("title", "myblastdb")
        if not fasta or not os.path.isfile(fasta):
            return BioResult(ok=False, status="error", error=f"FASTA 文件不存在: {fasta}")
        output = kwargs.get("output") or fasta.replace(".fasta", "").replace(".fa", "")
        try:
            r = subprocess.run(
                ["makeblastdb", "-in", fasta, "-dbtype", dbtype, "-title", title, "-out", output],
                capture_output=True, text=True, timeout=120,
            )
            return BioResult(ok=True if r.returncode == 0 else False,
                           status="success" if r.returncode == 0 else "error",
                           text=f"🗄️ BLAST 数据库已创建\n  输入: {fasta}\n  输出: {output}\n  {r.stdout[:500]}" if r.returncode == 0 else f"makeblastdb 失败: {r.stderr[:500]}",
                           data={"db_path": output}, method="cli_tool")
        except Exception as exc:
            return BioResult(ok=False, status="error", error=f"makeblastdb 失败: {exc}")

    def run_blast(self, task: str = "", **kwargs: Any) -> BioResult:
        """外部调用的 BLAST 入口（从 BioAdapter 调用）。"""
        program = kwargs.get("program", "blastp")
        if program == "blastp":
            return self._blastp(**kwargs)
        elif program == "blastn":
            return self._blastn(**kwargs)
        return self.execute(task=task, **kwargs)


def _infer_op_from_task(task: str) -> str:
    tl = task.lower()
    if any(k in tl for k in ("muscle", "多序列比对")):
        return "muscle"
    if any(k in tl for k in ("mafft", "快速比对")):
        return "mafft"
    if any(k in tl for k in ("blastp", "蛋白质搜索", "蛋白搜索")):
        return "blastp"
    if any(k in tl for k in ("blastn", "核酸搜索", "核苷酸搜索")):
        return "blastn"
    if any(k in tl for k in ("makeblastdb", "建库", "构建数据库")):
        return "makeblastdb"
    return ""
