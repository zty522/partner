"""NapCat QQ Adapter - QQ integration via OneBot 11 WebSocket protocol.

NapCat is a NTQQ-based bot protocol implementation that exposes OneBot 11 API.
This adapter connects to NapCat's WebSocket endpoint and provides message
send/receive functionality.

Architecture:
  QQ NTQQ Client ←→ NapCat (protocol server) ←WebSocket/OneBot 11→ This Adapter

Prerequisites:
  - NapCat running and configured (https://github.com/NapNeko/NapCatQQ)
  - NapCat WebSocket forward or reverse WebSocket configured
  - Python websockets package: pip install websockets

OneBot 11 Reference:
  - https://github.com/botuniverse/onebot-11
  - Message types: private, group
  - Post format: array (recommended) or string

Usage:
    from partner.qq_napcat import NapCatAdapter

    adapter = NapCatAdapter({
        "ws_url": "ws://127.0.0.1:3001",
        "access_token": "optional_token",
    })
    adapter.start(on_message=lambda msg: print(msg))
    adapter.send_text("123456", "Hello!")
    adapter.stop()
"""

import os
import json
import time
import asyncio
import logging
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)


class OB11MessageType(Enum):
    """OneBot 11 message event sub-types."""
    TEXT = "text"
    IMAGE = "image"
    VOICE = "record"
    VIDEO = "video"
    FILE = "file"
    AT = "at"
    FACE = "face"
    REPLY = "reply"
    JSON = "json"
    XML = "xml"


@dataclass
class QQMessage:
    """Normalized message from NapCat/OneBot 11."""
    msg_id: str
    message_type: str         # "private" or "group"
    sub_type: str             # "friend", "group", etc.
    sender_id: str            # QQ number of sender
    sender_name: str          # Display name
    group_id: str             # "" for private chat, group_id for group
    content: str              # Text content (or transcribed voice)
    raw_message: str          # Raw CQ code message
    timestamp: int
    is_at_me: bool = False
    at_list: List[str] = field(default_factory=list)
    raw: Any = None           # Original event dict
    extra: Dict = field(default_factory=dict)


class NapCatAdapter:
    """NapCat QQ adapter using OneBot 11 WebSocket protocol.

    Connects to NapCat's forward WebSocket endpoint and handles:
    - Message receiving (private + group)
    - Text/image/voice/file sending
    - Group member info and @mention detection
    - Auto-reconnection on connection loss

    Configuration:
        ws_url: WebSocket URL (e.g., "ws://127.0.0.1:3001")
        access_token: Optional OneBot access token
        reconnect_interval: Seconds between reconnection attempts (default: 30)
        group_at_only: Only respond in groups when @mentioned (default: True)
        audio_dir: Directory for voice files (default: temp dir)
    """

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self._ws_url = self.config.get("ws_url", "ws://127.0.0.1:3001")
        self._access_token = self.config.get("access_token", "")
        self._reconnect_interval = self.config.get("reconnect_interval", 30)
        self._group_at_only = self.config.get("group_at_only", True)
        self._audio_dir = self.config.get("audio_dir") or os.path.join(
            tempfile.gettempdir(), "partner_qq_audio"
        )
        os.makedirs(self._audio_dir, exist_ok=True)

        self._on_message: Optional[Callable[[QQMessage], None]] = None
        self._running = False
        self._ws = None
        self._loop = None
        self._thread = None
        self._self_id = ""
        self._seq = 0
        self._pending_responses: Dict[int, asyncio.Future] = {}

    # ── Lifecycle ────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if websockets package is installed."""
        try:
            import websockets
            return True
        except ImportError:
            return False

    def start(self, on_message: Callable[[QQMessage], None]):
        """Start listening for QQ messages.

        Args:
            on_message: Callback invoked for each received message.

        Raises:
            RuntimeError: If websockets is not installed or connection fails.
        """
        if not self.is_available():
            raise RuntimeError(
                "websockets package not installed. Install with:\n"
                "  pip install websockets"
            )

        self._on_message = on_message
        self._running = True

        # Run event loop in a background thread
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"NapCat adapter thread started, connecting to {self._ws_url}")

    def stop(self):
        """Stop listening and disconnect."""
        self._running = False
        if self._loop and self._loop.is_running():
            # Schedule disconnect
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("NapCat adapter stopped")

    # ── Message Sending ──────────────────────────────────────────

    def send_text(self, target: str, text: str, is_group: bool = True) -> bool:
        """Send a text message.

        Args:
            target: Group ID or user QQ number
            text: Text content
            is_group: True if sending to a group, False for private

        Returns:
            True if sent successfully
        """
        if is_group:
            action = "send_group_msg"
            params = {"group_id": target, "message": text}
        else:
            action = "send_private_msg"
            params = {"user_id": target, "message": text}

        result = self._send_action(action, params)
        if result is not None:
            logger.debug(f"Sent text to {'group' if is_group else 'user'} {target}: {text[:50]}...")
            return True
        return False

    def send_image(self, target: str, image_path: str, is_group: bool = True) -> bool:
        """Send an image message.

        Args:
            target: Group ID or user QQ number
            image_path: Path to image file or URL
            is_group: True for group, False for private
        """
        if image_path.startswith(("http://", "https://")):
            cq_image = f"[CQ:image,file={image_path}]"
        else:
            # Convert to base64 or file URI
            import base64
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(image_path)[1].lstrip(".")
            cq_image = f"[CQ:image,file=base64://{b64}]"

        if is_group:
            result = self._send_action("send_group_msg", {
                "group_id": target, "message": cq_image,
            })
        else:
            result = self._send_action("send_private_msg", {
                "user_id": target, "message": cq_image,
            })
        return result is not None

    def send_voice(self, target: str, audio_path: str, is_group: bool = True) -> bool:
        """Send a voice message.

        Args:
            target: Group ID or user QQ number
            audio_path: Path to audio file (will be base64 encoded)
            is_group: True for group, False for private
        """
        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return False

        import base64
        with open(audio_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        cq_voice = f"[CQ:record,file=base64://{b64}]"

        if is_group:
            result = self._send_action("send_group_msg", {
                "group_id": target, "message": cq_voice,
            })
        else:
            result = self._send_action("send_private_msg", {
                "user_id": target, "message": cq_voice,
            })
        return result is not None

    def send_file(self, target: str, file_path: str, is_group: bool = True) -> bool:
        """Send a file message."""
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False

        import base64
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        fname = os.path.basename(file_path)
        cq_file = f"[CQ:file,file=base64://{b64},name={fname}]"

        if is_group:
            result = self._send_action("send_group_msg", {
                "group_id": target, "message": cq_file,
            })
        else:
            result = self._send_action("send_private_msg", {
                "user_id": target, "message": cq_file,
            })
        return result is not None

    # ── Info Queries ─────────────────────────────────────────────

    def get_self_id(self) -> str:
        """Get the bot's QQ number."""
        return self._self_id

    def get_group_list(self) -> List[Dict]:
        """Get list of groups the bot is in."""
        result = self._send_action("get_group_list", {})
        return result if result else []

    def get_group_member_list(self, group_id: str) -> List[Dict]:
        """Get members of a group."""
        result = self._send_action("get_group_member_list", {"group_id": group_id})
        return result if result else []

    def get_group_member_info(self, group_id: str, user_id: str) -> Optional[Dict]:
        """Get info about a specific group member."""
        result = self._send_action("get_group_member_info", {
            "group_id": group_id, "user_id": user_id,
        })
        return result

    def get_friend_list(self) -> List[Dict]:
        """Get the bot's friend list."""
        result = self._send_action("get_friend_list", {})
        return result if result else []

    # ── Internal: Event Loop ─────────────────────────────────────

    def _run_loop(self):
        """Run the async event loop in a thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            logger.error(f"Event loop error: {e}")
        finally:
            self._loop.close()

    async def _connect_loop(self):
        """Reconnection loop."""
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                if self._running:
                    logger.warning(f"WebSocket connection lost: {e}")
                    logger.info(f"Reconnecting in {self._reconnect_interval}s...")
                    await asyncio.sleep(self._reconnect_interval)
                else:
                    break

    async def _connect_and_listen(self):
        """Connect to NapCat WebSocket and listen for events."""
        import websockets

        headers = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        logger.info(f"Connecting to {self._ws_url}...")

        async with websockets.connect(
            self._ws_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=10,
        ) as ws:
            self._ws = ws
            logger.info("Connected to NapCat WebSocket")

            # Get bot info
            await self._get_self_info()

            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    data = json.loads(raw_msg, strict=False)
                    await self._handle_event(data)
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON message received: {raw_msg[:100]}")
                except Exception as e:
                    logger.error(f"Event handling error: {e}")

    async def _disconnect(self):
        """Disconnect from WebSocket."""
        if self._ws:
            await self._ws.close()
            self._ws = None

    # ── Internal: Event Handling ─────────────────────────────────

    async def _handle_event(self, data: Dict):
        """Handle an incoming OneBot 11 event."""
        post_type = data.get("post_type", "")

        if post_type == "message":
            await self._handle_message_event(data)
        elif post_type == "meta_event":
            await self._handle_meta_event(data)
        elif post_type == "request":
            await self._handle_request_event(data)
        elif "echo" in data:
            # This is a response to an action we sent
            await self._handle_action_response(data)

    async def _handle_message_event(self, data: Dict):
        """Handle a message event (private or group)."""
        message_type = data.get("message_type", "")
        sub_type = data.get("sub_type", "")

        # Extract sender info
        sender = data.get("sender", {})
        sender_id = str(sender.get("user_id", ""))
        sender_name = sender.get("card") or sender.get("nickname") or sender_id
        group_id = str(data.get("group_id", "")) if message_type == "group" else ""

        # Extract text from message segments
        message_segments = data.get("message", [])
        raw_message = data.get("raw_message", "")

        # Build text from segments
        text_parts = []
        at_list = []
        is_at_me = False

        for seg in message_segments:
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {})

            if seg_type == "text":
                text_parts.append(seg_data.get("text", "").strip())
            elif seg_type == "at":
                at_qq = str(seg_data.get("qq", ""))
                at_list.append(at_qq)
                if at_qq == self._self_id or at_qq == "all":
                    is_at_me = True
            elif seg_type == "image":
                text_parts.append("[图片]")
            elif seg_type == "record":
                text_parts.append("[语音]")
            elif seg_type == "video":
                text_parts.append("[视频]")
            elif seg_type == "file":
                fname = seg_data.get("file", "文件")
                text_parts.append(f"[文件: {fname}]")
            elif seg_type == "face":
                text_parts.append("[表情]")
            elif seg_type == "reply":
                pass  # Skip reply markers
            elif seg_type == "json":
                text_parts.append("[JSON卡片]")
            elif seg_type == "xml":
                text_parts.append("[XML消息]")

        content = " ".join(p for p in text_parts if p)

        # Skip empty messages
        if not content.strip():
            return

        # Group: only respond when @mentioned (if configured)
        if message_type == "group" and self._group_at_only and not is_at_me:
            return

        msg = QQMessage(
            msg_id=str(data.get("message_id", "")),
            message_type=message_type,
            sub_type=sub_type,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            content=content,
            raw_message=raw_message,
            timestamp=data.get("time", int(time.time())),
            is_at_me=is_at_me,
            at_list=at_list,
            raw=data,
        )

        # Dispatch to callback
        if self._on_message:
            try:
                self._on_message(msg)
            except Exception as e:
                logger.error(f"Error in message callback: {e}")

    async def _handle_meta_event(self, data: Dict):
        """Handle meta events (lifecycle, heartbeat)."""
        meta_type = data.get("meta_event_type", "")
        if meta_type == "lifecycle":
            sub_type = data.get("sub_type", "")
            logger.info(f"Lifecycle event: {sub_type}")
        elif meta_type == "heartbeat":
            logger.debug("Heartbeat received")

    async def _handle_request_event(self, data: Dict):
        """Handle request events (friend request, group invite)."""
        request_type = data.get("request_type", "")
        logger.info(f"Request event: {request_type}")

        # Auto-approve friend requests (configurable)
        if request_type == "friend" and self.config.get("auto_approve_friend", False):
            self._send_action("set_friend_add_request", {
                "flag": data.get("flag", ""),
                "approve": True,
            })

    async def _handle_action_response(self, data: Dict):
        """Handle response to an action we sent."""
        echo = data.get("echo")
        if echo is not None and echo in self._pending_responses:
            future = self._pending_responses.pop(echo)
            if not future.done():
                future.set_result(data)

    # ── Internal: Action Sending ─────────────────────────────────

    def _send_action(self, action: str, params: Dict = None) -> Any:
        """Send an action to NapCat and wait for response.

        This is a synchronous wrapper around the async WebSocket send.
        """
        if not self._loop or not self._ws:
            logger.error("Not connected to NapCat")
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._send_action_async(action, params or {}),
            self._loop,
        )
        try:
            result = future.result(timeout=10)
            if result and result.get("status") == "ok":
                return result.get("data")
            elif result:
                logger.warning(f"Action {action} failed: {result}")
            return None
        except Exception as e:
            logger.error(f"Action {action} timeout/error: {e}")
            return None

    async def _send_action_async(self, action: str, params: Dict) -> Dict:
        """Send an action asynchronously."""
        self._seq += 1
        payload = {
            "action": action,
            "params": params,
            "echo": self._seq,
        }

        future = asyncio.get_event_loop().create_future()
        self._pending_responses[self._seq] = future

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=10)
        except asyncio.TimeoutError:
            self._pending_responses.pop(self._seq, None)
            return {"status": "failed", "retcode": "timeout"}
        except Exception as e:
            self._pending_responses.pop(self._seq, None)
            return {"status": "failed", "retcode": str(e)}

    async def _get_self_info(self):
        """Get the bot's own info after connecting."""
        result = await self._send_action_async("get_login_info", {})
        if result and result.get("status") == "ok":
            data = result.get("data", {})
            self._self_id = str(data.get("user_id", ""))
            logger.info(f"Bot logged in as: {data.get('nickname', '')} ({self._self_id})")
