from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from Backend.helper.custom_filter import CustomFilters
import secrets
import string

COMMANDS = [
    BotCommand("password", "🔐 Generate a random password")
]

@Client.on_message(filters.command("password") & CustomFilters.owner)
async def password_command(client: Client, message: Message):
    cmd = message.command
    length = 16 # Default length

    if len(cmd) > 1:
        try:
            length = int(cmd[1])
            if length > 64:
                length = 64
            if length < 8:
                length = 8
        except ValueError:
            pass

    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    password = ''.join(secrets.choice(chars) for _ in range(length))

    await message.reply_text(
        f"🔐 **Generated Password ({length} chars):**\n\n"
        f"`{password}`\n\n"
        f"⚠️ _Click to copy. Save it securely!_"
    )
