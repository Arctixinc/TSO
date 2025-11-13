from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.helpers import escape_markdown
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import os
import asyncio

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
            f"🔍 Searching for media with query: `{escape_markdown(query)}`",
            parse_mode=ParseMode.MARKDOWN
        )

        # Build filter
        if query.isdigit():
            filter_dict = {"tmdb_id": int(query)}
        else:
            filter_dict = {"title": {"$regex": query, "$options": "i"}}

        results = []
        total_storage_dbs = len(db.dbs) - 1

        # Gather all DB queries concurrently
        tasks = []
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            tasks.append(db.dbs[db_key]["movie"].find(filter_dict).to_list(None))
            tasks.append(db.dbs[db_key]["tv"].find(filter_dict).to_list(None))

        db_results = await asyncio.gather(*tasks)

        # Process results
        for i, data in enumerate(db_results):
            if i % 2 == 0:
                # Movies
                for movie in data:
                    title = escape_markdown(movie.get("title", "Unknown Title"))
                    release_year = movie.get("release_year", "N/A")
                    tmdb_id = movie.get("tmdb_id", "N/A")
                    qualities = ", ".join(
                        [q.get("quality", "N/A") for q in movie.get("telegram", [])]
                    )
                    file_count = len(movie.get("telegram", []))
                    results.append(
                        f"🎬 **Movie:** {title} ({release_year})\n"
                        f"TMDb ID: `{tmdb_id}`\n"
                        f"Qualities: {qualities}\n"
                        f"Files: {file_count}"
                    )
            else:
                # TV Shows
                for tv_show in data:
                    title = escape_markdown(tv_show.get("title", "Unknown Show"))
                    release_year = tv_show.get("release_year", "N/A")
                    tmdb_id = tv_show.get("tmdb_id", "N/A")

                    seasons_info = []
                    for season in tv_show.get("seasons", []):
                        sn = season.get("season_number", "?")
                        episodes_count = len(season.get("episodes", []))
                        seasons_info.append(f"S{sn} ({episodes_count} eps)")

                    results.append(
                        f"📺 **TV Show:** {title} ({release_year})\n"
                        f"TMDb ID: `{tmdb_id}`\n"
                        f"Seasons: {', '.join(seasons_info) or 'N/A'}"
                    )

        # No results found
        if not results:
            await status_msg.edit_text(
                f"🤷 No media found with query: `{escape_markdown(query)}`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        response_text = f"**🔍 Search Results for:** `{escape_markdown(query)}`\n\n" + "\n\n".join(results)

        # If output too long -> send as file
        if len(response_text) > 4096:
            await status_msg.edit_text("📝 Output too long — sending as file.", parse_mode=ParseMode.MARKDOWN)
            with open("info_results.txt", "w", encoding="utf-8") as f:
                f.write(response_text)
            await message.reply_document("info_results.txt")
            os.remove("info_results.txt")
        else:
            await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in info command: {e}")
        await message.reply_text(f"❌ **Error:** `{e}`", parse_mode=ParseMode.MARKDOWN)
