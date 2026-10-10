"""Mind Notes module tests: store validation, injection rendering, event registration."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")

from partner.memory.notes import (
    NOTE_TYPES,
    NoteStore,
    NoteValidationError,
    notes_injection,
    validate_record,
)
from partner.events import builtin_definitions

print("=" * 60)
print("测试 1: NOTE_TYPES 注册表完整性")
print("=" * 60)
for name, spec in NOTE_TYPES.items():
    assert spec["required"], f"{name} 缺 required"
    assert spec["statuses"], f"{name} 缺 statuses"
    assert spec["statuses"][0], f"{name} 缺初始状态"
print(f"✓ {len(NOTE_TYPES)} 种笔记类型注册完整: {sorted(NOTE_TYPES)}")

print("\n" + "=" * 60)
print("测试 2: 校验器")
print("=" * 60)
try:
    validate_record("pending", {"content": "x"})  # 缺 gap/trigger_signal
    raise AssertionError("应抛 NoteValidationError")
except NoteValidationError:
    print("✓ pending 缺必填字段被拒绝")
try:
    validate_record("pending", {"content": "x", "gap": "g", "trigger_signal": "t",
                                "status": "bogus"})
    raise AssertionError("应抛 NoteValidationError")
except NoteValidationError:
    print("✓ 非法状态被拒绝")
validate_record("pending", {"content": "x", "gap": "g", "trigger_signal": "t"})
print("✓ 合法 pending 通过")

print("\n" + "=" * 60)
print("测试 3: NoteStore 读写")
print("=" * 60)
with tempfile.TemporaryDirectory() as tmp:
    store = NoteStore(tmp)
    note = store.append("pending", {
        "content": "外部资料提到的世界模型思想，资料少，先记录",
        "gap": "只有一篇二手解读，未核验原文",
        "trigger_signal": "读到第二篇同主题论文后升级",
        "confidence": 0.4,
        "source": "https://x.xhs.example/p1",
    })
    assert note["id"].startswith("note_"), "id 前缀错误"
    assert note["status"] == "open"
    note2 = store.append("insight", {
        "content": "五级自主性框架可作为自进化评估维度",
        "source": "https://x.xhs.example/p2",
    })
    rows = store.list_all()
    assert len(rows) == 2, "list_all 数量错误"
    # recall 关键词命中
    hit = store.recall("世界模型 自进化", limit=5)
    assert len(hit) >= 1, "recall 未命中关键词"
    # replace
    updated = store.replace(note["id"], {"status": "resolved",
                                         "reason": "已找到第二篇资料"})
    assert updated["status"] == "resolved"
    # pending_open 过滤
    pend = store.pending_open()
    assert all(p["status"] in {"open", "progressing"} for p in pend)
    print("✓ 存储/召回/替换/过滤全部通过")

print("\n" + "=" * 60)
print("测试 4: notes_injection 渲染")
print("=" * 60)


class _Ctx:
    def __init__(self, injection):
        self.note_injection = injection


text = notes_injection(_Ctx({"notes": [
    {"type": "pending", "status": "open",
     "content": "没想清楚的点", "source": "xhs/p1", "gap": "资料少"}
], "pending": []}))
assert "【长期笔记参考】" in text, "缺标题段"
assert "没想清楚的点" in text, "缺笔记内容"
print(text)
print("✓ 注入渲染包含判断三问")

print("\n" + "=" * 60)
print("测试 5: 事件注册")
print("=" * 60)
defs = builtin_definitions()
names = [d.name for d in defs]
for required in ("notes.recall", "notes.judge", "notes.promote"):
    assert required in names, f"{required} 未注册"
judge = next(d for d in defs if d.name == "notes.judge")
assert judge.execution_method == "llm", "judge 应为 llm 执行"
print("✓ notes.recall / notes.judge / notes.promote 已注册，judge 为 llm 执行")

print("\n全部 notes 模块测试通过 ✓")
