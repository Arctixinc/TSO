from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db, StartTime
from time import time
from Backend.helper.pyro import get_readable_time

@Client.on_message(filters.command('health') & filters.private & CustomFilters.owner)
async def health_command(client: Client, message: Message):
    """
    Provides a quick status check on the bot's health.
    """
    try:
        status_msg = await message.reply_text("- `Checking system health...`", parse_mode=ParseMode.MARKDOWN)

        db_status = "OK"
        try:
            await db.dbs["tracking"].command("ping")
        except Exception as e:
            db_status = f"Error: {e}"

        telegram_status = "OK"
        try:
            await client.get_me()
        except Exception as e:
            telegram_status = f"Error: {e}"

        health_text = (
            f"**- System Health**\n\n"
            f"- **Database:** `{db_status}`\n"
            f"- **Telegram API:** `{telegram_status}`\n"
            f"- **Uptime:** `{get_readable_time(time() - StartTime)}`"
        )

        await status_msg.edit_text(health_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in health command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
