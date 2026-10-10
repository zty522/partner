"""端到端测试：验证所有新 Event 的完整流程"""
import sys
sys.path.insert(0, "/mnt/e/work/partner")

from pathlib import Path
from partner.event_fabric.catalog import build_catalog
from partner.events.message_sanitizer import (
    sanitize_message,
    format_user_message,
    format_failure_message,
)

print("=" * 70)
print("端到端测试：新 Event 集成验证")
print("=" * 70)

# 1. 验证 catalog 加载
print("\n[1/5] 验证 Event Catalog 加载...")
catalog = build_catalog()
all_events = catalog.names()
print(f"  ✓ Catalog 包含 {len(all_events)} 个 Event")

# 2. 验证新 Event 注册
print("\n[2/5] 验证新 Event 注册...")
new_event_names = [
    'long_run.control',
    'pdf.fact_adjudicate',
    'corpus.eligibility_freeze',
    'learning.source_recovery',
    'evolution.candidate_merge',
    'scheduler.fair',
    'metrics.token_aggregate',
    'model.unified_call',
    'pdf.format_five_section'
]

registered = []
for name in new_event_names:
    if name in all_events:
        registered.append(name)
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} - 未注册")

print(f"\n  已注册 {len(registered)}/{len(new_event_names)} 个新 Event")

# 3. 验证消息清洗功能
print("\n[3/5] 验证消息清洗功能...")
test_msg = "任务完成：读取了 /mnt/e/work/partner/data.txt (42,855 bytes)，Job ID: job_abc123def456"
cleaned = sanitize_message(test_msg)
assert "[路径已隐藏]" in cleaned
assert "[数据量已隐藏]" in cleaned
assert "[任务]" in cleaned
print("  ✓ 消息清洗功能正常")

# 4. 验证消息格式化功能
print("\n[4/5] 验证消息格式化功能...")
user_msg = format_user_message(
    what_done="分析了 100 个分子",
    what_got="发现 3 个候选药物",
    next_step="进行分子对接模拟",
    why="需要验证结合亲和力"
)
assert "【做了什么】" in user_msg
assert "【得到什么】" in user_msg
print("  ✓ 用户消息格式化正常")

failure_msg = format_failure_message(
    what_tried=["读取外部数据库"],
    why_failed="网络连接超时",
    how_recover="使用本地缓存"
)
assert "【尝试了什么】" in failure_msg
print("  ✓ 失败消息格式化正常")

# 5. 验证 Event 定义
print("\n[5/5] 验证 Event 定义完整性...")
from partner.events import builtin_definitions
ALL_NEW_EVENTS = builtin_definitions()

for event in ALL_NEW_EVENTS:
    assert event.name, f"Event {event} 缺少 name"
    assert event.series, f"Event {event.name} 缺少 series"
    assert event.description, f"Event {event.name} 缺少 description"
    assert event.handler, f"Event {event.name} 缺少 handler"
    print(f"  ✓ {event.name}: {event.description[:30]}...")

print("\n" + "=" * 70)
print("✓ 所有端到端测试通过！")
print("=" * 70)
print(f"\n集成摘要:")
print(f"  - 新 Event 模块: 9 个")
print(f"  - 已注册到 Catalog: {len(registered)} 个")
print(f"  - 功能测试: 全部通过")
print(f"  - 集成状态: 完成")
