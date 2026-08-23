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

    def test_batch_plan_uses_deepseek_chat(self):
        m, _ = select_model_and_tokens(CFG, "batch_plan")
        assert m == "deepseek-chat"

    def test_action_uses_deepseek_chat(self):
        m, _ = select_model_and_tokens(CFG, "action")
        assert m == "deepseek-chat"

    def test_report_uses_deepseek_chat(self):
        m, _ = select_model_and_tokens(CFG, "report")
        assert m == "deepseek-chat"

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
