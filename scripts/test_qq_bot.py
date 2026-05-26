#!/usr/bin/env python3
"""Quick test script for QQ Official Bot configuration.

Tests:
  1. Token refresh (AppID + AppSecret)
  2. Gateway URL retrieval
  3. Config file format

Usage:
    python test_qq_bot.py --app-id YOUR_APP_ID --app-secret YOUR_APP_SECRET
    python test_qq_bot.py --config /path/to/qq_config.json
"""

import argparse
import json
import os
import sys


def test_token(app_id: str, app_secret: str):
    """Test token refresh endpoint."""
    print("🔑 测试 Token 获取...")
    import asyncio

    async def _test():
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://bots.qq.com/app/getAppAccessToken",
                    json={"appId": app_id, "clientSecret": app_secret},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    status = resp.status
                    data = await resp.json()
                    if status == 200 and "access_token" in data:
                        token = data["access_token"]
                        expires = data.get("expires_in", "?")
                        print(f"  ✅ Token 获取成功!")
                        print(f"     Token: {token[:20]}...{token[-10:]}")
                        print(f"     过期时间: {expires}秒")
                        return token
                    else:
                        print(f"  ❌ Token 获取失败 (HTTP {status}): {data}")
                        return None
        except Exception as e:
            print(f"  ❌ 连接失败: {e}")
            print("     请检查网络连接和防火墙设置")
            return None

    return asyncio.run(_test())


def test_gateway(token: str):
    """Test gateway URL retrieval."""
    print()
    print("🌐 测试 Gateway 连接...")
    import asyncio

    async def _test():
        import aiohttp
        try:
            headers = {
                "Authorization": f"QQBot {token}",
                "X-Union-Appid": app_id,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.sgroup.qq.com/gateway/bot",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    status = resp.status
                    data = await resp.json()
                    if status == 200:
                        url = data.get("url", "")
                        shards = data.get("shards", 1)
                        print(f"  ✅ Gateway 连接成功!")
                        print(f"     WebSocket URL: {url}")
                        print(f"     分片数: {shards}")
                        return True
                    else:
                        print(f"  ❌ Gateway 请求失败 (HTTP {status}): {data}")
                        return False
        except Exception as e:
            print(f"  ❌ 连接失败: {e}")
            return False

    return asyncio.run(_test())


def test_ws_connect(token: str, ws_url: str):
    """Test WebSocket connection (quick connect + hello)."""
    print()
    print("🔌 测试 WebSocket 连接...")
    import asyncio

    async def _test():
        from ssl import SSLContext
        import aiohttp
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=SSLContext())
            ) as session:
                async with session.ws_connect(ws_url) as ws:
                    # Wait for HELLO message
                    msg = await ws.receive(timeout=10)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("op") == 10:  # HELLO
                            interval = data.get("d", {}).get("heartbeat_interval", 0)
                            print(f"  ✅ WebSocket 连接成功!")
                            print(f"     心跳间隔: {interval}ms")
                            return True
                    print(f"  ❌ 未收到 HELLO: {msg}")
                    return False
        except asyncio.TimeoutError:
            print(f"  ❌ WebSocket 连接超时 (10秒)")
            return False
        except Exception as e:
            print(f"  ❌ WebSocket 连接失败: {e}")
            return False

    return asyncio.run(_test())


def print_config_template():
    """Print config file template."""
    print()
    print("📋 qq_config.json 模板:")
    print("""
    {
        "app_id": "YOUR_APP_ID",
        "app_secret": "YOUR_APP_SECRET",
        "is_sandbox": false,
        "auto_reconnect": true
    }
    """)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QQ Official Bot 配置测试")
    parser.add_argument("--app-id", help="Bot AppID")
    parser.add_argument("--app-secret", help="Bot AppSecret")
    parser.add_argument("--config", help="QQ Config JSON 文件路径")
    parser.add_argument("--template", action="store_true", help="打印配置模板")
    args = parser.parse_args()

    if args.template:
        print_config_template()
        sys.exit(0)

    # Load config
    app_id = args.app_id
    app_secret = args.app_secret

    if args.config:
        try:
            with open(args.config) as f:
                config = json.load(f)
            app_id = app_id or config.get("app_id")
            app_secret = app_secret or config.get("app_secret")
        except Exception as e:
            print(f"❌ 读取配置失败: {e}")
            sys.exit(1)

    if not app_id or not app_secret:
        print("❌ 需要提供 AppID 和 AppSecret")
        print()
        print("  python test_qq_bot.py --app-id YOUR_ID --app-secret YOUR_SECRET")
        print("  python test_qq_bot.py --config qq_config.json")
        print("  python test_qq_bot.py --template    # 查看配置模板")
        sys.exit(1)

    print("=" * 60)
    print("🐧 QQ Official Bot 连接测试")
    print("=" * 60)
    print(f"   AppID: {app_id}")

    # Step 1: Get token
    token = test_token(app_id, app_secret)

    if not token:
        print()
        print("❌ 测试失败: Token 获取失败")
        print("   请检查:")
        print("   1. AppID 和 AppSecret 是否正确")
        print("   2. 在 q.qq.com 上启用了机器人能力")
        print("   3. 网络连接正常")
        sys.exit(1)

    # Step 2: Test gateway
    gateway_ok = test_gateway(token)

    if not gateway_ok:
        print()
        print("❌ 测试失败: Gateway 连接失败")
        print("   请检查机器人权限设置")
        sys.exit(1)

    print()
    print("🎉 所有测试通过！配置正常。")
    print()
    print("下一步: partner qq start")
