import importlib.util
import json
from pathlib import Path


def _module():
    path = Path("scripts/run_seed_all.py").resolve()
    spec = importlib.util.spec_from_file_location("run_seed_all_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_acceptance_seed_has_five_distinct_project_messages():
    module = _module()
    assert set(module.ROLES) == {"01", "02", "03", "04", "05"}
    messages = [module._build_request(role, info)
                for role, info in module.ROLES.items()]
    assert len(set(messages)) == 5
    assert all("[instance_native=true]" in value for value in messages)
    assert all("[native_kind=project]" in value for value in messages)


def test_05_seed_rotates_work_instead_of_forcing_a_candidate_every_run():
    module = _module()
    message = module._build_request("05", module.ROLES["05"])
    assert "不得每轮强行造新方案" in message
    assert "生产代码 Candidate" not in message
