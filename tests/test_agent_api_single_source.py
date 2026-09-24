from __future__ import annotations

import json

from partner.adapters.agent_config_sync import desired_hermes_model_config
from partner.commitment.proposer import resolve_provider_config


def write_api(root):
    config = root / "config"
    config.mkdir(parents=True)
    (config / "api.json").write_text(json.dumps({
        "default_provider": "qwen",
        "apis": {
            "deepseek": {"base_url": "https://deepseek.invalid/v1",
                         "api_key": "deep-key", "model": "deep-model"},
            "qwen": {"base_url": "https://qwen.invalid/v1",
                     "api_key": "qwen-key", "model": "qwen-model"},
        },
    }), encoding="utf-8")


def test_api_json_is_single_source_for_hermes_dispatch(tmp_path):
    write_api(tmp_path)
    resolved = desired_hermes_model_config(str(tmp_path))
    assert resolved == {"provider": "qwen", "model": "qwen-model",
                        "base_url": "https://qwen.invalid/v1", "api_key": "qwen-key"}


def test_commitment_proposer_respects_default_provider(tmp_path):
    write_api(tmp_path)
    resolved = resolve_provider_config(tmp_path)
    assert resolved.provider == "qwen"
    assert resolved.model == "qwen-model"
    assert resolved.base_url == "https://qwen.invalid/v1"
