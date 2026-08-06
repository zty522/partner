"""BioNeMo 核心适配器 — 封装 NVIDIA BioNeMo Service SDK/HTTP API。

支持三种运行模式：
  1. bionemo SDK (pip install bionemo) — 原生 Python SDK
  2. NIM 直连 (Direct REST API) — 绕过 SDK 直接调用 NIM 端点
  3. 本地 NIM — 指向本地运行的 NIM 容器

API Key 读取优先级：
  1. 构造参数 `api_key`
  2. 环境变量 `NGC_API_KEY`
  3. 环境变量 `NVCF_API_KEY`
  4. Hermes 配置中的 deepseek/openai key（降级用作占位）

Health Check 检测可用性：
  - SDK 可用 → 调用 list_models() 验证 key
  - NIM 直连 → 调用 health/ready 端点
  - 均不可用 → 返回不可用状态
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────
DEFAULT_API_HOST = "https://api.bionemo.ngc.nvidia.com/v1"
DEFAULT_NIM_BASE = "http://localhost:8000"
DEFAULT_TIMEOUT = 600  # 10 min，diffdock/openfold 可能需要
MIN_TIMEOUT = 60

# 可用模型列表
MODEL_MAP = {
    # 分子
    "molmim": {
        "type": "molecule",
        "sdk_method": "molmim_unguided_generate_sync",
        "nim_endpoint": "/molecular-generation/molmim/generate",
        "description": "MolMIM 分子生成",
    },
    "diffdock": {
        "type": "molecule",
        "sdk_method": "diffdock_sync",
        "nim_endpoint": "/molecular-docking/diffdock/predict",
        "description": "DiffDock 分子对接",
    },
    # 蛋白质
    "esmfold": {
        "type": "protein",
        "sdk_method": "esmfold_sync",
        "nim_endpoint": "/protein-structure/esmfold/predict-no-aln",
        "description": "ESMFold 蛋白质结构预测",
    },
    "openfold": {
        "type": "protein",
        "sdk_method": "openfold_sync",
        "nim_endpoint": "/protein-structure/openfold/predict",
        "description": "OpenFold 蛋白质折叠",
    },
    "esm2": {
        "type": "protein",
        "sdk_method": "esm2_sync",
        "nim_endpoint": "/protein-embedding/esm2-650m/embeddings",
        "description": "ESM-2 蛋白质嵌入",
    },
    # 序列
    "esm1nv": {
        "type": "sequence",
        "sdk_method": "esm1nv_sync",
        "nim_endpoint": "/protein-embedding/esm1nv/embeddings",
        "description": "ESM-1nv 序列嵌入",
    },
}

# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class BioNeMoResult:
    """标准化的 BioNeMo 调用结果。"""

    ok: bool = False
    status: str = "error"  # success | error | timeout | auth_error | no_sdk
    data: Any = None
    error: str = ""
    model_used: str = ""
    duration_s: float = 0.0
    metadata: dict = field(default_factory=dict)


# ── 适配器 ────────────────────────────────────────────────


class BioNeMoAdapter:
    """BioNeMo 适配器核心类。

    同时支持 bionemo SDK 和直接 HTTP 调用 NIM 端点。
    自动检测可用路径，优先 SDK。
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_host: str | None = None,
        nim_base: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key or os.environ.get("NGC_API_KEY") or os.environ.get("NVCF_API_KEY") or ""
        self.api_host = api_host or os.environ.get("BIONEMO_API_HOST") or DEFAULT_API_HOST
        self.nim_base = nim_base or os.environ.get("BIONEMO_NIM_BASE") or ""
        self.timeout = max(MIN_TIMEOUT, timeout)

        # 缓存检测结果
        self._sdk_available: bool | None = None
        self._nim_available: bool | None = None
        self._client: Any = None  # bionemo.api.BionemoClient 实例

    # ── 可用性检测 ──────────────────────────────────────────

    def is_available(self) -> bool:
        """检查至少一种访问模式可用。"""
        sdk = self._check_sdk()
        nim = self._check_nim()
        return sdk or nim

    def check_health(self) -> dict:
        """全面健康检查。

        Returns:
            {"available": bool, "sdk": bool, "nim": bool,
             "models": [str], "error": str}
        """
        sdk_ok = self._check_sdk()
        nim_ok = self._check_nim()
        models_available = list(MODEL_MAP.keys()) if (sdk_ok or nim_ok) else []

        return {
            "available": sdk_ok or nim_ok,
            "sdk": sdk_ok,
            "nim": nim_ok,
            "nim_base": self.nim_base,
            "api_configured": bool(self.api_key),
            "models": models_available,
            "error": "",
        }

    def _check_sdk(self) -> bool:
        if self._sdk_available is not None:
            return self._sdk_available
        try:
            from bionemo.api import BionemoClient

            # 快速验证：有 key 时尝试 list_models()
            if self.api_key:
                c = BionemoClient(api_key=self.api_key, api_host=self.api_host, timeout_secs=30)
                c.list_models()
                self._client = c
                self._sdk_available = True
                logger.info("[BioNeMoAdapter] SDK 可用, API key 已验证")
            else:
                self._sdk_available = True  # SDK 本身安装了但可能无 key
                logger.info("[BioNeMoAdapter] SDK 已安装 (无 API key)")
        except ImportError:
            self._sdk_available = False
            logger.debug("[BioNeMoAdapter] bionemo SDK 未安装")
        except Exception as exc:
            self._sdk_available = False
            logger.warning("[BioNeMoAdapter] SDK 检测失败: %s", exc)
        return self._sdk_available

    def _check_nim(self) -> bool:
        if self._nim_available is not None:
            return self._nim_available
        if not self.nim_base:
            self._nim_available = False
            return False
        try:
            import urllib.request

            url = f"{self.nim_base.rstrip('/')}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                self._nim_available = resp.status == 200
        except Exception:
            self._nim_available = False
        return self._nim_available

    # ── 核心调用 ────────────────────────────────────────────

    def call_model(
        self,
        model_name: str,
        **kwargs: Any,
    ) -> BioNeMoResult:
        """调用 BioNeMo 模型。

        Args:
            model_name: MODEL_MAP 中的 key (molmim, diffdock, esmfold, ...)
            **kwargs: 模型特定参数 (传递给 SDK 或 REST API)

        Returns:
            BioNeMoResult 标准化结果
        """
        start = time.time()
        model_info = MODEL_MAP.get(model_name)
        if not model_info:
            return BioNeMoResult(
                ok=False,
                status="error",
                error=f"未知模型: {model_name}，可用: {list(MODEL_MAP.keys())}",
                duration_s=time.time() - start,
            )

        result = BioNeMoResult(model_used=model_name)

        # 优先级 1: SDK
        if self._check_sdk():
            try:
                data = self._call_via_sdk(model_name, model_info, **kwargs)
                result.ok = True
                result.status = "success"
                result.data = data
                result.metadata["method"] = "sdk"
            except Exception as exc:
                err_str = str(exc)
                if "401" in err_str or "403" in err_str or "Unauthorized" in err_str or "Authorization" in err_str:
                    result.status = "auth_error"
                    result.error = f"API key 认证失败: {err_str[:200]}"
                else:
                    # SDK 失败，仅在配置了 NIM 时降级
                    if self.nim_base:
                        logger.warning("[BioNeMoAdapter] SDK 调用 %s 失败, 尝试 NIM: %s", model_name, err_str[:100])
                        result = self._call_via_nim(model_name, model_info, **kwargs)
                    else:
                        result.status = "no_sdk"
                        result.error = (
                            f"BioNeMo SDK 调用失败 (网络/连接错误)。\n"
                            f"请检查 NGC_API_KEY 是否正确设置，或网络是否可达。\n"
                            f"错误: {err_str[:200]}\n"
                            f"提示: pip install bionemo 可安装 SDK; "
                            f"export BIONEMO_NIM_BASE=http://localhost:8000 可配置本地 NIM"
                        )

        # 优先级 2: NIM 直连 (仅在配置了 nim_base 时)
        elif self.nim_base and self._check_nim():
            result = self._call_via_nim(model_name, model_info, **kwargs)

        # 均不可用
        else:
            result.status = "no_sdk"
            result.error = (
                f"BioNeMo 不可用: SDK 未安装且 NIM 未配置。"
                f"安装: pip install bionemo\n"
                f"配置 NIM: BIONEMO_NIM_BASE=http://localhost:8000\n"
                f"设置 API key: export NGC_API_KEY=xxx"
            )

        result.duration_s = round(time.time() - start, 2)
        return result

    def _call_via_sdk(self, model_name: str, model_info: dict, **kwargs) -> Any:
        """通过 bionemo Python SDK 调用模型。"""
        from bionemo.api import BionemoClient

        c = self._client or BionemoClient(
            api_key=self.api_key or "dummy",
            api_host=self.api_host,
            timeout_secs=self.timeout,
        )

        method_name = model_info["sdk_method"]
        method = getattr(c, method_name, None)
        if not method:
            raise ValueError(f"SDK 方法 {method_name} 不存在")

        # 类型转换和参数适配
        adapted = self._adapt_sdk_params(model_name, kwargs)
        return method(**adapted)

    def _adapt_sdk_params(self, model_name: str, kwargs: dict) -> dict:
        """将通用参数名适配为 SDK 参数名。"""
        mapping = {
            "molmim": {
                "smiles": "smi",
                "num_samples": "num_samples",
                "scaled_radius": "scaled_radius",
            },
            "diffdock": {
                "ligand": "ligand_file",
                "protein": "protein_file",
                "poses": "poses_to_generate",
                "diffusion_steps": "diffusion_steps",
            },
            "esmfold": {
                "sequence": "protein_sequence",
            },
            "openfold": {
                "sequence": "protein_sequence",
                "msas": "msas",
                "use_msa": "use_msa",
                "relax": "relax_prediction",
            },
            "esm2": {
                "sequences": "sequences",
                "model_size": "model",
            },
            "esm1nv": {
                "sequences": "sequences",
            },
        }
        param_map = mapping.get(model_name, {})
        adapted = {}
        for k, v in kwargs.items():
            mapped = param_map.get(k, k)
            adapted[mapped] = v
        return adapted

    def _call_via_nim(self, model_name: str, model_info: dict, **kwargs) -> BioNeMoResult:
        """通过直接 REST API 调用 NIM 端点。"""
        import urllib.request as request_lib

        base = self.nim_base.rstrip("/")
        endpoint = model_info.get("nim_endpoint", "")
        if not endpoint:
            return BioNeMoResult(ok=False, status="error", error=f"模型 {model_name} 无 NIM 端点")

        url = f"{base}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = self._build_nim_payload(model_name, kwargs)
        body = json.dumps(payload).encode("utf-8")

        try:
            req = request_lib.Request(url, data=body, headers=headers, method="POST")
            with request_lib.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            return BioNeMoResult(ok=True, status="success", data=data, metadata={"method": "nim"})
        except request_lib.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
            return BioNeMoResult(
                ok=False,
                status="error",
                error=f"NIM HTTP {exc.code}: {err_body}",
                metadata={"method": "nim"},
            )
        except Exception as exc:
            return BioNeMoResult(
                ok=False,
                status="error",
                error=f"NIM 调用失败: {exc}",
                metadata={"method": "nim"},
            )

    def _build_nim_payload(self, model_name: str, kwargs: dict) -> dict:
        """构建 NIM REST API 请求体。"""
        payloads = {
            "molmim": {
                "smi": kwargs.get("smiles", ""),
                "num_samples": kwargs.get("num_samples", 20),
                "scaled_radius": kwargs.get("scaled_radius", 1.0),
            },
            "diffdock": {
                "ligand_sdf": kwargs.get("ligand", ""),
                "protein_pdb": kwargs.get("protein", ""),
                "poses_to_generate": kwargs.get("poses", 20),
                "diffusion_steps": kwargs.get("diffusion_steps", 18),
            },
            "esmfold": {
                "sequence": kwargs.get("sequence", ""),
            },
            "openfold": {
                "sequence": kwargs.get("sequence", ""),
                "use_msa": kwargs.get("use_msa", True),
                "relax_prediction": kwargs.get("relax", True),
            },
            "esm2": {
                "sequence": kwargs.get("sequences", []),
                "model": kwargs.get("model_size", "650m"),
            },
            "esm1nv": {
                "sequence": kwargs.get("sequences", []),
            },
        }
        return payloads.get(model_name, kwargs)


# ── 便捷创建函数 ──────────────────────────────────────────


def create_bionemo_adapter(
    api_key: str | None = None,
    nim_base: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> BioNeMoAdapter:
    """创建并返回 BioNeMoAdapter 实例。"""
    return BioNeMoAdapter(api_key=api_key, nim_base=nim_base, timeout=timeout)
