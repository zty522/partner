"""Tests for UserPreferenceStore (Conversation V2 Phase 4)."""

import json
import os
import sys
import tempfile
import shutil

# Add partner to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from partner.user_prefs import UserPreferences, UserPreferenceStore


def test_create_default():
    """Test creating a new store with defaults."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        assert store.prefs.verbosity == "normal"
        assert store.prefs.language == "zh"
        assert store.prefs.notification_threshold == "medium"
        assert store.prefs.total_sessions == 0
        assert store.prefs.total_turns == 0
        assert os.path.exists(path)
        print("✅ test_create_default passed")
    finally:
        shutil.rmtree(tmp)


def test_record_topic_query():
    """Test topic recording and frequency tracking."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        store.record_topic_query("machine learning")
        store.record_topic_query("machine learning")
        store.record_topic_query("transformer")
        store.record_topic_query("machine learning", category="research")

        assert store.prefs.frequent_topics["machine learning"] == 3
        assert store.prefs.frequent_topics["transformer"] == 1
        assert store.prefs.last_topic_query == "machine learning"
        assert store.prefs.topic_categories["research"] == 1

        top = store.get_top_topics(2)
        assert top[0] == ("machine learning", 3)
        assert top[1] == ("transformer", 1)

        # Persistence: reload and check
        store2 = UserPreferenceStore(path)
        assert store2.prefs.frequent_topics["machine learning"] == 3

        print("✅ test_record_topic_query passed")
    finally:
        shutil.rmtree(tmp)


def test_infer_focus_areas():
    """Test focus area inference from query history."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        # Query a topic 3+ times → should appear in inferred focus
        for _ in range(4):
            store.record_topic_query("single cell")
        for _ in range(3):
            store.record_topic_query("foundation model")
        store.record_topic_query("one-off topic")  # only 1 time

        focus = store.infer_focus_areas()
        assert "single cell" in focus
        assert "foundation model" in focus
        assert "one-off topic" not in focus  # below threshold

        print("✅ test_infer_focus_areas passed")
    finally:
        shutil.rmtree(tmp)


def test_verbosity_settings():
    """Test verbosity get/set."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        assert store.get_verbosity() == "normal"

        store.set_verbosity("detailed")
        assert store.get_verbosity() == "detailed"
        assert store.prefs.preferred_detail_level == "detailed"

        # Invalid value ignored
        store.set_verbosity("invalid")
        assert store.get_verbosity() == "detailed"

        print("✅ test_verbosity_settings passed")
    finally:
        shutil.rmtree(tmp)


def test_infer_verbosity_from_behavior():
    """Test auto-inference of verbosity from behavior."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        # Not enough data (< 5 queries)
        store.infer_verbosity_from_behavior(2, 3)
        assert store.prefs.preferred_detail_level == ""

        # High detail request ratio → "detailed"
        store.infer_verbosity_from_behavior(5, 10)
        assert store.prefs.preferred_detail_level == "detailed"

        # Low detail request ratio → "brief"
        store.infer_verbosity_from_behavior(0, 10)
        assert store.prefs.preferred_detail_level == "brief"

        # Medium → "normal"
        store.infer_verbosity_from_behavior(2, 10)
        assert store.prefs.preferred_detail_level == "normal"

        print("✅ test_infer_verbosity_from_behavior passed")
    finally:
        shutil.rmtree(tmp)


def test_notification_threshold():
    """Test notification filtering by threshold."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        # Default "medium": high and normal pass, low blocked
        assert store.should_notify("high") is True
        assert store.should_notify("normal") is True
        assert store.should_notify("low") is False

        store.set_notification_threshold("low")
        assert store.should_notify("low") is True
        assert store.should_notify("normal") is True
        assert store.should_notify("high") is True

        store.set_notification_threshold("high")
        assert store.should_notify("high") is True
        assert store.should_notify("normal") is False
        assert store.should_notify("low") is False

        print("✅ test_notification_threshold passed")
    finally:
        shutil.rmtree(tmp)


def test_session_tracking():
    """Test session turn counting and session end."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        for _ in range(5):
            store.record_session_turn()
        assert store.prefs.total_turns == 5

        store.record_session_end()
        assert store.prefs.total_sessions == 1

        # Second session
        for _ in range(10):
            store.record_session_turn()
        store.record_session_end()
        assert store.prefs.total_sessions == 2
        assert store.prefs.avg_session_length > 0

        print("✅ test_session_tracking passed")
    finally:
        shutil.rmtree(tmp)


def test_learn_from_dialog_history():
    """Test learning from dialog turn data."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        turns = [
            {"role": "user", "content": "什么是GPT", "intent": "knowledge", "topic": "GPT"},
            {"role": "partner", "content": "GPT是..."},
            {"role": "user", "content": "详细说说", "intent": "detail", "topic": "GPT"},
            {"role": "partner", "content": "详细解释..."},
            {"role": "user", "content": "transformer呢", "intent": "knowledge", "topic": "transformer"},
            {"role": "partner", "content": "transformer是..."},
            {"role": "user", "content": "还有什么", "intent": "detail", "topic": "transformer"},
            {"role": "partner", "content": "还有..."},
            {"role": "user", "content": "再详细一点", "intent": "detail", "topic": "transformer"},
            {"role": "partner", "content": "好的..."},
            {"role": "user", "content": "总结一下", "intent": "status", "topic": ""},
        ]

        store.learn_from_dialog_history(turns)

        # Topics recorded
        assert store.prefs.frequent_topics.get("gpt", 0) >= 1
        assert store.prefs.frequent_topics.get("transformer", 0) >= 1

        # 3/5 user turns are detail requests → 60% → "detailed"
        assert store.prefs.preferred_detail_level == "detailed"

        print("✅ test_learn_from_dialog_history passed")
    finally:
        shutil.rmtree(tmp)


def test_personalization_helpers():
    """Test greeting and style hint generation."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        # Brief mode
        store.set_verbosity("brief")
        assert store.get_response_style_hint() == "用简洁的语言回答，避免冗余"

        # Detailed mode
        store.set_verbosity("detailed")
        hint = store.get_response_style_hint()
        assert "详细" in hint

        # English language
        store.prefs.language = "en"
        hint = store.get_response_style_hint()
        assert "English" in hint

        # Greeting
        greeting = store.get_personalized_greeting()
        assert isinstance(greeting, str)
        assert len(greeting) > 0

        print("✅ test_personalization_helpers passed")
    finally:
        shutil.rmtree(tmp)


def test_get_status_and_export():
    """Test status dict and summary export."""
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "user_prefs.json")
        store = UserPreferenceStore(path)

        store.record_topic_query("test topic")
        store.record_session_turn()
        store.record_session_end()

        status = store.get_status()
        assert "verbosity" in status
        assert "top_topics" in status
        assert status["total_sessions"] == 1

        summary = store.export_summary()
        assert "用户偏好摘要" in summary
        assert "test topic" in summary

        print("✅ test_get_status_and_export passed")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_create_default()
    test_record_topic_query()
    test_infer_focus_areas()
    test_verbosity_settings()
    test_infer_verbosity_from_behavior()
    test_notification_threshold()
    test_session_tracking()
    test_learn_from_dialog_history()
    test_personalization_helpers()
    test_get_status_and_export()
    print("\n🎉 All UserPreferenceStore tests passed!")
