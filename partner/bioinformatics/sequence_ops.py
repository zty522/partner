"""序列操作模块 — 基于 BioPython（已安装）。

功能：
  - 序列翻译 (DNA → Protein)
  - 反向互补
  - GC 含量计算
  - FASTA/GenBank 文件解析
  - NCBI 在线查询 (Entrez)
  - 序列格式互转
"""

from __future__ import annotations

import logging
from typing import Any

from .bio_adapter import BioResult

logger = logging.getLogger(__name__)


class SequenceOps:
    """BioPython 序列操作封装。"""

    def __init__(self):
        self._bio = None

    @property
    def bio(self):
        if self._bio is None:
            from Bio import Seq, SeqIO, SeqRecord
            from Bio.SeqUtils import molecular_weight, gc_fraction
            self._Seq = Seq
            self._SeqIO = SeqIO
            self._SeqRecord = SeqRecord
            self._gc = gc_fraction
            self._mw = molecular_weight
            self._bio = True
        return self._bio

    def execute(self, task: str = "", **kwargs: Any) -> BioResult:
        op = kwargs.get("op") or _infer_op_from_task(task)
        if not op:
            return BioResult(ok=False, status="error", error="未指定操作。可用: translate, gc, reverse_complement, format_convert, ncbi_search")

        try:
            self.bio
        except ImportError:
            return BioResult(ok=False, status="error", error="BioPython 未安装。安装: pip install biopython")

        handlers = {
            "translate": self._translate,
            "gc": self._gc_content,
            "revcomp": self._reverse_complement,
            "format_convert": self._format_convert,
            "ncbi_search": self._ncbi_search,
        }
        handler = handlers.get(op)
        if not handler:
            return BioResult(ok=False, status="error", error=f"未知操作: {op}")
        return handler(**kwargs)

    def _translate(self, **kwargs: Any) -> BioResult:
        """DNA → 蛋白质翻译。"""
        seq = kwargs.get("sequence") or kwargs.get("input") or ""
        if not seq:
            return BioResult(ok=False, status="error", error="需要 DNA 序列")
        dna = self._Seq.Seq(seq.upper())
        table = kwargs.get("table", 1)  # 标准密码子表
        try:
            protein = dna.translate(table=int(table), to_stop=kwargs.get("to_stop", False))
        except Exception as exc:
            return BioResult(ok=False, status="error", error=f"翻译失败: {exc}")
        return BioResult(ok=True, status="success", text=f"🧬 翻译 (密码子表 {table})\n  DNA: {seq[:60]}...\n  Protein: {protein[:60]}...\n  长度: {len(seq)}nt → {len(protein)}aa", data={"dna": str(dna), "protein": str(protein), "table": table})

    def _gc_content(self, **kwargs: Any) -> BioResult:
        """计算 GC 含量。"""
        seq = kwargs.get("sequence") or kwargs.get("input") or ""
        if not seq:
            return BioResult(ok=False, status="error", error="需要 DNA 序列")
        s = self._Seq.Seq(seq.upper())
        if "U" in s:
            s = s.transcribe()  # RNA → DNA
        gc = float(self._gc(s))
        length = len(s)
        counts = {b: s.count(b) for b in "ATGC"}
        return BioResult(ok=True, status="success", text=f"📊 GC 含量分析\n  序列长度: {length} bp\n  GC%: {gc*100:.1f}%\n  A:{counts.get('A',0)} T:{counts.get('T',0)} G:{counts.get('G',0)} C:{counts.get('C',0)}", data={"gc": round(gc, 4), "length": length, "counts": counts})

    def _reverse_complement(self, **kwargs: Any) -> BioResult:
        """反向互补。"""
        seq = kwargs.get("sequence") or kwargs.get("input") or ""
        if not seq:
            return BioResult(ok=False, status="error", error="需要核苷酸序列")
        s = self._Seq.Seq(seq.upper())
        rc = str(s.reverse_complement())
        return BioResult(ok=True, status="success", text=f"🔄 反向互补\n  原始: {seq[:60]}...\n  互补: {rc[:60]}...", data={"original": seq, "reverse_complement": rc})

    def _format_convert(self, **kwargs: Any) -> BioResult:
        """序列格式转换。"""
        data = kwargs.get("data") or kwargs.get("input") or ""
        in_fmt = kwargs.get("in_format", "fasta")
        out_fmt = kwargs.get("out_format", "genbank")
        if not data:
            return BioResult(ok=False, status="error", error="需要序列数据")
        import io
        records = list(self._SeqIO.parse(io.StringIO(data), in_fmt))
        if not records:
            return BioResult(ok=False, status="error", error=f"无法解析 {in_fmt} 格式")
        out_io = io.StringIO()
        self._SeqIO.write(records, out_io, out_fmt)
        return BioResult(ok=True, status="success", text=f"🔄 {in_fmt} → {out_fmt}\n  转换 {len(records)} 条序列\n  输出 ({len(out_io.getvalue())} chars)", data={"converted": out_io.getvalue(), "count": len(records)})

    def _ncbi_search(self, **kwargs: Any) -> BioResult:
        """NCBI Entrez 查询（需要网络）。"""
        query = kwargs.get("query") or kwargs.get("input") or ""
        db = kwargs.get("db", "nucleotide")
        max_results = int(kwargs.get("max_results", 5))
        email = kwargs.get("email") or "anonymous@example.com"
        if not query:
            return BioResult(ok=False, status="error", error="需要查询关键词")
        try:
            from Bio import Entrez
            Entrez.email = email
            handle = Entrez.esearch(db=db, term=query, retmax=max_results)
            record = Entrez.read(handle)
            ids = record["IdList"]
            if not ids:
                return BioResult(ok=True, status="success", text="未找到匹配结果", data={"ids": []})
            return BioResult(ok=True, status="success", text=f"🔍 NCBI {db} 查询: {query}\n  找到 {record['Count']} 条结果\n  ID: {', '.join(ids[:10])}", data={"ids": ids, "total": record["Count"], "query": query, "db": db})
        except ImportError:
            return BioResult(ok=False, status="error", error="BioPython Entrez 不可用")
        except Exception as exc:
            return BioResult(ok=False, status="error", error=f"NCBI 查询失败: {exc}")


def _infer_op_from_task(task: str) -> str:
    tl = task.lower()
    if any(k in tl for k in ("翻译", "translate", "蛋白", "氨基酸")):
        return "translate"
    if any(k in tl for k in ("gc", "gc含量")):
        return "gc"
    if any(k in tl for k in ("反向互补", "revcomp", "互补")):
        return "revcomp"
    if any(k in tl for k in ("格式", "convert", "fasta", "genbank")):
        return "format_convert"
    if any(k in tl for k in ("ncbi", "搜索", "查询", "entrez")):
        return "ncbi_search"
    return ""
