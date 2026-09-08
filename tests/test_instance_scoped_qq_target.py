import json

from partner.evolution.sprint18_unified_patch import (
    lock_openid_for_workspace,
    restore_instance_openid,
)


def _append_inbound(workspace, openid, *, channel="c2c", group_id=""):
    path = workspace / "state" / "qq_chat_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "role": "user",
        "source": "qq",
        "channel": channel,
        "sender_id": openid,
        "sender_name": "tester",
        "group_id": group_id,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_each_instance_recovers_only_its_own_app_scoped_openid(tmp_path):
    one = tmp_path / "instances" / "01"
    three = tmp_path / "instances" / "03"
    _append_inbound(one, "openid-for-app-01")
    _append_inbound(three, "openid-for-app-03")

    assert restore_instance_openid(one) == [one / "state" / "qq_user_context.json"]
    assert restore_instance_openid(three) == [three / "state" / "qq_user_context.json"]
    ctx_one = json.loads((one / "state" / "qq_user_context.json").read_text())
    ctx_three = json.loads((three / "state" / "qq_user_context.json").read_text())
    assert ctx_one["openid"] == "openid-for-app-01"
    assert ctx_three["openid"] == "openid-for-app-03"


def test_legacy_lock_argument_cannot_overwrite_instance_identity(tmp_path):
    workspace = tmp_path / "instances" / "02"
    _append_inbound(workspace, "openid-for-app-02")

    lock_openid_for_workspace(workspace, "foreign-openid-from-app-03")

    context = json.loads((workspace / "state" / "qq_user_context.json").read_text())
    assert context["openid"] == "openid-for-app-02"


def test_group_sender_is_not_reused_for_private_proactive_delivery(tmp_path):
    workspace = tmp_path / "instances" / "04"
    _append_inbound(workspace, "private-openid", channel="c2c")
    _append_inbound(workspace, "group-member-openid", channel="group", group_id="group-1")

    restore_instance_openid(workspace)

    context = json.loads((workspace / "state" / "qq_user_context.json").read_text())
    assert context["openid"] == "private-openid"


def test_no_history_never_invents_or_overwrites_an_openid(tmp_path):
    workspace = tmp_path / "instances" / "05"
    context_path = workspace / "state" / "qq_user_context.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(json.dumps({"openid": "existing"}))

    assert restore_instance_openid(workspace) == []
    assert json.loads(context_path.read_text())["openid"] == "existing"
