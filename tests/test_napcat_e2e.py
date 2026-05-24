#!/usr/bin/env python3
"""NapCat Shell + Partner QQ Bridge 端到端连接测试脚本.

此脚本测试从 NapCat Shell (OneBot 11 WebSocket) 到 Partner QQ Bridge 的完整链路：
1. TCP 端口可达性测试
2. WebSocket 握手测试
3. OneBot 11 Action 发送/响应测试
4. 消息事件模拟测试
5. QQ Bridge 集成测试

使用方式：
    python3 test_napcat_e2e.py [--ws-url ws://127.0.0.1:3001] [--test-level 1-5]

测试级别：
    1 - 仅 TCP 端口检测
    2 - + WebSocket 握手
    3 - + OneBot 11 Action（get_self_info, get_group_list）
    4 - + 发送测试消息到指定群/用户
    5 - + QQ Bridge 完整集成（需要 Partner 环境）

前提条件：
    - Windows 上已安装并启动 NapCat Shell 模式
    - QQ 已登录（NapCat Shell 会自动注入）
    - onebot11.json 配置了 WebSocket Server（默认端口 3001）
"""

import os
import sys
import json
import time
import socket
import asyncio
import argparse
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ── Test Results ──────────────────────────────────────────────────

class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""
        self.details: Dict[str, Any] = {}
        self.duration_ms: float = 0

    def __repr__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return f"{status} [{self.name}] {self.message} ({self.duration_ms:.0f}ms)"


def run_tests(ws_url: str, test_level: int, target_id: str = "", is_group: bool = True,
              access_token: str = "") -> List[TestResult]:
    """Run connectivity tests at the specified level."""
    results: List[TestResult] = []

    # Parse host:port from ws_url
    # ws://127.0.0.1:3001 → host=127.0.0.1, port=3001
    url_parts = ws_url.replace("ws://", "").replace("wss://", "").split(":")
    host = url_parts[0]
    port = int(url_parts[1]) if len(url_parts) > 1 else 3001

    # ── Level 1: TCP Port Check ───────────────────────────────────
    r = _test_tcp_port(host, port)
    results.append(r)
    if not r.passed and test_level >= 2:
        logger.error("TCP 端口不可达，跳过后续测试")
        return results

    if test_level < 2:
        return results

    # ── Level 2: WebSocket Handshake ──────────────────────────────
    r = asyncio.run(_test_ws_handshake(ws_url, access_token))
    results.append(r)
    if not r.passed and test_level >= 3:
        logger.error("WebSocket 握手失败，跳过后续测试")
        return results

    if test_level < 3:
        return results

    # ── Level 3: OneBot 11 Actions ────────────────────────────────
    action_results = asyncio.run(_test_ob11_actions(ws_url, access_token))
    results.extend(action_results)

    if test_level < 4:
        return results

    # ── Level 4: Send Test Message ────────────────────────────────
    if target_id:
        r = asyncio.run(_test_send_message(ws_url, access_token, target_id, is_group))
        results.append(r)
    else:
        r = TestResult("发送测试消息")
        r.message = "跳过（未指定 --target-id）"
        results.append(r)

    if test_level < 5:
        return results

    # ── Level 5: QQ Bridge Integration ────────────────────────────
    r = _test_bridge_import()
    results.append(r)

    return results


# ── Individual Tests ──────────────────────────────────────────────

def _test_tcp_port(host: str, port: int) -> TestResult:
    """Test TCP port reachability."""
    r = TestResult(f"TCP 端口连通性 ({host}:{port})")
    start = time.time()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()

        r.duration_ms = (time.time() - start) * 1000
        if result == 0:
            r.passed = True
            r.message = f"端口 {port} 可达"
        else:
            r.message = f"端口 {port} 不可达 (error code: {result})"
            r.details["suggestion"] = (
                "请确认：\n"
                "1. NapCat Shell 已启动（运行 launcher.bat）\n"
                "2. QQ 已登录成功（NapCat 控制台显示 'login success'）\n"
                "3. onebot11.json 中 websocketServers 已启用\n"
                "4. 防火墙未阻止端口 3001"
            )
    except Exception as e:
        r.duration_ms = (time.time() - start) * 1000
        r.message = f"连接异常: {e}"

    logger.info(str(r))
    return r


async def _test_ws_handshake(ws_url: str, access_token: str = "") -> TestResult:
    """Test WebSocket handshake."""
    r = TestResult("WebSocket 握手")
    start = time.time()

    try:
        import websockets

        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with websockets.connect(
            ws_url,
            additional_headers=headers,
            open_timeout=5,
        ) as ws:
            r.duration_ms = (time.time() - start) * 1000
            r.passed = True
            r.message = f"WebSocket 连接成功"
            r.details["ws_url"] = ws_url

            # Wait briefly for any initial events
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                data = json.loads(raw, strict=False)
                r.details["first_event"] = {
                    "post_type": data.get("post_type"),
                    "meta_event_type": data.get("meta_event_type"),
                }
                r.message += f"，收到初始事件: {data.get('meta_event_type', data.get('post_type', 'unknown'))}"
            except asyncio.TimeoutError:
                r.message += "，无初始事件（正常）"

    except ImportError:
        r.duration_ms = (time.time() - start) * 1000
        r.message = "websockets 包未安装 (pip install websockets)"
    except Exception as e:
        r.duration_ms = (time.time() - start) * 1000
        r.message = f"WebSocket 连接失败: {type(e).__name__}: {e}"
        r.details["suggestion"] = (
            "可能原因：\n"
            "1. NapCat 未启动或未完成登录\n"
            "2. WebSocket Server 未启用（检查 onebot11.json）\n"
            "3. access_token 不匹配"
        )

    logger.info(str(r))
    return r


async def _test_ob11_actions(ws_url: str, access_token: str = "") -> List[TestResult]:
    """Test OneBot 11 standard actions."""
    results = []

    try:
        import websockets

        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with websockets.connect(
            ws_url,
            additional_headers=headers,
            open_timeout=5,
        ) as ws:
            # Test get_login_info
            r = await _send_action_and_wait(ws, "get_login_info", {}, "获取登录信息")
            results.append(r)
            if r.passed:
                bot_info = r.details.get("response_data", {})
                logger.info(f"  Bot QQ: {bot_info.get('user_id')}, Nickname: {bot_info.get('nickname')}")

            # Test get_group_list
            r = await _send_action_and_wait(ws, "get_group_list", {}, "获取群列表")
            results.append(r)
            if r.passed:
                groups = r.details.get("response_data", [])
                if isinstance(groups, list):
                    logger.info(f"  群数量: {len(groups)}")
                    for g in groups[:5]:
                        logger.info(f"    - {g.get('group_name', '?')} ({g.get('group_id', '?')})")

            # Test get_friend_list
            r = await _send_action_and_wait(ws, "get_friend_list", {}, "获取好友列表")
            results.append(r)
            if r.passed:
                friends = r.details.get("response_data", [])
                if isinstance(friends, list):
                    logger.info(f"  好友数量: {len(friends)}")

    except Exception as e:
        r = TestResult("OneBot 11 Actions")
        r.message = f"连接失败: {e}"
        results.append(r)

    return results


async def _send_action_and_wait(ws, action: str, params: Dict, test_name: str,
                                 timeout: float = 10) -> TestResult:
    """Send an OneBot 11 action and wait for response."""
    r = TestResult(test_name)
    start = time.time()
    echo = int(time.time() * 1000)

    try:
        msg = json.dumps({
            "action": action,
            "params": params,
            "echo": echo,
        })
        await ws.send(msg)

        # Wait for response with matching echo
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(raw, strict=False)
            if data.get("echo") == echo:
                r.duration_ms = (time.time() - start) * 1000
                if data.get("status") == "ok":
                    r.passed = True
                    r.message = f"{test_name} 成功"
                    r.details["response_data"] = data.get("data")
                else:
                    r.message = f"{test_name} 失败: retcode={data.get('retcode')}, msg={data.get('msg')}"
                r.details["raw_response"] = data
                break
        else:
            r.duration_ms = (time.time() - start) * 1000
            r.message = f"{test_name} 超时 ({timeout}s)"

    except Exception as e:
        r.duration_ms = (time.time() - start) * 1000
        r.message = f"{test_name} 异常: {e}"

    logger.info(str(r))
    return r


async def _test_send_message(ws_url: str, access_token: str, target_id: str,
                              is_group: bool) -> TestResult:
    """Send a test message."""
    r = TestResult("发送测试消息")
    start = time.time()

    try:
        import websockets

        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        async with websockets.connect(ws_url, additional_headers=headers, open_timeout=5) as ws:
            echo = int(time.time() * 1000)
            test_text = f"[Partner E2E Test] {datetime.now().strftime('%H:%M:%S')}"

            if is_group:
                action = "send_group_msg"
                params = {"group_id": target_id, "message": test_text}
            else:
                action = "send_private_msg"
                params = {"user_id": target_id, "message": test_text}

            await ws.send(json.dumps({
                "action": action,
                "params": params,
                "echo": echo,
            }))

            deadline = time.time() + 10
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(raw, strict=False)
                if data.get("echo") == echo:
                    r.duration_ms = (time.time() - start) * 1000
                    if data.get("status") == "ok":
                        r.passed = True
                        r.message = f"消息发送成功 → {'群' if is_group else '好友'} {target_id}"
                        r.details["message_id"] = data.get("data", {}).get("message_id")
                    else:
                        r.message = f"发送失败: {data.get('retcode')} {data.get('msg')}"
                    break
            else:
                r.duration_ms = (time.time() - start) * 1000
                r.message = "发送超时"

    except Exception as e:
        r.duration_ms = (time.time() - start) * 1000
        r.message = f"发送异常: {e}"

    logger.info(str(r))
    return r


def _test_bridge_import() -> TestResult:
    """Test QQ Bridge import and initialization."""
    r = TestResult("QQ Bridge 导入测试")
    start = time.time()

    try:
        sys.path.insert(0, "/mnt/e/work/study_room/partner")
        from partner.qq_napcat import NapCatAdapter, QQMessage
        from partner.qq_bridge import QQBridge, QQBridgeConfig

        # Test NapCatAdapter instantiation
        adapter = NapCatAdapter({"ws_url": "ws://127.0.0.1:3001"})
        assert hasattr(adapter, "start")
        assert hasattr(adapter, "stop")
        assert hasattr(adapter, "send_text")

        # Test QQMessage creation
        msg = QQMessage(
            msg_id="test_001",
            message_type="private",
            sub_type="friend",
            sender_id="12345",
            sender_name="TestUser",
            group_id="",
            content="Hello Partner!",
            raw_message="Hello Partner!",
            timestamp=int(time.time()),
        )
        assert msg.content == "Hello Partner!"

        # Test QQBridgeConfig
        config = QQBridgeConfig(ws_url="ws://127.0.0.1:3001")
        assert config.ws_url == "ws://127.0.0.1:3001"

        r.duration_ms = (time.time() - start) * 1000
        r.passed = True
        r.message = "QQ Bridge 组件导入和实例化正常"

    except Exception as e:
        r.duration_ms = (time.time() - start) * 1000
        r.message = f"导入失败: {e}"

    logger.info(str(r))
    return r


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NapCat Shell + Partner QQ Bridge E2E Test")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:3001",
                        help="NapCat WebSocket URL (default: ws://127.0.0.1:3001)")
    parser.add_argument("--test-level", type=int, choices=[1, 2, 3, 4, 5], default=3,
                        help="Test depth level (1-5, default: 3)")
    parser.add_argument("--target-id", default="",
                        help="Target group/user ID for message send test")
    parser.add_argument("--is-group", action="store_true", default=True,
                        help="Target is a group (default: True)")
    parser.add_argument("--token", default="",
                        help="OneBot access token")
    args = parser.parse_args()

    print("=" * 60)
    print("NapCat Shell + Partner QQ Bridge E2E Test")
    print("=" * 60)
    print(f"WebSocket URL: {args.ws_url}")
    print(f"Test Level: {args.test_level}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    results = run_tests(
        ws_url=args.ws_url,
        test_level=args.test_level,
        target_id=args.target_id,
        is_group=args.is_group,
        access_token=args.token,
    )

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    for r in results:
        print(f"  {r}")
        if r.details.get("suggestion"):
            print(f"    💡 {r.details['suggestion']}")

    print(f"\n通过: {passed}/{total}")
    if passed == total:
        print("🎉 所有测试通过！NapCat Shell → Partner QQ Bridge 链路正常。")
    else:
        print("⚠️ 部分测试失败，请检查上方提示。")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
