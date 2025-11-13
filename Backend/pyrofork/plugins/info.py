from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import re
import html

@Client.on_message(filters.command('info') & filters.private & CustomFilters.owner)
async def info_command(client: Client, message: Message):
    """
    Get detailed information about a media item by title or TMDb ID.
    Usage: /info <TMDb ID> or /info <title>
    """
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text(
                "❗**Usage:** `/info <TMDb ID or title>`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        query = args[1].strip()
        status_msg = await message.reply_text(
            f"🔍 Searching for media with query: `{query}`",
            parse_mode=ParseMode.MARKDOWN
        )

        # Prepare query filter
        if query.isdigit():
            filter_dict = {"tmdb_id": int(query)}
        else:
            filter_dict = {"title": {"$regex": re.escape(query), "$options": "i"}}

        results = []
        total_storage_dbs = len(db.dbs) - 1

        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            # Search Movies
            movies = await db.dbs[db_key]["movie"].find(filter_dict).to_list(None)
            for movie in movies:
                qualities = ", ".join(q.get('quality', 'N/A') for q in movie.get('telegram', [])) or "N/A"
                results.append(
                    f"🎬 **Movie:** {movie.get('title', 'Unknown')} ({movie.get('release_year', 'N/A')})\n"
                    f"🆔 TMDb ID: `{movie.get('tmdb_id', 'N/A')}`\n"
                    f"💾 Qualities: {qualities}"
                )

            # Search TV Shows
            tv_shows = await db.dbs[db_key]["tv"].find(filter_dict).to_list(None)
            for tv_show in tv_shows:
                seasons_info = []
                for season in tv_show.get('seasons', []):
                    episodes_count = len(season.get('episodes', []))
                    seasons_info.append(f"S{season.get('season_number', '?')} ({episodes_count} eps)")
                results.append(
                    f"📺 **TV Show:** {tv_show.get('title', 'Unknown')} ({tv_show.get('release_year', 'N/A')})\n"
                    f"🆔 TMDb ID: `{tv_show.get('tmdb_id', 'N/A')}`\n"
                    f"📚 Seasons: {', '.join(seasons_info) if seasons_info else 'N/A'}"
                )

        # Handle no results
        if not results:
            await status_msg.edit_text(
                f"🤷 No media found for query: `{query}`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Prepare response
        response_text = f"**🔍 Search Results for:** `{query}`\n\n" + "\n\n".join(results)

        # Send output
        if len(response_text) > 4096:
            await status_msg.edit_text("📝 Output too long — sending as file.")
            with open("info_results.txt", "w", encoding="utf-8") as f:
                f.write(response_text)
            await message.reply_document("info_results.txt")
        else:
            await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in info command: {e}", exc_info=True)
        await message.reply_text(
            f"❌ **Error:** `{e}`",
            parse_mode=ParseMode.MARKDOWN
        )
