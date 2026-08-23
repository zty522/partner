"""自动图表生成事件（atomic_create_chart）。

把执行步骤的数值数据自动画成 PNG 图（Loss 曲线、柱状图、散点图），方便嵌入 PDF/汇报。
支持 data=None 时自动从 task 上一个步骤 stdout/result 抽取数值数据。
"""
from __future__ import annotations

import os
import re
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _try_parse_loss_lines(text: str) -> list:
    """从训练日志文本里抽取 loss 序列。
    支持格式：
    - "Epoch N: loss=X"
    - "Epoch N/5, Loss: X"
    - "Epoch N/5 - Loss: X"
    - "epoch=1 loss=4.96"
    - "epoch 1, loss = X.X"
    """
    out = []
    _pat = re.compile(
        r"(?:^|\s)(?:[Ee]poch|epoch)\s*[^\n]*?(?:[Ll]oss|ll)\s*[:=]\s*([\d.eE+\-]+)",
        re.MULTILINE,
    )
    for m in _pat.finditer(text):
        try:
            out.append(float(m.group(1)))
        except Exception:
            pass
    return out


def _try_parse_json_or_eval(text: str):
    """从 stdout 抽取 JSON 或 eval 结构。"""
    # 找 {...} 块
    for pat in (r"\{[^{}]*\}", r"\[[^[\]]*\]"):
        for m in re.finditer(pat, text):
            try:
                obj = json.loads(m.group(0))
                return obj
            except Exception:
                pass
    return None


def _scan_step_results(ctx, current_step: str = None) -> list:
    """扫描 task 目录所有 _step_*.result.json 找数值数据。"""
    wd = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        wd = getattr(ti, "working_dir", "") or ""
        if not wd and hasattr(ti, "project_dir"):
            wd = getattr(ti, "project_dir", "") or ""
    if not wd:
        wd = getattr(ctx, "working_dir", "") or ""
    if not wd and isinstance(ctx, dict):
        wd = ctx.get("working_dir", "")
    # 兜底：用 __file__ 推 task_dir（chart_events.py 在 .../partner/v2/）
    if not wd or not os.path.isdir(wd):
        wd = os.path.dirname(__file__)  # 无意义，只是触发下面 isdir 失败
        wd = ""
    if not wd or not os.path.isdir(wd):
        return []
    out = []
    for f in sorted(os.listdir(wd)):
        if not (f.startswith("_step_") and f.endswith(".result.json")):
            continue
        try:
            data = json.load(open(os.path.join(wd, f), encoding="utf-8"))
        except Exception:
            continue
        result = data.get("result") or {}
        # 优先 stdout
        text = str(result.get("stdout") or "")
        losses = _try_parse_loss_lines(text)
        if losses:
            for i, l in enumerate(losses):
                out.append({"epoch": i + 1, "loss": l, "step": f})
            continue
        # JSON 数据
        obj = _try_parse_json_or_eval(text)
        if isinstance(obj, dict) and "losses" in obj:
            for i, l in enumerate(obj["losses"]):
                out.append({"epoch": i + 1, "loss": float(l), "step": f})
            continue
    return out


def atomic_create_chart(ctx, params: dict) -> dict:
    """根据 params 生成图表 PNG。

    data 为 None/空 时自动从 task 上一个步骤 stdout 抽取。
    """
    chart_type = str(params.get("type") or "line").lower().strip()
    data = params.get("data")
    # 数据兜底：LLM 可能传 string（python 字符串当成 data）
    if isinstance(data, str):
        # 字符串：尝试解析 loss 格式
        losses = _try_parse_loss_lines(data)
        if losses:
            data = [{"epoch": i + 1, "loss": float(l)} for i, l in enumerate(losses)]
        else:
            # 字符串没解析出损失，回退到 step stdout
            data = None
    if not data:
        data = _scan_step_results(ctx)
        if not data:
            return {"ok": False, "error": "no data provided and no auto-extracted from steps"}
    title = str(params.get("title") or "")
    x_label = str(params.get("x_label") or "x")
    y_label = str(params.get("y_label") or "y")
    out_path = str(params.get("output_path") or params.get("path") or "")
    if not out_path:
        base = getattr(ctx, "task_instance", None)
        wd = getattr(base, "working_dir", "") if base is not None else ""
        if not wd:
            wd = getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "") or "/tmp"
        out_path = os.path.join(wd, f"chart_{int(os.path.getmtime(__file__) if os.path.exists(__file__) else 0)}.png")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 5))
        if chart_type == "bar":
            if isinstance(data, dict):
                labels = data.get("labels") or []
                values = data.get("values") or []
            else:
                labels = [str(d.get("label", d.get("name", d.get("smiles", i)))) for i, d in enumerate(data)]
                values = [float(d.get("value", d.get("valid_count", d.get("y", 0)))) for d in data]
            ax.bar(range(len(labels)), values, color="steelblue")
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=45, ha="right")
            ax.set_ylabel(y_label)
        elif chart_type == "scatter":
            xs = [d.get("x", i) for i, d in enumerate(data)] if isinstance(data, list) else data.get("x", [])
            ys = [d.get("y", d.get("value", 0)) for d in data] if isinstance(data, list) else data.get("y", [])
            ax.scatter(xs, ys, alpha=0.6, color="coral")
            ax.set_xlabel(x_label)
            ax.set_ylabel(y_label)
        else:
            # line（默认）
            if isinstance(data, dict):
                xs = data.get("xs") or data.get("x") or list(range(len(data.get("ys") or data.get("y") or [])))
                ys = data.get("ys") or data.get("y") or []
                ax.plot(xs, ys, marker="o", label=data.get("label") or title or "value")
            else:
                # list of dict
                # 智能找 x/y 字段
                xs = []
                ys = []
                for i, d in enumerate(data):
                    x = d.get("x", d.get("epoch", d.get("step", d.get("idx", i))))
                    y = d.get("y", d.get("loss", d.get("value", d.get("v", 0))))
                    xs.append(x)
                    ys.append(y)
                if not any(ys):
                    # 没有任何数值 → 报错
                    return {"ok": False, "error": "no numeric data found in list (need y/loss/value fields)"}
                ax.plot(xs, ys, marker="o", color="steelblue", label=title or y_label)
            ax.set_xlabel(x_label)
            ax.set_ylabel(y_label)
            ax.grid(True, alpha=0.3)
            ax.legend()

        if title:
            ax.set_title(title)
        plt.tight_layout()
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        size = os.path.getsize(out_path)
        logger.info("[CREATE_CHART] wrote %s (%dB, type=%s, data_count=%d)", out_path, size, chart_type, len(data) if isinstance(data, list) else 0)
        try:
            from partner.evolution.evolution_log import log_evolution
            log_evolution("chart_created", detail={"path": out_path, "size": size, "type": chart_type, "title": title[:40], "auto_data": data is None or not params.get("data")})
        except Exception:
            pass
        return {"ok": True, "path": out_path, "size": size, "files": [out_path], "chart_type": chart_type}
    except ImportError as e:
        return {"ok": False, "error": f"matplotlib not available: {e}"}
    except Exception as e:
        logger.warning("[CREATE_CHART] failed: %s", e)
        return {"ok": False, "error": f"chart creation failed: {e}"}
