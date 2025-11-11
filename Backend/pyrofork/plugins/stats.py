from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db

@Client.on_message(filters.command('stats') & filters.private & CustomFilters.owner)
async def stats_command(client: Client, message: Message):
    """
    Shows statistics about the media in the database.
    """
    try:
        status_msg = await message.reply_text("📊 Calculating stats...", parse_mode=ParseMode.MARKDOWN)

        total_movies = 0
        total_tv_shows = 0

        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            total_movies += await db.dbs[db_key]["movie"].count_documents({})
            total_tv_shows += await db.dbs[db_key]["tv"].count_documents({})

        stats_text = (
            f"**📊 Database Statistics**\n\n"
            f"🎬 **Total Movies:** `{total_movies}`\n"
            f"📺 **Total TV Shows:** `{total_tv_shows}`\n"
        )

        await status_msg.edit_text(stats_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in stats command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
