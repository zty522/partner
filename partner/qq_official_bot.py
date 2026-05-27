"""QQ Official Bot Adapter - QQ开放平台官方机器人 WebSocket 适配器.

Connects to Tencent QQ Official Bot platform (q.qq.com) using WebSocket.
Supports:
  - Private chat (C2C) messages
  - Group @mention messages
  - REST API message replies
  - Auto reconnection
  - Heartbeat keepalive

Requires AppID + AppSecret from https://q.qq.com/ (create a bot application).

Architecture:
  QQ Bot Platform (api.sgroup.qq.com)
      |  WebSocket Gateway (wss://...)
      |  REST API (https://api.sgroup.qq.com/)
      ↓
  QQBridge (this module)
      |  callback: on_message(msg)
      ↓
  Partner ConversationEngine or user's handler

Usage:
    from partner.qq_official_bot import QQQfficialBot

    def on_msg(msg):
        print(f"[{msg.message_type}] {msg.sender_name}: {msg.content}")

    bot = QQQfficialBot(app_id="YOUR_APP_ID", app_secret="YOUR_APP_SECRET")
    bot.set_message_handler(on_msg)

    # Blocking
    bot.start()
    # or non-blocking
    bot.start_async()
"""

import asyncio
import json
import logging
import random
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.sgroup.qq.com"
SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"
WS_OPCODE = type("WS", (), {})()
WS_OPCODE.DISPATCH = 0          # Server push event
WS_OPCODE.HEARTBEAT = 1         # Client/server heartbeat
WS_OPCODE.IDENTIFY = 2          # Client authentication
WS_OPCODE.RESUME = 6            # Client reconnection
WS_OPCODE.RECONNECT = 7         # Server requests reconnection
WS_OPCODE.INVALID_SESSION = 9   # Invalid session
WS_OPCODE.HELLO = 10            # First message after connect
WS_OPCODE.HEARTBEAT_ACK = 11    # Heartbeat acknowledgement

# Events we care about
EVENT_AT_MESSAGE = "AT_MESSAGE_CREATE"            # Bot @mentioned in a channel/guild
EVENT_C2C_MESSAGE = "C2C_MESSAGE_CREATE"           # Private chat message
EVENT_GROUP_AT_MESSAGE = "GROUP_AT_MESSAGE_CREATE"  # Bot @mentioned in a QQ group
EVENT_DIRECT_MESSAGE = "DIRECT_MESSAGE_CREATE"     # Direct message (QQ Guild)

# Intent bit flags
INTENT_GUILDS = 1 << 0
INTENT_GUILD_MEMBERS = 1 << 1
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_GUILD_MESSAGE_REACTIONS = 1 << 10
INTENT_DIRECT_MESSAGE = 1 << 12
INTENT_OPEN_FORUM = 1 << 18
INTENT_AUDIO_LIVE_MEMBER = 1 << 19
INTENT_C2C_MESSAGE = 1 << 28          # Private chat (QQ好友)
INTENT_GROUP_AT_MESSAGE = 1 << 25     # Group @mention (QQ群)
INTENT_INTERACTION = 1 << 26

# Default: private chat + group @mention
DEFAULT_INTENTS = INTENT_C2C_MESSAGE | INTENT_GROUP_AT_MESSAGE | INTENT_GUILD_MESSAGES


# ── Data Models ──────────────────────────────────────────────────────

class QQMessageType(Enum):
    """Types of QQ messages received."""
    PRIVATE = "c2c"          # Private/one-on-one chat
    GROUP_AT = "group_at"    # Group @mention
    GUILD = "guild"          # Guild/channel message
    UNKNOWN = "unknown"


@dataclass
class QQMessage:
    """Normalized QQ Official Bot message."""
    msg_id: str
    message_type: QQMessageType    # PRIVATE, GROUP_AT, or GUILD
    sender_id: str                 # User's QQ open ID
    sender_name: str               # User's display name
    group_id: str                  # For group messages: group open ID
    group_name: str                # Group name
    guild_id: str                  # For guild messages: guild/channel ID
    content: str                   # Text content
    raw_message: str               # Raw message text
    timestamp: int
    raw: Any = None                # Original event dict
    extra: Dict = field(default_factory=dict)


@dataclass
class QQBotInfo:
    """Information about the bot itself."""
    id: str
    name: str
    avatar: str = ""


# ── Main Adapter ─────────────────────────────────────────────────────

class QQQfficialBot:
    """QQ Official Bot adapter using WebSocket gateway.

    Connects to QQ Bot platform, authenticates, and listens for messages.
    Supports private chats (C2C), group @mentions, and guild messages.

    Args:
        app_id: Bot AppID from q.qq.com developer console
        app_secret: Bot AppSecret from q.qq.com developer console
        intents: Bitmask of events to subscribe to (default: C2C + GROUP_AT)
        is_sandbox: Use sandbox environment (default: False)
        auto_reconnect: Auto-reconnect on disconnect (default: True)
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        intents: int = DEFAULT_INTENTS,
        is_sandbox: bool = False,
        auto_reconnect: bool = True,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.intents = intents
        self.is_sandbox = is_sandbox
        self.auto_reconnect = auto_reconnect

        # Token management
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

        # Connection state
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._session_id: str = ""
        self._last_seq: int = 0
        self._heartbeat_interval: float = 0
        self._ws_url: str = ""
        self._shard_count: int = 1
        self._self_id: str = ""
        self._bot_info: Optional[QQBotInfo] = None

        # Callbacks
        self._message_handler: Optional[Callable[[QQMessage], None]] = None
        self._ready_handler: Optional[Callable[[QQBotInfo], None]] = None
        self._error_handler: Optional[Callable[[Exception], None]] = None

        # Stats
        self._stats = {
            "connected_at": None,
            "messages_received": 0,
            "messages_sent": 0,
            "reconnect_count": 0,
            "errors": 0,
        }

        # HTTP session for REST API
        self._http_session = None

    # ── Public API ──────────────────────────────────────────────────

    def set_message_handler(self, handler: Callable[[QQMessage], None]):
        """Set callback for incoming messages."""
        self._message_handler = handler

    def set_ready_handler(self, handler: Callable[[QQBotInfo], None]):
        """Set callback for when bot is ready and connected."""
        self._ready_handler = handler

    def set_error_handler(self, handler: Callable[[Exception], None]):
        """Set callback for errors."""
        self._error_handler = handler

    def start(self):
        """Start the bot (blocking)."""
        # Configure logging to show output
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        print("  🔑 正在获取访问令牌...")
        try:
            self._loop.run_until_complete(self._run())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            print("\n  ⏹  已停止")
        finally:
            self._loop.close()
            self._running = False

    def start_async(self):
        """Start the bot in a background thread (non-blocking)."""
        self._thread = threading.Thread(target=self.start, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the bot."""
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
        if self._thread:
            self._thread.join(timeout=5)

    async def send_message(
        self,
        target_id: str,
        content: str,
        message_type: QQMessageType = QQMessageType.PRIVATE,
        msg_id: str = "",
        media: Optional[Dict] = None,
    ) -> bool:
        """Send a message via REST API.

        Args:
            target_id: For PRIVATE: user's open ID.
                       For GROUP_AT: group's open ID.
                       For GUILD: channel ID.
            content: Text content to send.
            message_type: Type of message destination.
            msg_id: Optional message ID to reply to.
            media: Optional media attachment (from upload_file response).
                   Example: {"file_info": "uuid_string"}

        Returns:
            True if sent successfully.
        """
        await self._ensure_token()

        if message_type == QQMessageType.PRIVATE:
            endpoint = f"/v2/users/{target_id}/messages"
            if media:
                payload = {"content": content, "msg_type": 7, "media": media}
            else:
                payload = {"content": content, "msg_type": 0}
            if msg_id:
                payload["msg_id"] = msg_id
            # Unique msg_seq prevents QQ platform "消息被去重" (40054005)
            payload["msg_seq"] = random.randint(1, 999999999)
        elif message_type == QQMessageType.GROUP_AT:
            endpoint = f"/v2/groups/{target_id}/messages"
            if media:
                payload = {"content": content, "msg_type": 7, "media": media}
            else:
                payload = {"content": content, "msg_type": 0}
            if msg_id:
                payload["msg_id"] = msg_id
            # Unique msg_seq prevents QQ platform "消息被去重" (40054005)
            payload["msg_seq"] = random.randint(1, 999999999)
        elif message_type == QQMessageType.GUILD:
            endpoint = f"/channels/{target_id}/messages"
            payload = {"content": content, "msg_id": msg_id} if msg_id else {"content": content}
        else:
            logger.error(f"Unknown message type: {message_type}")
            return False

        return await self._api_post(endpoint, payload)

    async def upload_file(
        self,
        target_id: str,
        file_data: bytes,
        file_type: int = 4,
        message_type: QQMessageType = QQMessageType.PRIVATE,
        srv_send_msg: bool = False,
    ) -> Optional[str]:
        """Upload a file to QQ media server.

        The file is uploaded but NOT auto-sent (srv_send_msg=False).
        Returns the file_info string for use with send_message(media=...).

        Args:
            target_id: User's open ID (for PRIVATE) or group's open ID (for GROUP_AT).
            file_data: Raw bytes of the file.
            file_type: 1=image, 2=video, 3=voice, 4=file (default).
            message_type: PRIVATE or GROUP_AT.
            srv_send_msg: If True, auto-sends as a proactive message (uses monthly quota).

        Returns:
            file_info string for send_message, or None on failure.
        """
        import base64
        await self._ensure_token()

        if message_type == QQMessageType.PRIVATE:
            endpoint = f"/v2/users/{target_id}/files"
        elif message_type == QQMessageType.GROUP_AT:
            endpoint = f"/v2/groups/{target_id}/files"
        else:
            logger.error(f"File upload not supported for {message_type}")
            return None

        b64_data = base64.b64encode(file_data).decode("ascii")

        try:
            import aiohttp
            url = f"{self._api_base}{endpoint}"
            headers = {
                "Authorization": self._auth_header(),
                "Content-Type": "application/json",
                "X-Union-Appid": self.app_id,
            }
            payload = {
                "file_type": file_type,
                "file_data": b64_data,
                "srv_send_msg": srv_send_msg,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status in (200, 201):
                        result = await resp.json()
                        file_uuid = result.get("file_uuid", "")
                        if file_uuid:
                            logger.info(f"File uploaded: file_uuid={file_uuid}")
                            return file_uuid
                        else:
                            logger.error(f"Upload response missing file_uuid: {result}")
                            return None
                    else:
                        text = await resp.text()
                        logger.error(f"File upload failed: {resp.status} {text}")
                        return None
        except Exception as e:
            logger.error(f"File upload error: {e}")
            return None

    async def send_file(
        self,
        target_id: str,
        file_data: bytes,
        file_type: int = 4,
        message_type: QQMessageType = QQMessageType.PRIVATE,
        msg_id: str = "",
        text_content: str = "",
    ) -> bool:
        """Upload and send a file as a media message (two-step).

        Uses the non-proactive two-step approach:
          1. upload_file(srv_send_msg=False) → file_info
          2. send_message(msg_type=7, media=file_info)

        This is preferred when replying to a user message (uses passive quota).

        Args:
            target_id: User or group open ID.
            file_data: Raw file bytes.
            file_type: 1=image, 2=video, 3=voice, 4=file.
            message_type: PRIVATE or GROUP_AT.
            msg_id: Optional message ID to reply to.
            text_content: Optional text to accompany the file.

        Returns:
            True if sent successfully.
        """
        file_info = await self.upload_file(
            target_id, file_data, file_type,
            message_type, srv_send_msg=False,
        )
        if not file_info:
            logger.error("File upload failed, cannot send")
            return False

        return await self.send_message(
            target_id, text_content, message_type,
            msg_id, media={"file_info": file_info},
        )

    async def reply_with_file(self, msg: QQMessage, file_data: bytes,
                               file_type: int = 4, text_content: str = "") -> bool:
        """Reply to a message with a file attachment (auto-detects type)."""
        if msg.message_type == QQMessageType.PRIVATE:
            return await self.send_file(
                msg.sender_id, file_data, file_type,
                QQMessageType.PRIVATE, msg.msg_id, text_content,
            )
        elif msg.message_type == QQMessageType.GROUP_AT:
            return await self.send_file(
                msg.group_id, file_data, file_type,
                QQMessageType.GROUP_AT, msg.msg_id, text_content,
            )
        logger.error(f"reply_with_file not supported for {msg.message_type}")
        return False

    async def reply_message(self, msg: QQMessage, content: str) -> bool:
        """Reply to a message (auto-detects type)."""
        if msg.message_type == QQMessageType.PRIVATE:
            return await self.send_message(
                msg.sender_id, content,
                QQMessageType.PRIVATE, msg.msg_id,
            )
        elif msg.message_type == QQMessageType.GROUP_AT:
            return await self.send_message(
                msg.group_id, content,
                QQMessageType.GROUP_AT, msg.msg_id,
            )
        elif msg.message_type == QQMessageType.GUILD:
            return await self.send_message(
                msg.guild_id, content,
                QQMessageType.GUILD, msg.msg_id,
            )
        return False

    def get_bot_info(self) -> Optional[QQBotInfo]:
        """Get cached bot info."""
        return self._bot_info

    def get_stats(self) -> Dict:
        """Get connection statistics."""
        return dict(self._stats)

    def get_event_loop(self):
        """Get the async event loop (for scheduling coroutines from sync code)."""
        return self._loop

    def send_proactive(self, to_user: str, content: str, msg_type: QQMessageType = QQMessageType.PRIVATE) -> bool:
        """Send a proactive message (not in reply to a user message).

        Can be called from any thread. Uses run_coroutine_threadsafe.
        """
        if not self.get_event_loop() or not self.get_event_loop().is_running():
            logger.error("Event loop not running, cannot send proactive message")
            return False
        import asyncio
        future = asyncio.run_coroutine_threadsafe(
            self.send_message(to_user, content, msg_type),
            self.get_event_loop(),
        )
        try:
            return future.result(timeout=15)
        except Exception as e:
            logger.error(f"Proactive send failed: {e}")
            return False

    def is_connected(self) -> bool:
        """Check if connected to the gateway."""
        return self._running and self._ws is not None and not self._ws.closed

    # ── Internal: Token Management ─────────────────────────────────

    async def _ensure_token(self):
        """Ensure access token is valid, refresh if needed."""
        if self._access_token is None or time.time() >= self._token_expires_at:
            await self._refresh_token()

    async def _refresh_token(self):
        """Get a new access token from QQ Bot platform."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    TOKEN_URL,
                    json={"appId": self.app_id, "clientSecret": self.app_secret},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json()
                    if "access_token" not in data:
                        raise RuntimeError(f"Token refresh failed: {data}")
                    self._access_token = data["access_token"]
                    self._token_expires_at = time.time() + int(data.get("expires_in", 7200)) - 60
                    logger.info(f"Access token refreshed, expires in {data.get('expires_in')}s")
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            raise

    def _auth_header(self) -> str:
        """Get the Authorization header value."""
        return f"QQBot {self._access_token}"

    @property
    def _api_base(self) -> str:
        """Get the API base URL depending on sandbox mode."""
        return SANDBOX_API_BASE if self.is_sandbox else API_BASE

    # ── Internal: REST API ─────────────────────────────────────────

    async def _api_post(self, endpoint: str, payload: Dict) -> bool:
        """Make a POST request to the QQ Bot API."""
        try:
            import aiohttp
            url = f"{self._api_base}{endpoint}"
            headers = {
                "Authorization": self._auth_header(),
                "Content-Type": "application/json",
                "X-Union-Appid": self.app_id,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 201, 204):
                        self._stats["messages_sent"] += 1
                        return True
                    else:
                        text = await resp.text()
                        logger.error(f"API POST {endpoint} failed: {resp.status} {text}")
                        return False
        except Exception as e:
            logger.error(f"API request error: {e}")
            return False

    async def _api_get(self, endpoint: str) -> Optional[Dict]:
        """Make a GET request to the QQ Bot API."""
        try:
            import aiohttp
            url = f"{self._api_base}{endpoint}"
            headers = {
                "Authorization": self._auth_header(),
                "X-Union-Appid": self.app_id,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        text = await resp.text()
                        logger.error(f"API GET {endpoint} failed: {resp.status} {text}")
                        return None
        except Exception as e:
            logger.error(f"API request error: {e}")
            return None

    # ── Internal: WebSocket Connection ─────────────────────────────

    async def _run(self):
        """Main run loop: get gateway URL, connect, and listen."""
        while self._running:
            try:
                print("  🔑 正在获取访问令牌...")
                await self._ensure_token()
                print("  ✅ Token 获取成功")

                # Get WebSocket gateway URL
                print("  🌐 正在获取 WebSocket 网关地址...")
                gateway_info = await self._api_get("/gateway/bot")
                if not gateway_info:
                    logger.error("Failed to get gateway URL, retrying in 30s...")
                    print("  ❌ 获取网关地址失败，30秒后重试...")
                    await asyncio.sleep(30)
                    continue

                self._ws_url = gateway_info.get("url", "")
                self._shard_count = gateway_info.get("shards", 1)
                if not self._ws_url:
                    logger.error("Empty gateway URL, retrying in 30s...")
                    print("  ❌ 网关地址为空，30秒后重试...")
                    await asyncio.sleep(30)
                    continue

                logger.info(f"Gateway URL: {self._ws_url}, shards: {self._shard_count}")
                print(f"  ✅ 网关地址已获取，正在连接 WebSocket...")

                # Connect WebSocket
                await self._connect_and_listen()

            except Exception as e:
                self._stats["errors"] += 1
                err_msg = str(e)
                logger.error(f"Connection error: {e}")
                print(f"  ❌ 连接错误: {err_msg[:200]}")
                if "401" in err_msg or "403" in err_msg or "4004" in err_msg:
                    print("  ⚠️  可能是 AppID 或 AppSecret 错误，请检查配置")
                    print("  运行: partner qq setup")
                    print("  或在 q.qq.com 确认机器人状态")
                    if not self.auto_reconnect:
                        break
                if self._error_handler:
                    try:
                        self._error_handler(e)
                    except Exception:
                        pass
                if self._running and self.auto_reconnect:
                    logger.info("Retrying in 30s...")
                    print("  ⏳ 30秒后自动重连...")
                    await asyncio.sleep(30)

    async def _connect_and_listen(self):
        """Connect to WebSocket gateway and listen for events."""
        import aiohttp
        from ssl import SSLContext

        logger.info(f"Connecting to WebSocket gateway...")
        print(f"  🔌 正在连接 WebSocket: {self._ws_url[:60]}...")

        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=SSLContext())
        ) as session:
            async with session.ws_connect(self._ws_url) as ws:
                self._ws = ws
                self._stats["connected_at"] = datetime.now().isoformat()
                logger.info("WebSocket connected, waiting for HELLO...")

                async for msg in ws:
                    if not self._running:
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_ws_message(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        error = ws.exception()
                        logger.error(f"WebSocket error: {error}")
                        if self._error_handler:
                            self._error_handler(error)
                        break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                        logger.info(f"WebSocket closed: {ws.close_code}")
                        break

    async def _disconnect(self):
        """Disconnect from WebSocket."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None

    # ── Internal: WS Protocol Handling ─────────────────────────────

    async def _on_ws_message(self, raw: str):
        """Process a single WebSocket message."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {raw[:100]}")
            return

        op = data.get("op")
        event = data.get("t", "")
        seq = data.get("s", 0)

        # Update sequence
        if seq > 0:
            self._last_seq = seq

        # Handle based on opcode
        if op == WS_OPCODE.HELLO:
            # First message: get heartbeat interval
            d = data.get("d", {})
            self._heartbeat_interval = d.get("heartbeat_interval", 30) / 1000.0
            logger.info(f"HELLO received, heartbeat interval: {self._heartbeat_interval}s")
            # Send identify
            if self._session_id:
                await self._ws_resume()
            else:
                await self._ws_identify()
            # Start heartbeat
            asyncio.create_task(self._heartbeat_loop())

        elif op == WS_OPCODE.HEARTBEAT_ACK:
            logger.debug("Heartbeat ACK")

        elif op == WS_OPCODE.DISPATCH:
            await self._handle_dispatch(data)

        elif op == WS_OPCODE.RECONNECT:
            logger.info("Server requested reconnection")
            self._can_reconnect = True
            raise Exception("Server requested reconnect")

        elif op == WS_OPCODE.INVALID_SESSION:
            logger.warning("Invalid session, resetting")
            self._session_id = ""
            self._last_seq = 0

    async def _ws_identify(self):
        """Send identify/authentication message."""
        payload = {
            "op": WS_OPCODE.IDENTIFY,
            "d": {
                "token": self._auth_header(),
                "intents": self.intents,
                "shard": [0, self._shard_count],
                "properties": {
                    "$os": "linux",
                    "$device": "partner-bot",
                    "$browser": "partner",
                },
            },
        }
        await self._send_ws(payload)
        logger.info("Identify sent")

    async def _ws_resume(self):
        """Send resume message for reconnection."""
        payload = {
            "op": WS_OPCODE.RESUME,
            "d": {
                "token": self._auth_header(),
                "session_id": self._session_id,
                "seq": self._last_seq,
            },
        }
        await self._send_ws(payload)
        logger.info("Resume sent")

    async def _heartbeat_loop(self):
        """Send heartbeat at regular intervals."""
        while self._running and self._ws and not self._ws.closed:
            payload = {
                "op": WS_OPCODE.HEARTBEAT,
                "d": self._last_seq,
            }
            await self._send_ws(payload)
            await asyncio.sleep(self._heartbeat_interval)

    async def _send_ws(self, payload: Dict):
        """Send a JSON message over WebSocket."""
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(payload, ensure_ascii=False))

    # ── Internal: Event Dispatch ───────────────────────────────────

    async def _handle_dispatch(self, data: Dict):
        """Handle a dispatch event from the gateway."""
        event = data.get("t", "")

        if event == "READY":
            await self._handle_ready(data.get("d", {}))
        elif event == "RESUMED":
            logger.info("Resumed successfully")
            asyncio.create_task(self._heartbeat_loop())

        # Message events we care about
        if event == EVENT_C2C_MESSAGE:
            await self._handle_c2c_message(data.get("d", {}))
        elif event == EVENT_GROUP_AT_MESSAGE:
            await self._handle_group_at_message(data.get("d", {}))
        elif event == EVENT_AT_MESSAGE:
            await self._handle_at_message(data.get("d", {}))

    async def _handle_ready(self, data: Dict):
        """Handle READY event - bot is connected."""
        self._session_id = data.get("session_id", "")
        user = data.get("user", {})
        self._self_id = str(user.get("id", ""))
        self._bot_info = QQBotInfo(
            id=self._self_id,
            name=user.get("username", ""),
            avatar=user.get("avatar", ""),
        )
        logger.info(f"Bot ready: {self._bot_info.name} (id: {self._bot_info.id})")
        self._stats["reconnect_count"] = 0

        if self._ready_handler:
            try:
                self._ready_handler(self._bot_info)
            except Exception as e:
                logger.error(f"Ready handler error: {e}")

    async def _handle_c2c_message(self, data: Dict):
        """Handle C2C (private/one-on-one) message."""
        author = data.get("author", {})
        content = self._extract_text(data)

        if not content.strip():
            return

        msg = QQMessage(
            msg_id=data.get("id", ""),
            message_type=QQMessageType.PRIVATE,
            sender_id=str(author.get("user_openid", "")),
            sender_name=author.get("name", author.get("user_openid", "Unknown")),
            group_id="",
            group_name="",
            guild_id=data.get("channel_id", ""),
            content=content,
            raw_message=content,
            timestamp=data.get("timestamp", int(time.time())),
            raw=data,
        )
        await self._dispatch_message(msg)

    async def _handle_group_at_message(self, data: Dict):
        """Handle GROUP_AT_MESSAGE (bot @mentioned in group)."""
        author = data.get("author", {})
        content = self._extract_text(data)

        if not content.strip():
            return

        group_info = data.get("group_info", {}) or {}
        group_open_id = data.get("group_openid", group_info.get("group_openid", ""))

        msg = QQMessage(
            msg_id=data.get("id", ""),
            message_type=QQMessageType.GROUP_AT,
            sender_id=str(author.get("user_openid", "")),
            sender_name=author.get("name", author.get("user_openid", "Unknown")),
            group_id=group_open_id,
            group_name=group_info.get("group_name", ""),
            guild_id="",
            content=content,
            raw_message=content,
            timestamp=data.get("timestamp", int(time.time())),
            raw=data,
        )
        await self._dispatch_message(msg)

    async def _handle_at_message(self, data: Dict):
        """Handle AT_MESSAGE (bot @mentioned in guild/channel)."""
        author = data.get("author", {})
        content = self._extract_text(data)

        if not content.strip():
            return

        msg = QQMessage(
            msg_id=data.get("id", ""),
            message_type=QQMessageType.GUILD,
            sender_id=str(author.get("id", "")),
            sender_name=author.get("username", author.get("id", "Unknown")),
            group_id="",
            group_name="",
            guild_id=data.get("channel_id", data.get("guild_id", "")),
            content=content,
            raw_message=content,
            timestamp=data.get("timestamp", int(time.time())),
            raw=data,
        )
        await self._dispatch_message(msg)

    def _extract_text(self, data: Dict) -> str:
        """Extract text content from a message event.

        QQ Bot messages can contain rich content blocks.
        """
        content = data.get("content", "")

        # If content is a list of blocks, extract text from each
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text" or block_type == "paragraph":
                        texts.append(block.get("text", ""))
                    elif block_type == "mention":
                        texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            return " ".join(t for t in texts if t).strip()

        # Simple string content
        if isinstance(content, str):
            return content.strip()

        return str(content) if content else ""

    async def _dispatch_message(self, msg: QQMessage):
        """Dispatch a message to the registered handler."""
        self._stats["messages_received"] += 1
        logger.info(
            f"[QQ {msg.message_type.value}] "
            f"{msg.sender_name}({msg.sender_id}): {msg.content[:100]}"
        )

        if self._message_handler:
            try:
                # Run in executor to avoid blocking the event loop
                if asyncio.iscoroutinefunction(self._message_handler):
                    await self._message_handler(msg)
                else:
                    await asyncio.to_thread(self._message_handler, msg)
            except Exception as e:
                logger.error(f"Message handler error: {e}", exc_info=True)
                self._stats["errors"] += 1
