"""WeChatPad Adapter - WeChat integration via iPad protocol (cross-platform).

WeChatPad is a WeChat iPad protocol implementation that provides REST API
for sending messages and WebSocket for receiving messages. Unlike WeChatFerry,
it does NOT require Windows or a running WeChat desktop client.

Architecture:
  WeChat User ←iPad Protocol→ WeChatPad Server ←HTTP/WS→ This Adapter

This module provides:
  - WechatPadAdapter: low-level message send/receive via REST + WebSocket
  - Message callback registration
  - Login management (QR code, status check, wake-up)
  - Auto-reconnection on WebSocket failure

Prerequisites:
  - WeChatPad server running (Docker or standalone)
  - Valid admin_key or token
  - Python packages: requests, websocket-client

API Reference (from LangBot/wechatpad_api):
  POST /message/SendTextMessage?key=TOKEN
  POST /message/SendImageMessage?key=TOKEN
  POST /message/SendVoice?key=TOKEN
  POST /message/SendAppMessage?key=TOKEN
  POST /message/SendEmojiMessage?key=TOKEN
  POST /message/ShareCardMessage?key=TOKEN
  POST /message/RevokeMsg?key=TOKEN
  GET  /login/GetLoginStatus?key=TOKEN
  POST /login/GetLoginQrCodeNew?key=TOKEN
  POST /login/WakeUpLogin?key=TOKEN
  POST /login/LogOut?key=TOKEN
  POST /admin/GenAuthKey1?key=ADMIN_KEY
  GET  /user/GetProfile?key=TOKEN
  GET  /user/GetSafetyInfo?key=TOKEN
  POST /friend/... (friend management)
  POST /chatroom/... (group management)
  POST /cdn/... (file download)
  WebSocket: WS_URL/GetSyncMsg?key=TOKEN

Usage:
    from partner.wechatpad_adapter import WechatPadAdapter

    adapter = WechatPadAdapter({
        "api_url": "http://127.0.0.1:8080",
        "ws_url": "ws://127.0.0.1:8080",
        "token": "your_token",
        "admin_key": "your_admin_key",  # optional, for token generation
        "wxid": "your_wxid",            # optional, auto-detected
    })
    adapter.start(on_message=lambda msg: print(msg))
    adapter.send_text("wxid_xxx", "Hello!")
    adapter.stop()
"""

import os
import json
import time
import logging
import tempfile
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)


class WeChatPadMsgType(Enum):
    """WeChatPad message types (from protocol documentation)."""
    TEXT = 1
    IMAGE = 3
    VOICE = 34
    VIDEO = 43
    EMOTICON = 47
    FILE_APPMSG = 49       # File via appmsg
    LINK_APPMSG = 49       # Link via appmsg
    SYSTEM = 10000
    RECALLED = 10002


@dataclass
class WechatPadMessage:
    """Normalized message from WeChatPad."""
    msg_id: str
    new_msg_id: str
    msg_type: int
    is_group: bool
    sender: str             # wxid of actual sender
    content: str            # text content
    room_id: str            # "" for private, group wxid for group
    timestamp: int
    is_at_me: bool = False
    at_list: List[str] = field(default_factory=list)
    push_content: str = ""  # notification text
    raw: Any = None         # original event dict
    extra: Dict = field(default_factory=dict)


class WechatPadAdapter:
    """WeChatPad protocol adapter for WeChat integration.

    Uses REST API for sending messages and WebSocket for receiving.
    Cross-platform: works on Linux/macOS/Windows (only needs WeChatPad server).

    Configuration:
        api_url: WeChatPad HTTP API base URL (e.g., "http://127.0.0.1:8080")
        ws_url: WeChatPad WebSocket URL (e.g., "ws://127.0.0.1:8080")
        token: Authentication token
        admin_key: Admin key for token generation (optional)
        wxid: Bot's wxid (optional, auto-detected from profile)
        reconnect_interval: Seconds between reconnection attempts (default: 30)
        request_timeout: HTTP request timeout in seconds (default: 60)
    """

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self._api_url = self.config.get("api_url", "").rstrip("/")
        self._ws_url = self.config.get("ws_url", "").rstrip("/")
        self._token = self.config.get("token", "")
        self._admin_key = self.config.get("admin_key", "")
        self._wxid = self.config.get("wxid", "")
        self._reconnect_interval = self.config.get("reconnect_interval", 30)
        self._request_timeout = self.config.get("request_timeout", 60)

        self._on_message: Optional[Callable[[WechatPadMessage], None]] = None
        self._running = False
        self._ws = None
        self._loop = None
        self._thread = None
        self._self_nickname = ""

        # Statistics
        self._stats = {
            "messages_received": 0,
            "messages_sent": 0,
            "errors": 0,
            "reconnects": 0,
        }

    # ── Lifecycle ────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if required packages are installed."""
        try:
            import requests
            return True
        except ImportError:
            return False

    def start(self, on_message: Callable[[WechatPadMessage], None]):
        """Start listening for WeChat messages.

        Args:
            on_message: Callback invoked for each received message.

        Raises:
            RuntimeError: If requests is not installed or connection fails.
        """
        if not self.is_available():
            raise RuntimeError(
                "requests package not installed. Install with:\n"
                "  pip install requests websocket-client"
            )

        if not self._api_url:
            raise RuntimeError("WeChatPad api_url not configured")

        self._on_message = on_message
        self._running = True

        # Ensure we have a valid token
        self._ensure_token()

        # Get bot profile (wxid, nickname)
        self._fetch_profile()

        # Start WebSocket listener in background thread
        self._thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self._thread.start()
        logger.info(f"WechatPad adapter started, wxid={self._wxid}")

    def stop(self):
        """Stop listening and disconnect."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("WechatPad adapter stopped")

    # ── Message Sending (REST API) ───────────────────────────────

    def send_text(self, to_wxid: str, text: str, at_list: List[str] = None) -> bool:
        """Send a text message.

        Args:
            to_wxid: Recipient wxid (user or group)
            text: Text content
            at_list: List of wxids to @mention (for group messages)

        Returns:
            True if sent successfully
        """
        url = f"{self._api_url}/message/SendTextMessage"
        json_data = {
            "MsgItem": [{
                "AtWxIDList": at_list or [],
                "ImageContent": "",
                "MsgType": 0,
                "TextContent": text,
                "ToUserName": to_wxid,
            }]
        }
        result = self._post_json(url, json_data)
        if result and result.get("Code") == 200:
            self._stats["messages_sent"] += 1
            logger.debug(f"Sent text to {to_wxid}: {text[:50]}...")
            return True
        logger.warning(f"send_text failed: {result}")
        return False

    def send_image(self, to_wxid: str, image_url: str, at_list: List[str] = None) -> bool:
        """Send an image message.

        Args:
            to_wxid: Recipient wxid
            image_url: Image URL or base64 data
            at_list: List of wxids to @mention
        """
        url = f"{self._api_url}/message/SendImageMessage"
        json_data = {
            "MsgItem": [{
                "AtWxIDList": at_list or [],
                "ImageContent": image_url,
                "MsgType": 0,
                "TextContent": "",
                "ToUserName": to_wxid,
            }]
        }
        result = self._post_json(url, json_data)
        if result and result.get("Code") == 200:
            self._stats["messages_sent"] += 1
            return True
        logger.warning(f"send_image failed: {result}")
        return False

    def send_voice(self, to_wxid: str, voice_data: str,
                   voice_format: int = 0, voice_duration: int = 0) -> bool:
        """Send a voice message.

        Args:
            to_wxid: Recipient wxid
            voice_data: Base64 encoded voice data
            voice_format: Voice format (0=amr, 1=silk, etc.)
            voice_duration: Duration in seconds
        """
        url = f"{self._api_url}/message/SendVoice"
        json_data = {
            "ToUserName": to_wxid,
            "VoiceData": voice_data,
            "VoiceFormat": voice_format,
            "VoiceSecond": voice_duration,
        }
        result = self._post_json(url, json_data)
        if result and result.get("Code") == 200:
            self._stats["messages_sent"] += 1
            return True
        logger.warning(f"send_voice failed: {result}")
        return False

    def send_app_msg(self, to_wxid: str, xml_data: str, content_type: int = 0) -> bool:
        """Send an app message (link, file, mini-program, etc.)."""
        url = f"{self._api_url}/message/SendAppMessage"
        json_data = {
            "AppList": [{
                "ContentType": content_type,
                "ContentXML": xml_data,
                "ToUserName": to_wxid,
            }]
        }
        result = self._post_json(url, json_data)
        return result is not None and result.get("Code") == 200

    def send_emoji(self, to_wxid: str, emoji_md5: str, emoji_size: int = 0) -> bool:
        """Send an emoji message."""
        url = f"{self._api_url}/message/SendEmojiMessage"
        json_data = {
            "EmojiList": [{
                "EmojiMd5": emoji_md5,
                "EmojiSize": emoji_size,
                "ToUserName": to_wxid,
            }]
        }
        result = self._post_json(url, json_data)
        return result is not None and result.get("Code") == 200

    def revoke_msg(self, to_wxid: str, msg_id: str,
                   new_msg_id: str, create_time: int) -> bool:
        """Revoke a sent message."""
        url = f"{self._api_url}/message/RevokeMsg"
        json_data = {
            "ClientMsgId": msg_id,
            "CreateTime": create_time,
            "NewMsgId": new_msg_id,
            "ToUserName": to_wxid,
        }
        result = self._post_json(url, json_data)
        return result is not None and result.get("Code") == 200

    # ── Login & Profile ──────────────────────────────────────────

    def get_login_qr(self, proxy: str = "") -> Optional[Dict]:
        """Get login QR code data."""
        url = f"{self._api_url}/login/GetLoginQrCodeNew"
        json_data = {"Check": bool(proxy), "Proxy": proxy}
        return self._post_json(url, json_data)

    def get_login_status(self) -> Optional[Dict]:
        """Get current login status."""
        url = f"{self._api_url}/login/GetLoginStatus"
        return self._get_json(url)

    def wake_up_login(self, proxy: str = "") -> Optional[Dict]:
        """Wake up an existing login session."""
        url = f"{self._api_url}/login/WakeUpLogin"
        json_data = {"Check": bool(proxy), "Proxy": ""}
        return self._post_json(url, json_data)

    def logout(self) -> Optional[Dict]:
        """Logout current session."""
        url = f"{self._api_url}/login/LogOut"
        return self._post_json(url, {})

    def get_profile(self) -> Optional[Dict]:
        """Get bot's user profile."""
        url = f"{self._api_url}/user/GetProfile"
        return self._get_json(url)

    def get_self_wxid(self) -> str:
        """Get the bot's wxid."""
        return self._wxid

    def get_self_nickname(self) -> str:
        """Get the bot's nickname."""
        return self._self_nickname

    def get_chatroom_member_detail(self, chatroom_name: str) -> Optional[Dict]:
        """Get group member details."""
        url = f"{self._api_url}/chatroom/GetChatroomMemberDetail"
        return self._post_json(url, {"ChatroomName": chatroom_name})

    # ── File Operations ──────────────────────────────────────────

    def cdn_download(self, aeskey: str, file_type: int, file_url: str) -> Optional[Dict]:
        """Download file via CDN."""
        url = f"{self._api_url}/cdn/SendCDNDownload"
        json_data = {
            "AesKey": aeskey,
            "FileType": file_type,
            "FileUrl": file_url,
        }
        return self._post_json(url, json_data)

    def get_msg_voice(self, buf_id: str, length: int, msg_id: str) -> Optional[Dict]:
        """Download voice message."""
        url = f"{self._api_url}/cdn/GetMsgVoice"
        json_data = {
            "BufId": buf_id,
            "Length": length,
            "MsgId": msg_id,
        }
        return self._post_json(url, json_data)

    # ── Statistics ───────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get adapter statistics."""
        return {**self._stats, "wxid": self._wxid, "connected": self._ws is not None}

    # ── Internal: HTTP Client ────────────────────────────────────

    def _post_json(self, url: str, data: Dict = None) -> Optional[Dict]:
        """Send a POST request with JSON body."""
        import requests
        headers = {"Content-Type": "application/json"}
        full_url = f"{url}?key={self._token}"
        try:
            response = requests.post(
                full_url, json=data, headers=headers,
                timeout=self._request_timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"POST {url} failed: {e}")
            self._stats["errors"] += 1
            return None

    def _get_json(self, url: str) -> Optional[Dict]:
        """Send a GET request."""
        import requests
        headers = {"Content-Type": "application/json"}
        full_url = f"{url}?key={self._token}"
        try:
            response = requests.get(
                full_url, headers=headers,
                timeout=self._request_timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"GET {url} failed: {e}")
            self._stats["errors"] += 1
            return None

    # ── Internal: Token Management ───────────────────────────────

    def _ensure_token(self):
        """Ensure we have a valid token. Generate one if needed."""
        if self._token:
            # Check if existing token is still valid
            status = self.get_login_status()
            if status and status.get("Code") == 200:
                logger.info("Existing token is valid")
                return
            elif status and status.get("Code") == 300:
                logger.info("Token expired, generating new one")

        if not self._admin_key:
            raise RuntimeError(
                "No token and no admin_key configured. "
                "Provide either 'token' or 'admin_key' in config."
            )

        # Generate new token
        url = f"{self._api_url}/admin/GenAuthKey1"
        json_data = {"Count": 1, "Days": 365}
        result = self._post_json(url, json_data)
        if result and result.get("Code") == 200:
            tokens = result.get("Data", [])
            if tokens:
                self._token = tokens[0]
                logger.info(f"Generated new token: {self._token[:8]}...")
                return

        raise RuntimeError(f"Failed to generate token: {result}")

    def _fetch_profile(self):
        """Fetch bot profile to get wxid and nickname."""
        profile = self.get_profile()
        if profile and profile.get("Code") == 200:
            data = profile.get("Data", {})
            user_info = data.get("userInfo", {})
            if not self._wxid:
                self._wxid = user_info.get("userName", {}).get("str", "")
            self._self_nickname = user_info.get("nickName", {}).get("str", "")
            logger.info(f"Profile: wxid={self._wxid}, nickname={self._self_nickname}")
        else:
            logger.warning(f"Failed to fetch profile: {profile}")

    # ── Internal: WebSocket Message Loop ─────────────────────────

    def _run_ws_loop(self):
        """Run WebSocket connection loop in a background thread."""
        import websocket

        while self._running:
            try:
                uri = f"{self._ws_url}/GetSyncMsg?key={self._token}"
                logger.info(f"Connecting to WebSocket: {uri}")

                def on_message(ws, message):
                    try:
                        data = json.loads(message, strict=False)
                        self._handle_ws_event(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Non-JSON WS message: {message[:100]}")

                def on_error(ws, error):
                    logger.error(f"WebSocket error: {str(error)[:200]}")

                def on_close(ws, close_status_code, close_msg):
                    logger.info("WebSocket closed")

                def on_open(ws):
                    logger.info("WebSocket connected")

                self._ws = websocket.WebSocketApp(
                    uri,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_open=on_open,
                )
                self._ws.run_forever(ping_interval=60, ping_timeout=20)

            except Exception as e:
                logger.error(f"WebSocket loop error: {e}")

            if self._running:
                self._stats["reconnects"] += 1
                logger.info(f"Reconnecting in {self._reconnect_interval}s...")
                time.sleep(self._reconnect_interval)

    def _handle_ws_event(self, data: Dict):
        """Handle an incoming WebSocket event (message push)."""
        # WeChatPad pushes messages as raw WeChat protocol objects
        # Key fields: from_user_name, content, msg_type, create_time, etc.
        try:
            msg = self._normalize_message(data)
            if msg and self._on_message:
                self._stats["messages_received"] += 1
                self._on_message(msg)
        except Exception as e:
            logger.error(f"Error handling WS event: {e}", exc_info=True)
            self._stats["errors"] += 1

    def _normalize_message(self, data: Dict) -> Optional[WechatPadMessage]:
        """Convert a raw WeChatPad event to our WechatPadMessage format."""
        try:
            from_user = data.get("from_user_name", {})
            from_user_str = from_user.get("str", "") if isinstance(from_user, dict) else str(from_user)

            content = data.get("content", {})
            content_str = content.get("str", "") if isinstance(content, dict) else str(content)

            to_user = data.get("to_user_name", {})
            to_user_str = to_user.get("str", "") if isinstance(to_user, dict) else str(to_user)

            msg_type = data.get("msg_type", 0)
            create_time = data.get("create_time", int(time.time()))
            new_msg_id = str(data.get("new_msg_id", ""))
            msg_id = str(data.get("msg_id", ""))

            # Determine if group message
            is_group = from_user_str.endswith("@chatroom")

            # Extract actual sender from content prefix (group messages)
            sender = from_user_str
            if is_group and ":" in content_str:
                parts = content_str.split(":", 1)
                if len(parts) == 2 and len(parts[0]) < 30:
                    sender = parts[0].strip()
                    content_str = parts[1].strip()

            # Check @mention
            is_at_me = False
            at_list = []
            push_content = data.get("push_content", "") or ""
            msg_source = data.get("msg_source", "") or ""

            if is_group:
                # Check push_content for @notification
                if "在群聊中@了你" in push_content:
                    is_at_me = True

                # Parse msg_source for atuserlist
                if msg_source:
                    try:
                        ms_xml = ET.fromstring(msg_source)
                        at_users = ms_xml.findtext("atuserlist", "")
                        if at_users:
                            at_list = [u.strip() for u in at_users.split(",") if u.strip()]
                            if self._wxid in at_list:
                                is_at_me = True
                    except ET.ParseError:
                        pass

            return WechatPadMessage(
                msg_id=msg_id,
                new_msg_id=new_msg_id,
                msg_type=msg_type,
                is_group=is_group,
                sender=sender,
                content=content_str,
                room_id=from_user_str if is_group else "",
                timestamp=create_time,
                is_at_me=is_at_me,
                at_list=at_list,
                push_content=push_content,
                raw=data,
            )

        except Exception as e:
            logger.error(f"Failed to normalize message: {e}", exc_info=True)
            return None
