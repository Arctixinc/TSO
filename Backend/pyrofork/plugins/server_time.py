from pyrogram import Client, filters
from pyrogram.types import Message
from datetime import datetime
import pytz
from Backend.helper.custom_filter import CustomFilters

@Client.on_message(filters.command("servertime") & CustomFilters.owner)
async def server_time_command(client: Client, message: Message):
    try:
        utc_now = datetime.now(pytz.utc)
        ist_now = utc_now.astimezone(pytz.timezone("Asia/Kolkata"))

        fmt = "%Y-%m-%d %H:%M:%S %Z"

        text = (
            f"🕰 **Server Time**\n\n"
            f"🌍 **UTC:** `{utc_now.strftime(fmt)}`\n"
            f"🇮🇳 **IST:** `{ist_now.strftime(fmt)}`"
        )

        await message.reply_text(text)
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
