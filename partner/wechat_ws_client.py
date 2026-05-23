"""WeChat WebSocket Client for WSL/Linux

Connects to the Windows WeChatFerry bridge via WebSocket.
Routes messages through Partner's conversation engine.

Usage:
    from partner.wechat_ws_client import WeChatWSClient
    
    client = WeChatWSClient(
        workspace="/mnt/e/work/study_room",
        ws_url="ws://192.168.1.100:8765",
    )
    client.start()
"""

import asyncio
import json
import os
import logging
from typing import Optional

try:
    import websockets
except ImportError:
    raise ImportError("websockets not installed. Install: pip install websockets")

from .conversation import ConversationEngine
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal
from .state import StateManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class WeChatWSClient:
    """WebSocket client connecting to Windows WeChatFerry bridge."""
    
    def __init__(
        self,
        workspace: str,
        ws_url: str = "ws://localhost:8765",
        voice_enabled: bool = True,
    ):
        self.workspace = workspace
        self.ws_url = ws_url
        self.voice_enabled = voice_enabled
        
        # Initialize Partner components
        state_dir = os.path.join(workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        
        self.task_queue = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        self.knowledge = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        self.state_manager = StateManager(state_dir)
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state_manager
        )
        
        self._ws = None
        self._running = False
        self._user_cache = {}  # wxid -> name cache
    
    def start(self):
        """Start the client (blocking)."""
        self._running = True
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self._running = False
    
    async def _run(self):
        """Main connection loop with reconnection."""
        while self._running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    logger.info(f"Connected to {self.ws_url}")
                    
                    async for message in ws:
                        await self._handle_message(message)
                        
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Connection closed, reconnecting in 5s...")
                await asyncio.sleep(5)
            except ConnectionRefusedError:
                logger.warning(f"Connection refused, retrying in 10s...")
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error: {e}, retrying in 10s...")
                await asyncio.sleep(10)
    
    async def _handle_message(self, raw: str):
        """Handle incoming message from bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON: {raw[:100]}")
            return
        
        msg_type = data.get("type")
        
        if msg_type == "connected":
            user = data.get("user", {})
            logger.info(f"Bridge connected. WeChat user: {user.get('name', 'unknown')}")
            return
        
        if msg_type == "message":
            await self._process_wechat_message(data)
            return
        
        if msg_type == "error":
            logger.error(f"Bridge error: {data.get('message')}")
            return
        
        # Other types (sent, contacts, etc.) - just log
        logger.debug(f"Received: {msg_type}")
    
    async def _process_wechat_message(self, data: dict):
        """Process a WeChat message through Partner."""
        sender = data.get("sender", "")
        content = data.get("content", "")
        roomid = data.get("roomid", "")
        is_group = data.get("is_group", False)
        is_voice = data.get("is_voice", False)
        
        # Skip empty messages
        if not content:
            return
        
        # In groups, only respond when @mentioned
        if is_group:
            # TODO: Check if @mentioned
            # For now, skip group messages
            logger.debug(f"Skipping group message from {sender}")
            return
        
        # Get sender name
        sender_name = self._user_cache.get(sender, sender)
        
        logger.info(f"Message from {sender_name}: {content[:80]}...")
        
        # Process through conversation engine
        try:
            response = await asyncio.to_thread(
                self.conversation.chat,
                user_message=content,
                user_id=sender,
                user_name=sender_name,
            )
            
            if response:
                # Send reply
                await self._send_reply(sender, response)
                logger.info(f"Replied to {sender_name}: {response[:80]}...")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await self._send_reply(sender, "抱歉，处理消息时出错了。请稍后再试。")
    
    async def _send_reply(self, to: str, content: str):
        """Send reply through bridge."""
        if not self._ws:
            return
        
        # Truncate long messages
        if len(content) > 2000:
            content = content[:1997] + "..."
        
        await self._ws.send(json.dumps({
            "type": "send_text",
            "to": to,
            "content": content,
        }, ensure_ascii=False))


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="WeChat WebSocket Client")
    parser.add_argument("--workspace", "-w", required=True, help="Partner workspace")
    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=8765, help="Bridge port")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice")
    args = parser.parse_args()
    
    client = WeChatWSClient(
        workspace=args.workspace,
        ws_url=f"ws://{args.host}:{args.port}",
        voice_enabled=not args.no_voice,
    )
    client.start()


if __name__ == "__main__":
    main()
