from pyrogram import filters, Client
from pyrogram.types import Message

@Client.on_message(filters.command("demo"))
async def demo_handler(client: Client, message: Message):
    await message.reply_text("✅ **Plugin Version: 1.0**\n\nChange this text in `Backend/pyrofork/plugins/test_reload_demo.py` and run `/reload` to test hot reloading!")
