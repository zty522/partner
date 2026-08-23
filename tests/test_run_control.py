from partner.monitoring.run_control import is_instance_paused, load_control, set_paused


def test_persistent_pause_and_resume(tmp_path):
    root = str(tmp_path / "workspace")
    instance_workspace = str(tmp_path / "workspace" / "instances" / "03")
    set_paused(root, ["03", "04"], True)
    assert is_instance_paused(instance_workspace, "03") is True
    assert load_control(root)["paused_instances"] == ["03", "04"]
    set_paused(root, ["03"], False)
    assert is_instance_paused(instance_workspace, "03") is False
    assert is_instance_paused(str(tmp_path / "workspace" / "instances" / "04"), "04") is True
