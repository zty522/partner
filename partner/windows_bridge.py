"""Windows WeChatFerry WebSocket Bridge

Runs on Windows, exposes WeChatFerry via WebSocket for WSL/Linux clients.

Usage (Windows PowerShell):
    python -m partner.windows_bridge
    python -m partner.windows_bridge --port 8765

Then from WSL/Linux:
    partner wechat --host <windows-ip>
"""

import asyncio
import json
import sys
import logging
from typing import Optional

try:
    import websockets
except ImportError:
    print("Error: websockets not installed")
    print("Install: pip install websockets")
    sys.exit(1)

try:
    from wcferry import Wcf, WxMsg
except ImportError:
    print("Error: wcferry not installed")
    print("Install: pip install wcferry")
    print("Note: WeChatFerry requires Windows + specific WeChat version")
    sys.exit(1)


logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class WeChatFerryBridge:
    """WebSocket bridge for WeChatFerry."""
    
    def __init__(self, port: int = 8765):
        self.port = port
        self.wcf: Optional[Wcf] = None
        self.clients = set()
        self._running = False
    
    def start(self):
        """Start the bridge."""
        logger.info("Starting WeChatFerry...")
        self.wcf = Wcf()
        
        if not self.wcf.is_login():
            logger.error("WeChat not logged in. Please login to WeChat first.")
            return
        
        user = self.wcf.get_user_info()
        logger.info(f"Logged in as: {user.get('name', 'unknown')} ({user.get('wxid', '')})")
        
        # Enable message receiving
        self.wcf.enable_revoke_msg()
        self.wcf.enable_recv_msg()
        
        logger.info(f"Starting WebSocket server on port {self.port}...")
        self._running = True
        
        try:
            asyncio.run(self._serve())
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self._cleanup()
    
    async def _serve(self):
        """Run WebSocket server."""
        async with websockets.serve(
            self._handle_client,
            "0.0.0.0",
            self.port,
            ping_interval=30,
            ping_timeout=10,
        ):
            logger.info(f"Bridge ready at ws://0.0.0.0:{self.port}")
            logger.info("Waiting for WSL/Linux client to connect...")
            
            # Keep running and forward messages
            while self._running:
                try:
                    # Check for new WeChat messages
                    if self.wcf and self.wcf.msgQ.empty() is False:
                        msg = self.wcf.msgQ.get(timeout=0.1)
                        await self._forward_message(msg)
                    else:
                        await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error: {e}")
                    await asyncio.sleep(1)
    
    async def _handle_client(self, websocket, path):
        """Handle WebSocket client connection."""
        self.clients.add(websocket)
        logger.info(f"Client connected: {websocket.remote_address}")
        
        try:
            # Send welcome
            await websocket.send(json.dumps({
                "type": "connected",
                "user": self.wcf.get_user_info() if self.wcf else {},
            }))
            
            # Listen for commands from client
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._handle_command(websocket, data)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "Invalid JSON"
                    }))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            logger.info(f"Client disconnected: {websocket.remote_address}")
    
    async def _handle_command(self, websocket, data: dict):
        """Handle command from client."""
        cmd = data.get("type")
        
        if cmd == "send_text":
            to = data.get("to")
            content = data.get("content")
            if to and content:
                self.wcf.send_text(to, content)
                await websocket.send(json.dumps({
                    "type": "sent",
                    "to": to,
                    "content": content,
                }))
                logger.info(f"Sent to {to}: {content[:50]}...")
        
        elif cmd == "send_image":
            to = data.get("to")
            path = data.get("path")
            if to and path:
                self.wcf.send_image(to, path)
                await websocket.send(json.dumps({
                    "type": "sent",
                    "to": to,
                    "content": f"[image: {path}]",
                }))
        
        elif cmd == "get_contacts":
            contacts = self.wcf.get_contacts()
            await websocket.send(json.dumps({
                "type": "contacts",
                "data": contacts,
            }))
        
        elif cmd == "get_info":
            info = self.wcf.get_user_info()
            await websocket.send(json.dumps({
                "type": "info",
                "data": info,
            }))
        
        else:
            await websocket.send(json.dumps({
                "type": "error",
                "message": f"Unknown command: {cmd}"
            }))
    
    async def _forward_message(self, msg):
        """Forward WeChat message to all connected clients."""
        if not self.clients:
            return
        
        # Parse message
        msg_data = {
            "type": "message",
            "id": msg.id,
            "sender": msg.sender,
            "roomid": msg.roomid,
            "content": msg.content,
            "is_group": msg.roomid and msg.roomid.endswith("@chatroom"),
            "msg_type": msg.type,
        }
        
        # Voice message
        if msg.type == 34:  # Voice
            msg_data["is_voice"] = True
            # Save voice file
            try:
                voice_path = self.wcf.decode_voice(msg.id, msg.extra)
                msg_data["voice_path"] = voice_path
            except Exception:
                pass
        
        # Forward to all clients
        data = json.dumps(msg_data, ensure_ascii=False)
        for client in self.clients.copy():
            try:
                await client.send(data)
            except websockets.exceptions.ConnectionClosed:
                self.clients.discard(client)
    
    def _cleanup(self):
        """Cleanup resources."""
        if self.wcf:
            try:
                self.wcf.disable_recv_msg()
            except Exception:
                pass
        
        self._running = False
        logger.info("Bridge stopped")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="WeChatFerry WebSocket Bridge")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port")
    args = parser.parse_args()
    
    bridge = WeChatFerryBridge(port=args.port)
    bridge.start()


if __name__ == "__main__":
    main()
