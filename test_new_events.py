"""测试所有新 Event 的功能"""
import sys
sys.path.insert(0, "/mnt/e/work/partner")

from pathlib import Path
from partner.events import (
    sanitize_message,
    format_user_message,
    format_failure_message,
    ALL_NEW_EVENTS
)

print("=" * 60)
print("测试 1: 消息清洗功能")
print("=" * 60)

test_msg = """
任务完成：
- 读取了 /mnt/e/work/partner/data/file.txt (42,855 bytes)
- 执行 local_read 步骤
- Job ID: job_abc123def456
- 第 3/10 步
completed=true
"""

cleaned = sanitize_message(test_msg)
print("原始消息:")
print(test_msg)
print("\n清洗后:")
print(cleaned)

# 验证清洗结果
assert "[路径已隐藏]" in cleaned, "路径未被隐藏"
assert "[数据量已隐藏]" in cleaned, "bytes 未被隐藏"
assert "[处理步骤]" in cleaned, "Event 名称未被隐藏"
assert "[任务]" in cleaned, "Job ID 未被隐藏"
assert "[进度]" in cleaned, "步骤计数未被隐藏"
assert "[状态]" in cleaned, "状态码未被隐藏"
print("✓ 消息清洗测试通过")

print("\n" + "=" * 60)
print("测试 2: 用户消息格式化")
print("=" * 60)

user_msg = format_user_message(
    what_done="分析了 100 个分子",
    what_got="发现 3 个候选药物",
    next_step="进行分子对接模拟",
    why="需要验证结合亲和力"
)
print(user_msg)
assert "【做了什么】" in user_msg
assert "【得到什么】" in user_msg
assert "【下一步】" in user_msg
assert "【为什么】" in user_msg
print("✓ 用户消息格式化测试通过")

print("\n" + "=" * 60)
print("测试 3: 失败消息格式化")
print("=" * 60)

failure_msg = format_failure_message(
    what_tried=["读取外部数据库", "调用 API 接口"],
    why_failed="网络连接超时",
    how_recover="使用本地缓存数据"
)
print(failure_msg)
assert "【尝试了什么】" in failure_msg
assert "【为什么失败】" in failure_msg
assert "【下一步怎么恢复】" in failure_msg
print("✓ 失败消息格式化测试通过")

print("\n" + "=" * 60)
print("测试 4: Event 定义验证")
print("=" * 60)

for event in ALL_NEW_EVENTS:
    print(f"Event: {event.name}")
    print(f"  Series: {event.series}")
    print(f"  Description: {event.description}")
    print(f"  Handler: {event.handler.__name__}")
    assert event.name, "Event name 为空"
    assert event.series, "Event series 为空"
    assert event.description, "Event description 为空"
    assert event.handler, "Event handler 为空"

print(f"\n✓ 所有 {len(ALL_NEW_EVENTS)} 个 Event 定义验证通过")

print("\n" + "=" * 60)
print("所有测试通过！")
print("=" * 60)
