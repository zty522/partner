import json
from pathlib import Path
from types import SimpleNamespace

import partner.v2.multimodal_browser_events as module


def _ctx(tmp_path: Path):
    task = SimpleNamespace(working_dir=str(tmp_path / "task"))
    return SimpleNamespace(workspace=str(tmp_path), task_instance=task)


def _worker(_ctx, action, params, **_kwargs):
    if action == "open":
        return {"status": "ok", "url": params["url"]}
    if action == "execute":
        return {"status": "ok", "result": {
            "url": "https://cloud.tencent.com/developer/article/1",
            "title": "真实社区页面", "body": "公开技术文章正文",
            "links": [{"index": 0, "text": "深入阅读", "href": "https://cloud.tencent.com/developer/article/2"}],
            "controls": [],
        }}
    if action == "screenshot":
        path = Path(params["save_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png fixture")
        return {"status": "ok", "path": str(path)}
    raise AssertionError(action)


def test_multimodal_observe_requires_allowlisted_https(tmp_path):
    result = module.atomic_multimodal_browser_observe(
        _ctx(tmp_path), {"platform": "tencent_cloud", "url": "https://example.com"})
    assert result["ok"] is False
    assert result["status"] == "url_outside_platform_allowlist"


def test_multimodal_community_read_uses_dom_vision_and_two_llm_calls(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(module, "_run_worker", _worker)
    monkeypatch.setattr(module, "read_image_with_qwen", lambda *_args, **_kwargs: {
        "ok": True, "model": "qwen3-vl-flash", "description": "截图显示公开技术文章"})
    def reason(prompt):
        calls.append(prompt)
        return {"selected_link_index": 0, "evidence_agreement": True,
                "login_state": "not_required", "safe_next_action": "read"}
    monkeypatch.setattr(module, "_json_from_llm", reason)
    result = module.atomic_multimodal_community_read(
        _ctx(tmp_path), {"platform": "tencent_cloud"})
    assert result["ok"] is True
    assert result["status"] == "article_observed"
    assert result["model_calls"] == 4
    assert len(calls) == 2
    assert Path(result["evidence_path"]).is_file()
    payload = json.loads(Path(result["evidence_path"]).read_text(encoding="utf-8"))
    assert payload["business_metrics"]["articles_opened"] == 1


def test_login_wall_stops_before_link_navigation(tmp_path, monkeypatch):
    def login_worker(ctx, action, params, **kwargs):
        result = _worker(ctx, action, params, **kwargs)
        if action == "execute":
            result["result"]["url"] = "https://mp.weixin.qq.com/"
            result["result"]["body"] = "请先登录，使用微信扫码登录"
        return result
    monkeypatch.setattr(module, "_run_worker", login_worker)
    monkeypatch.setattr(module, "read_image_with_qwen", lambda *_args, **_kwargs: {
        "ok": True, "model": "qwen3-vl-flash", "description": "可见扫码登录"})
    monkeypatch.setattr(module, "_json_from_llm", lambda _prompt: {
        "selected_link_index": 0, "login_state": "login_required"})
    result = module.atomic_multimodal_community_read(
        _ctx(tmp_path), {"platform": "wechat_official"})
    assert result["ok"] is True
    assert result["status"] == "awaiting_user_login"
    assert result["production_effective"] is False


def test_nested_llm_json_is_parsed_without_truncation(monkeypatch):
    monkeypatch.setattr("partner.adapters.direct_api.chat", lambda *_args, **_kwargs: (
        '<think>done</think>{"evidence_agreement":{"dom":true},'
        '"selected_link_index":2,"safe_next_action":"read"}'))
    result = module._json_from_llm("evidence")
    assert result["evidence_agreement"] == {"dom": True}
    assert result["selected_link_index"] == 2
    assert result["_llm_calls"] == 1


def test_login_resume_requires_deterministic_authenticated_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_run_worker", _worker)
    monkeypatch.setattr(module, "read_image_with_qwen", lambda *_args, **_kwargs: {
        "ok": True, "model": "qwen3-vl-flash", "description": "公开页面"})
    monkeypatch.setattr(module, "_json_from_llm", lambda _prompt: {
        "login_state": "authenticated", "safe_next_action": "read"})
    result = module.atomic_multimodal_login_resume(
        _ctx(tmp_path), {"platform": "tencent_cloud"})
    assert result["ok"] is False
    assert result["status"] == "login_state_inconclusive"


def test_engagement_login_copy_is_not_a_reading_wall():
    dom = {"body": "公开文章完整正文。登录后评论或点赞。", "controls": [{"text": "登录/注册"}]}
    assert module._login_state(dom) == "unknown"


def test_captcha_is_a_hard_business_failure():
    assert module._blocking_state({"title": "CAPTCHA Verification", "body": ""}) == "challenge_detected"


def test_visible_login_overlay_wins_over_background_navigation():
    dom = {"body": "创作中心 消息中心 扫码登录 手机号登录", "controls": []}
    assert module._login_state(dom) == "login_required"
