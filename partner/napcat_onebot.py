"""NapCat OneBot 11 WebSocket 适配器 - 连接 NapCat Shell WebSocket 服务。

NapCat Shell 以 DLL 注入方式运行在 QQ 客户端进程中，暴露 OneBot 11
协议兼容的 WebSocket 接口。Partner 通过本适配器接收/发送 QQ 消息。

架构:
    QQ User → NTQQ (Windows) → NapCat Shell (DLL 注入)
        ↓ WebSocket (ws://<windows-host>:3001) ← 通过 napcat_proxy 转发
    NapCatProxy → ws://localhost:13001
        ↓
    NapCatOneBot (this module)
        ↓ callback: on_message(msg)
    QQBridge → ConversationEngine → Reply

使用方式:
    from partner.napcat_onebot import NapCatOneBot, NapCatBotConfig

    bot = NapCatOneBot(
        config=NapCatBotConfig(ws_url="ws://localhost:13001")
    )
    bot.set_message_handler(lambda msg: print(msg))

    # 阻塞式
    bot.start()
    # 或后台
    bot.start_async()

数据流:
    [NapCat → WebSocket] → on_event() → on_message_callback (可选)
                    ↓
    send_message(target_id, content) → [NapCat ← REST API ← WebSocket Action]
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Dict, Any, List

logger = logging.getLogger(__name__)


# ── OneBot 11 常量 ─────────────────────────────────────────────────

# 消息格式
class OneBotPostType(Enum):
    MESSAGE = "message"
    NOTICE = "notice"
    REQUEST = "request"
    META_EVENT = "meta_event"


class OneBotMessageType(Enum):
    PRIVATE = "private"
    GROUP = "group"


# 动作端点
ACTION_SEND_PRIVATE_MSG = "send_private_msg"
ACTION_SEND_GROUP_MSG = "send_group_msg"
ACTION_GET_LOGIN_INFO = "get_login_info"
ACTION_GET_GROUP_LIST = "get_group_list"
ACTION_GET_FRIEND_LIST = "get_friend_list"
ACTION_GET_MSG = "get_msg"


# ── 数据模型 ───────────────────────────────────────────────────────

@dataclass
class NapCatMessage:
    """归一化 NapCat OneBot 消息。"""
    msg_id: str                    # 消息 ID
    message_type: OneBotMessageType  # private / group
    sub_type: str                  # friend / group / normal / anonymous / notice
    sender_id: str                 # QQ 号 (str)
    sender_name: str               # 昵称
    group_id: str = ""             # 群号（群消息时）
    group_name: str = ""
    content: str = ""              # 文本内容
    raw_message: str = ""
    timestamp: int = 0
    raw: Any = None                # 原始事件 dict


@dataclass
class NapCatBotInfo:
    """Bot 自身信息。"""
    qq_id: str
    nickname: str


@dataclass
class NapCatBotConfig:
    """NapCat OneBot 客户端配置。"""
    ws_url: str = "ws://localhost:13001"  # NapCat WebSocket 地址
    max_reconnect_retries: int = 5        # 最大重连次数
    reconnect_delay: float = 3.0          # 重连延迟（秒）
    heartbeat_interval: float = 30.0      # 心跳间隔（秒）
    response_timeout: float = 10.0        # API 响应超时（秒）


# ── OneBot 11 适配器 ───────────────────────────────────────────────

class NapCatOneBot:
    """NapCat OneBot 11 WebSocket 客户端。

    连接到 NapCat Shell 暴露的 WebSocket 服务，收发 QQ 消息。
    使用 OneBot 11 标准通信协议（动作 + 事件）。
    """

    def __init__(self, config: NapCatBotConfig = None, **kwargs):
        self.config = config or NapCatBotConfig(**kwargs)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ws = None
        self._reconnect_count = 0
        self._echo_counter = 0
        self._pending_responses: Dict[str, asyncio.Future] = {}
        self._bot_info: Optional[NapCatBotInfo] = None

        # 回调
        self._message_handler: Optional[Callable[[NapCatMessage], None]] = None
        self._ready_handler: Optional[Callable[[NapCatBotInfo], None]] = None
        self._error_handler: Optional[Callable[[Exception], None]] = None

    # ── 公共 API ──────────────────────────────────────────────────

    def set_message_handler(self, handler: Callable[[NapCatMessage], None]):
        self._message_handler = handler

    def set_ready_handler(self, handler: Callable[[NapCatBotInfo], None]):
        self._ready_handler = handler

    def set_error_handler(self, handler: Callable[[Exception], None]):
        self._error_handler = handler

    def start(self):
        """阻塞式启动，连接 NapCat WebSocket。"""
        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except KeyboardInterrupt:
            logger.info("NapCat Bot 由用户停止")
        finally:
            self._loop.close()
            self._running = False

    def start_async(self):
        """在后台线程启动（非阻塞）。"""
        self._thread = threading.Thread(target=self.start, daemon=True)
        self._thread.start()

    def stop(self):
        """停止 Bot。"""
        self._running = False
        if self._ws:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def send_private_msg(
        self, user_id: str, content: str,
        auto_escape: bool = False,
    ) -> Optional[Dict]:
        """发送私聊消息。

        Returns:
            OneBot 响应 JSON，或 None（失败）。
        """
        return self._call_action(
            ACTION_SEND_PRIVATE_MSG,
            {"user_id": int(user_id), "message": content, "auto_escape": auto_escape},
        )

    def send_group_msg(
        self, group_id: str, content: str,
        auto_escape: bool = False,
    ) -> Optional[Dict]:
        """发送群消息。

        Returns:
            OneBot 响应 JSON，或 None（失败）。
        """
        return self._call_action(
            ACTION_SEND_GROUP_MSG,
            {"group_id": int(group_id), "message": content, "auto_escape": auto_escape},
        )

    def get_login_info(self) -> Optional[NapCatBotInfo]:
        """获取 Bot 登录信息。"""
        result = self._call_action(ACTION_GET_LOGIN_INFO, {})
        if result and result.get("status") == "ok":
            data = result.get("data", {})
            self._bot_info = NapCatBotInfo(
                qq_id=str(data.get("user_id", "")),
                nickname=data.get("nickname", ""),
            )
            return self._bot_info
        return None

    def get_group_list(self) -> List[Dict]:
        """获取群列表。"""
        result = self._call_action(ACTION_GET_GROUP_LIST, {})
        if result and result.get("status") == "ok":
            return result.get("data", [])
        return []

    def get_friend_list(self) -> List[Dict]:
        """获取好友列表。"""
        result = self._call_action(ACTION_GET_FRIEND_LIST, {})
        if result and result.get("status") == "ok":
            return result.get("data", [])
        return []

    def is_connected(self) -> bool:
        """检查 WebSocket 是否连接。"""
        return self._running and self._ws is not None and not self._ws.closed

    def get_bot_info(self) -> Optional[NapCatBotInfo]:
        return self._bot_info

    # ── 内部: 事件循环 ──────────────────────────────────────────

    async def _run(self):
        """主循环：连接 NapCat → 接收事件 → 处理消息。"""
        import aiohttp
        from aiohttp import ClientWebSocketResponse, WSMsgType

        ws_url = self.config.ws_url
        logger.info(f"🔌 正在连接 NapCat: {ws_url}")

        while self._running and self._reconnect_count < self.config.max_reconnect_retries:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        ws_url,
                        heartbeat=self.config.heartbeat_interval,
                        timeout=aiohttp.ClientWSTimeout(
                            ws_close=5,
                        ),
                    ) as ws:
                        self._ws = ws
                        self._reconnect_count = 0
                        logger.info(f"✅ 已连接 NapCat: {ws_url}")

                        # 连接成功后获取 Bot 信息
                        bot_info = await self._get_login_info_async()
                        if bot_info:
                            self._bot_info = bot_info
                            logger.info(
                                f"🤖 Bot: {bot_info.nickname} ({bot_info.qq_id})"
                            )
                            if self._ready_handler:
                                self._ready_handler(bot_info)

                        # 事件循环
                        async for msg in ws:
                            if msg.type == WSMsgType.TEXT:
                                await self._handle_message(msg.data)
                            elif msg.type == WSMsgType.ERROR:
                                logger.error(f"WebSocket 错误: {ws.exception()}")
                                break
                            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED):
                                logger.info("WebSocket 连接已关闭")
                                break

            except asyncio.CancelledError:
                break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                logger.warning(f"⚠️  连接失败: {e}")
            except Exception as e:
                logger.error(f"❌ 未知错误: {e}")
                if self._error_handler:
                    self._error_handler(e)

            # 重连
            if self._running:
                self._reconnect_count += 1
                delay = self.config.reconnect_delay * min(self._reconnect_count, 3)
                logger.info(
                    f"🔄 重连 ({self._reconnect_count}/"
                    f"{self.config.max_reconnect_retries}) 等待 {delay}s..."
                )
                await asyncio.sleep(delay)

        if self._reconnect_count >= self.config.max_reconnect_retries:
            logger.error("❌ 达到最大重连次数")

    # ── 内部: 消息处理 ──────────────────────────────────────────

    async def _handle_message(self, raw_data: str):
        """处理收到的 WebSocket 消息。"""
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.warning(f"无法解析消息: {raw_data[:200]}")
            return

        # OneBot 消息有 post_type 字段
        post_type = data.get("post_type")

        if post_type == OneBotPostType.MESSAGE.value:
            await self._handle_onebot_event(data)
        elif post_type == OneBotPostType.META_EVENT.value:
            meta_type = data.get("meta_event_type", "")
            if meta_type == "lifecycle":
                logger.info(f"🔄 NapCat 生命周期事件: {data.get('sub_type', '')}")
        elif post_type == OneBotPostType.NOTICE.value:
            notice_type = data.get("notice_type", "")
            logger.debug(f"通知事件: {notice_type}")
        elif "echo" in data:
            # API 调用的响应
            await self._handle_api_response(data)
        else:
            logger.debug(f"未处理的消息类型: {post_type}")

    async def _handle_onebot_event(self, data: Dict):
        """处理 OneBot 消息事件。"""
        msg_type = data.get("message_type", "")

        if msg_type == OneBotMessageType.PRIVATE.value:
            msg = NapCatMessage(
                msg_id=str(data.get("message_id", "")),
                message_type=OneBotMessageType.PRIVATE,
                sub_type=data.get("sub_type", ""),
                sender_id=str(data.get("sender", {}).get("user_id", "")),
                sender_name=data.get("sender", {}).get("nickname", ""),
                content=self._extract_text(data.get("message", "")),
                raw_message=data.get("raw_message", ""),
                timestamp=data.get("time", 0),
                raw=data,
            )
        elif msg_type == OneBotMessageType.GROUP.value:
            msg = NapCatMessage(
                msg_id=str(data.get("message_id", "")),
                message_type=OneBotMessageType.GROUP,
                sub_type=data.get("sub_type", ""),
                sender_id=str(data.get("sender", {}).get("user_id", "")),
                sender_name=data.get("sender", {}).get("nickname", ""),
                group_id=str(data.get("group_id", "")),
                group_name=data.get("sender", {}).get("card", ""),
                content=self._extract_text(data.get("message", "")),
                raw_message=data.get("raw_message", ""),
                timestamp=data.get("time", 0),
                raw=data,
            )
        else:
            logger.debug(f"未知消息类型: {msg_type}")
            return

        if self._message_handler:
            self._message_handler(msg)

    @staticmethod
    def _extract_text(message: Any) -> str:
        """从 OneBot 消息段数组中提取纯文本。

        OneBot `message` 字段可能是：
        - string: "Hello"
        - array: [{"type": "text", "data": {"text": "Hello"}}, ...]
        """
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            texts = []
            for seg in message:
                if isinstance(seg, dict):
                    if seg.get("type") == "text":
                        texts.append(seg.get("data", {}).get("text", ""))
                    elif seg.get("type") == "face":
                        texts.append(f"[表情:{seg.get('data',{}).get('id','')}]")
                    elif seg.get("type") == "image":
                        texts.append("[图片]")
                    elif seg.get("type") == "voice":
                        texts.append("[语音]")
                    elif seg.get("type") == "at":
                        qq = seg.get("data", {}).get("qq", "")
                        texts.append(f"@{qq}")
                    else:
                        texts.append(f"[{seg.get('type','?')}]")
            return "".join(texts)
        return str(message)

    # ── 内部: API 调用 ──────────────────────────────────────────

    def _call_action(
        self, action: str, params: Dict, timeout: float = None,
    ) -> Optional[Dict]:
        """同步调用 OneBot 动作（通过线程安全方式）。"""
        if not self._loop or not self._loop.is_running():
            logger.error("事件循环未运行")
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._call_action_async(action, params),
            self._loop,
        )
        try:
            return future.result(timeout=timeout or self.config.response_timeout)
        except Exception as e:
            logger.error(f"动作调用失败 [{action}]: {e}")
            return None

    async def _call_action_async(
        self, action: str, params: Dict,
    ) -> Optional[Dict]:
        """异步调用 OneBot 动作。

        通过 WebSocket 发送动作请求，等待带 echo 的响应。
        """
        if not self._ws or self._ws.closed:
            logger.error("WebSocket 未连接")
            return None

        self._echo_counter += 1
        echo = f"echo_{self._echo_counter}"
        payload = {"action": action, "params": params, "echo": echo}

        # 注册待响应
        future = self._loop.create_future()
        self._pending_responses[echo] = future

        try:
            await self._ws.send_json(payload)
            # 等待响应（带超时）
            result = await asyncio.wait_for(
                future, timeout=self.config.response_timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"动作超时 [{action}/{echo}]")
            return None
        finally:
            self._pending_responses.pop(echo, None)

    async def _handle_api_response(self, data: Dict):
        """处理 API 调用的响应。"""
        echo = data.get("echo", "")
        if echo in self._pending_responses:
            future = self._pending_responses[echo]
            if not future.done():
                future.set_result(data)

    async def _get_login_info_async(self) -> Optional[NapCatBotInfo]:
        """异步获取登录信息。"""
        result = await self._call_action_async(ACTION_GET_LOGIN_INFO, {})
        if result and result.get("status") == "ok":
            data = result.get("data", {})
            return NapCatBotInfo(
                qq_id=str(data.get("user_id", "")),
                nickname=data.get("nickname", ""),
            )
        return None


# ── 便捷函数 ───────────────────────────────────────────────────────

def test_connection(ws_url: str = "ws://localhost:13001", timeout: float = 5) -> bool:
    """快速测试 NapCat 连接。

    尝试连接 NapCat WebSocket，检查是否可用。

    Returns:
        True 如果连接成功且收到生命周期事件。
    """
    import asyncio

    async def _test():
        import aiohttp
        from aiohttp import WSMsgType

        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    ws_url, timeout=aiohttp.ClientWSTimeout(ws_close=2),
                ) as ws:
                    # 等待第一个事件（应该是 lifecycle meta_event）
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            return True
                        break
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return False

    return asyncio.run(_test())
