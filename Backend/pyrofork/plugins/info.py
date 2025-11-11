from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import re

@Client.on_message(filters.command('info') & filters.private & CustomFilters.owner)
async def info_command(client: Client, message: Message):
    """
    Get detailed information about a media item by title or TMDb ID.
    Usage: /info <TMDb ID> or /info <title>
    """
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❗**Usage:** `/info <TMDb ID or title>`", parse_mode=ParseMode.MARKDOWN)
            return

        query = args[1]
        status_msg = await message.reply_text(f"🔍 Searching for media with query: `{query}`", parse_mode=ParseMode.MARKDOWN)

        filter_dict = {}
        if query.isdigit():
            filter_dict["tmdb_id"] = int(query)
        else:
            filter_dict["title"] = {"$regex": query, "$options": "i"}

        results = []
        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            movies = await db.dbs[db_key]["movie"].find(filter_dict).to_list(None)
            for movie in movies:
                qualities = ", ".join([q['quality'] for q in movie.get('telegram', [])])
                results.append(f"🎬 **Movie:** {movie['title']} ({movie['release_year']})\\nTMDb ID: {movie['tmdb_id']}\\nQualities: {qualities}")

            tv_shows = await db.dbs[db_key]["tv"].find(filter_dict).to_list(None)
            for tv_show in tv_shows:
                seasons_info = []
                for season in tv_show.get('seasons', []):
                    episodes_count = len(season.get('episodes', []))
                    seasons_info.append(f"S{season['season_number']} ({episodes_count} episodes)")
                results.append(f"📺 **TV Show:** {tv_show['title']} ({tv_show['release_year']})\\nTMDb ID: {tv_show['tmdb_id']}\\nSeasons: {', '.join(seasons_info)}")

        if not results:
            await status_msg.edit_text(f"🤷 No media found with query: `{query}`", parse_mode=ParseMode.MARKDOWN)
            return

        response_text = f"**🔍 Search Results for:** `{query}`\\n\\n" + "\\n\\n".join(results)

        if len(response_text) > 4096:
            await status_msg.edit_text("📝 Output is too long. Sending as a file.", parse_mode=ParseMode.MARKDOWN)
            with open("info_results.txt", "w") as f:
                f.write(response_text)
            await message.reply_document("info_results.txt")
        else:
            await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in info command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
