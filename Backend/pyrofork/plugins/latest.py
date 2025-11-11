from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db

@Client.on_message(filters.command('latest') & filters.private & CustomFilters.owner)
async def latest_command(client: Client, message: Message):
    """
    Shows the most recently added movies and TV shows.
    """
    try:
        status_msg = await message.reply_text("- `Fetching latest media...`", parse_mode=ParseMode.MARKDOWN)

        latest_movies = await db.sort_movies([("updated_on", "desc")], 1, 10)
        latest_tv_shows = await db.sort_tv_shows([("updated_on", "desc")], 1, 10)

        response_text = "**- Latest Movies**\n"
        for movie in latest_movies['movies']:
            response_text += f"- `{movie['title']}`\n"

        response_text += "\n**- Latest TV Shows**\n"
        for tv_show in latest_tv_shows['tv_shows']:
            response_text += f"- `{tv_show['title']}`\n"

        await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in latest command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
