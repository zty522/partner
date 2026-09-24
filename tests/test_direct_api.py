"""direct_api 模型分流 —— 单元测试"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.adapters.direct_api import select_model_and_tokens

CFG = {"model": "deepseek-v4-flash"}


class TestModelSelection:
    def test_chat_uses_configured_model(self):
        m, _ = select_model_and_tokens(CFG, "chat")
        assert m == "deepseek-v4-flash"

    def test_classify_uses_configured_model(self):
        m, _ = select_model_and_tokens(CFG, "classify")
        assert m == "deepseek-v4-flash"

    def test_direct_reply_uses_configured_model(self):
        m, _ = select_model_and_tokens(CFG, "direct_reply")
        assert m == "deepseek-v4-flash"

    def test_batch_plan_uses_deepseek_v4_pro(self):
        m, _ = select_model_and_tokens(CFG, "batch_plan")
        assert m == "deepseek-v4-pro"

    def test_action_uses_deepseek_v4_pro(self):
        m, _ = select_model_and_tokens(CFG, "action")
        assert m == "deepseek-v4-pro"

    def test_report_uses_deepseek_v4_pro(self):
        m, _ = select_model_and_tokens(CFG, "report")
        assert m == "deepseek-v4-pro"

    def test_batch_plan_max_tokens_raised(self):
        _, mt = select_model_and_tokens(CFG, "batch_plan", max_tokens=4096)
        assert mt >= 16000

    def test_batch_plan_explicit_max_tokens_kept_if_large(self):
        _, mt = select_model_and_tokens(CFG, "batch_plan", max_tokens=32000)
        assert mt == 32000

    def test_batch_plan_model_override(self):
        cfg = {"model": "deepseek-v4-flash", "batch_plan_model": "deepseek-reasoner"}
        m, _ = select_model_and_tokens(cfg, "batch_plan")
        assert m == "deepseek-reasoner"

    def test_long_gen_model_override(self):
        cfg = {"model": "deepseek-v4-flash", "long_gen_model": "deepseek-chat-custom"}
        m, _ = select_model_and_tokens(cfg, "action")
        assert m == "deepseek-chat-custom"

    def test_chat_keeps_default_max_tokens(self):
        _, mt = select_model_and_tokens(CFG, "chat", max_tokens=4096)
        assert mt == 4096


def test_m3_figure_plan_disables_thinking_but_research_keeps_default(monkeypatch):
    from partner.adapters import direct_api
    from types import SimpleNamespace
    payloads=[]
    monkeypatch.setattr(direct_api,'_resolve_api_json',lambda *args:{'_provider':'minimax','api_key':'test-only','model':'MiniMax-M3','base_url':'https://invalid.example'})
    monkeypatch.setattr(direct_api,'_log_api_call',lambda **kwargs:None)
    def post(*args,**kwargs):
        payloads.append(kwargs['payload'])
        return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'usage':{}})
    monkeypatch.setattr(direct_api,'_post_hard_timeout',post)
    direct_api.chat('format source plan',purpose='report_visual_plan')
    assert payloads[-1]['thinking']=={'type':'disabled'}
    assert direct_api.get_last_usage()['thinking_requested']=='disabled'
    for purpose in ('report_claim_repair', 'autoevolution_tests', 'autoevolution_candidate_1', 'autoevolution_candidate_2'):
        direct_api.chat('serialize reviewed design',purpose=purpose)
        assert payloads[-1]['thinking']=={'type':'disabled'}
    direct_api.chat('independent audit',purpose='autoevolution_audit')
    assert 'thinking' not in payloads[-1]


def test_qwen_explicit_thinking_control_is_forwarded(monkeypatch):
    from partner.adapters import direct_api
    payloads = []
    monkeypatch.setattr(direct_api, "_resolve_api_json", lambda _provider="": {
        "_provider": "qwen", "api_key": "test", "model": "qwen3.8-flash",
        "base_url": "https://qwen.invalid", "enable_thinking": False,
    })

    class Response:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    def fake_post(*_args, **kwargs):
        payloads.append(kwargs["payload"])
        return Response()

    monkeypatch.setattr(direct_api, "_post_hard_timeout", fake_post)
    assert direct_api.chat("test", workspace="/tmp") == "OK"
    assert payloads[-1]["enable_thinking"] is False
    direct_api.chat('research',purpose='action')
    assert 'thinking' not in payloads[-1]
