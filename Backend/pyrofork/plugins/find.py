from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import re

@Client.on_message(filters.command('find') & filters.private & CustomFilters.owner)
async def find_command(client: Client, message: Message):
    """
    Searches for media in the database by title.
    Usage: /find <keyword>
    """
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❗**Usage:** `/find <keyword>`", parse_mode=ParseMode.MARKDOWN)
            return

        keyword = args[1]
        status_msg = await message.reply_text(f"🔍 Searching for media with keyword: `{keyword}`", parse_mode=ParseMode.MARKDOWN)

        results = []
        regex = re.compile(keyword, re.IGNORECASE)

        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            movies = await db.dbs[db_key]["movie"].find({"title": regex}).to_list(None)
            for movie in movies:
                results.append(f"🎬 **Movie:** {movie['title']} (TMDb ID: {movie['tmdb_id']})")

            tv_shows = await db.dbs[db_key]["tv"].find({"title": regex}).to_list(None)
            for tv_show in tv_shows:
                results.append(f"📺 **TV Show:** {tv_show['title']} (TMDb ID: {tv_show['tmdb_id']})")

        if not results:
            await status_msg.edit_text(f"🤷 No media found with keyword: `{keyword}`", parse_mode=ParseMode.MARKDOWN)
            return

        response_text = f"**🔍 Search Results for:** `{keyword}`\n\n" + "\n".join(results)

        if len(response_text) > 4096:
            await status_msg.edit_text("📝 Output is too long. Sending as a file.", parse_mode=ParseMode.MARKDOWN)
            with open("find_results.txt", "w") as f:
                f.write(response_text)
            await message.reply_document("find_results.txt")
        else:
            await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in find command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
