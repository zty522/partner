"""Wechaty Adapter - Cross-platform WeChat integration.

Uses the Wechaty library to connect to WeChat from any platform (Linux/macOS/Windows).
This is the recommended approach for Linux users.

Usage:
    from partner.wechat_wechaty import WechatyAdapter
    
    adapter = WechatyAdapter(workspace="/path/to/workspace")
    adapter.start()  # Will show QR code for login
"""

import os
import asyncio
import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class WechatyAdapter:
    """Cross-platform WeChat adapter using Wechaty."""
    
    def __init__(self, workspace: str, config: dict = None):
        self.workspace = workspace
        self.config = config or {}
        self._bot = None
        self._on_message: Optional[Callable] = None
        
        # Import wechaty
        try:
            from wechaty import Wechaty, Message, Contact, Room
            self._wechaty = Wechaty
            self._Message = Message
            self._Contact = Contact
            self._Room = Room
        except ImportError:
            raise ImportError(
                "wechaty not installed. Install with: pip install wechaty"
            )
    
    def start(self, on_message: Callable = None):
        """Start the adapter (blocking).
        
        Will show QR code for WeChat login on first run.
        """
        self._on_message = on_message
        
        # Initialize conversation engine if no custom handler
        if not on_message:
            self._init_conversation()
        
        # Run the bot
        asyncio.run(self._run())
    
    def _init_conversation(self):
        """Initialize Partner conversation engine."""
        from .conversation import ConversationEngine
        from .task_queue import TaskQueue
        from .knowledge import KnowledgeBase
        from .journal import Journal
        from .state import StateManager
        
        state_dir = os.path.join(self.workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        
        self.task_queue = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        self.knowledge = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        self.state_manager = StateManager(state_dir)
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state_manager
        )
    
    async def _run(self):
        """Main bot loop."""
        bot = self._wechaty()
        
        @bot.on('message')
        async def on_message(msg: self._Message):
            """Handle incoming messages."""
            try:
                # Skip non-text messages for now
                if msg.type() != self._Message.Type.TEXT:
                    return
                
                # Get sender info
                talker = msg.talker()
                if not talker:
                    return
                
                # Skip self messages
                if talker.self():
                    return
                
                # Get room (group chat) info
                room = msg.room()
                
                # In groups, only respond when @mentioned
                if room:
                    # Check if bot is mentioned
                    bot_self = bot.Contact.self()
                    if not await msg.mention_self():
                        return
                    # Remove @mention from text
                    text = msg.text()
                    # TODO: Clean up @mention text
                else:
                    text = msg.text()
                
                sender_id = talker.contact_id
                sender_name = talker.name
                
                logger.info(f"Message from {sender_name}: {text[:80]}...")
                
                # Process through conversation engine
                if hasattr(self, 'conversation'):
                    response = await asyncio.to_thread(
                        self.conversation.chat,
                        user_message=text,
                        user_id=sender_id,
                        user_name=sender_name,
                    )
                    
                    if response:
                        # Send reply
                        if room:
                            await room.say(f"@{sender_name} {response}")
                        else:
                            await talker.say(response)
                        
                        logger.info(f"Replied to {sender_name}: {response[:80]}...")
                elif self._on_message:
                    # Custom handler
                    await self._on_message({
                        "sender_id": sender_id,
                        "sender_name": sender_name,
                        "text": text,
                        "is_group": room is not None,
                        "room_id": room.room_id if room else None,
                    })
                
            except Exception as e:
                logger.error(f"Error processing message: {e}")
        
        @bot.on('scan')
        async def on_scan(qrcode: str, status: int, data: Optional[str] = None):
            """Show QR code for login."""
            if status == self._Wechaty.ScanStatus.Waiting:
                print(f"\n  📱 Scan QR code to login WeChat:")
                print(f"  https://wechaty.js.org/qrcode/{qrcode}")
                print()
        
        @bot.on('login')
        async def on_login(contact: self._Contact):
            """Login successful."""
            print(f"  ✅ Logged in as: {contact.name}")
        
        @bot.on('logout')
        async def on_logout(contact: self._Contact):
            """Logged out."""
            print(f"  ⚠️  Logged out: {contact.name}")
        
        # Start the bot
        print("  Starting Wechaty...")
        await bot.start()


def main():
    """CLI entry point for testing."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Wechaty WeChat Adapter")
    parser.add_argument("--workspace", "-w", required=True, help="Partner workspace")
    args = parser.parse_args()
    
    adapter = WechatyAdapter(workspace=args.workspace)
    adapter.start()


if __name__ == "__main__":
    main()
