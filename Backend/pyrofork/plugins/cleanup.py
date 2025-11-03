import io
from asyncio import sleep as asleep
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db


@Client.on_message(filters.command('cleanup') & filters.private & CustomFilters.owner, group=10)
async def cleanup_broken_links(client: Client, message: Message):
    """
    Scans database for broken Telegram video links and optionally deletes them.
    Usage:
        /cleanup          → only scans & reports
        /cleanup delete   → scans & deletes broken entries
    """
    try:
        args = message.text.split()
        delete_mode = len(args) > 1 and args[1].lower() == "delete"

        status_msg = await message.reply_text(
            f"{'🧹 Cleaning up' if delete_mode else '🔍 Starting cleanup scan...'}\n"
            "📊 Checking all database entries for broken links...\n"
            "⏳ This may take a while...",
            parse_mode=ParseMode.MARKDOWN
        )

        broken_entries = []
        checked = 0
        total_movies = 0
        total_tv = 0
        total_deleted = 0

        from Backend.helper.encrypt import decode_string

        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            # === MOVIES ===
            LOGGER.info(f"Checking movies in {db_key}...")
            movies = await db.dbs[db_key]["movie"].find({}).to_list(None)
            total_movies += len(movies)

            for movie in movies:
                tmdb_id = movie.get("tmdb_id")
                telegram_data = movie.get("telegram", [])
                valid_telegram = []

                for quality in telegram_data:
                    checked += 1
                    try:
                        decoded = await decode_string(quality["id"])
                        chat_id = int(f"-100{decoded['chat_id']}")
                        msg_id = int(decoded["msg_id"])

                        msg = await client.get_messages(chat_id, msg_id)
                        if not msg or not (msg.video or msg.document):
                            raise Exception("Message not found or invalid")
                        valid_telegram.append(quality)

                    except Exception as e:
                        broken_entries.append({
                            "type": "movie",
                            "title": movie.get("title"),
                            "quality": quality.get("quality"),
                            "tmdb_id": tmdb_id,
                            "db_index": db_index,
                            "error": str(e),
                        })
                        LOGGER.warning(f"Broken Movie: {movie.get('title')} - {quality.get('quality')} ({e})")

                    if checked % 10 == 0:
                        await status_msg.edit_text(
                            f"🔍 Scanning...\n"
                            f"📊 Checked: {checked}\n"
                            f"❌ Broken: {len(broken_entries)}\n"
                            f"🗑️ Deleted: {total_deleted}\n"
                            f"🎬 Movies: {total_movies}\n"
                            f"📺 Shows: {total_tv}",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    await asleep(0.1)

                # Delete or update
                if delete_mode and len(valid_telegram) != len(telegram_data):
                    if valid_telegram:
                        await db.dbs[db_key]["movie"].update_one(
                            {"tmdb_id": tmdb_id}, {"$set": {"telegram": valid_telegram}}
                        )
                    else:
                        await db.dbs[db_key]["movie"].delete_one({"tmdb_id": tmdb_id})
                    total_deleted += 1

            # === TV SHOWS ===
            LOGGER.info(f"Checking TV shows in {db_key}...")
            shows = await db.dbs[db_key]["tv"].find({}).to_list(None)
            total_tv += len(shows)

            for show in shows:
                tmdb_id = show.get("tmdb_id")
                valid_seasons = []

                for season in show.get("seasons", []):
                    valid_episodes = []
                    for episode in season.get("episodes", []):
                        valid_telegram = []
                        for quality in episode.get("telegram", []):
                            checked += 1
                            try:
                                decoded = await decode_string(quality["id"])
                                chat_id = int(f"-100{decoded['chat_id']}")
                                msg_id = int(decoded["msg_id"])

                                msg = await client.get_messages(chat_id, msg_id)
                                if not msg or not (msg.video or msg.document):
                                    raise Exception("Message not found or invalid")
                                valid_telegram.append(quality)

                            except Exception as e:
                                broken_entries.append({
                                    "type": "tv",
                                    "title": show.get("title"),
                                    "season": season.get("season_number"),
                                    "episode": episode.get("episode_number"),
                                    "quality": quality.get("quality"),
                                    "tmdb_id": tmdb_id,
                                    "db_index": db_index,
                                    "error": str(e),
                                })
                                LOGGER.warning(
                                    f"Broken TV: {show.get('title')} S{season.get('season_number')}E{episode.get('episode_number')} ({e})"
                                )

                            if checked % 10 == 0:
                                await status_msg.edit_text(
                                    f"🔍 Scanning...\n"
                                    f"📊 Checked: {checked}\n"
                                    f"❌ Broken: {len(broken_entries)}\n"
                                    f"🗑️ Deleted: {total_deleted}\n"
                                    f"🎬 Movies: {total_movies}\n"
                                    f"📺 Shows: {total_tv}",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                            await asleep(0.1)

                        if valid_telegram or not delete_mode:
                            episode["telegram"] = valid_telegram
                            valid_episodes.append(episode)
                    if valid_episodes:
                        season["episodes"] = valid_episodes
                        valid_seasons.append(season)

                if delete_mode:
                    if valid_seasons:
                        await db.dbs[db_key]["tv"].update_one(
                            {"tmdb_id": tmdb_id}, {"$set": {"seasons": valid_seasons}}
                        )
                    else:
                        await db.dbs[db_key]["tv"].delete_one({"tmdb_id": tmdb_id})
                    total_deleted += 1

        # === Final Report ===
        summary = (
            f"{'🧹 Cleanup Complete!' if delete_mode else '✅ Scan Complete!'}\n\n"
            f"📊 Total Checked: {checked}\n"
            f"❌ Broken Links: {len(broken_entries)}\n"
            f"🗑️ Deleted Entries: {total_deleted}\n"
            f"🎬 Movies: {total_movies}\n"
            f"📺 TV Shows: {total_tv}\n\n"
        )

        if broken_entries:
            summary += "**First 10 broken entries:**\n"
            for i, entry in enumerate(broken_entries[:10]):
                if entry["type"] == "movie":
                    summary += f"{i+1}. 🎬 {entry['title']} ({entry.get('quality', 'N/A')})\n"
                else:
                    summary += f"{i+1}. 📺 {entry['title']} S{entry.get('season')}E{entry.get('episode')} ({entry.get('quality', 'N/A')})\n"
            if len(broken_entries) > 10:
                summary += f"\n...and {len(broken_entries) - 10} more\n"

        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN)

        # === Create and Send Log File ===
        if broken_entries:
            log_buffer = io.StringIO()
            log_buffer.write("BROKEN LINKS CLEANUP REPORT\n")
            log_buffer.write("=" * 60 + "\n\n")
            for i, entry in enumerate(broken_entries, start=1):
                if entry["type"] == "movie":
                    log_buffer.write(
                        f"{i}. [MOVIE] {entry['title']} | {entry.get('quality')} | DB: {entry['db_index']} | Error: {entry.get('error', '-')}\n"
                    )
                else:
                    log_buffer.write(
                        f"{i}. [TV] {entry['title']} S{entry.get('season')}E{entry.get('episode')} | {entry.get('quality')} | DB: {entry['db_index']} | Error: {entry.get('error', '-')}\n"
                    )
            log_buffer.write("\n=== SUMMARY ===\n")
            log_buffer.write(f"Total Checked: {checked}\n")
            log_buffer.write(f"Broken Links: {len(broken_entries)}\n")
            log_buffer.write(f"Deleted Entries: {total_deleted}\n")
            log_buffer.write(f"Movies Checked: {total_movies}\n")
            log_buffer.write(f"TV Shows Checked: {total_tv}\n")

            log_buffer.seek(0)
            await client.send_document(
                chat_id=message.chat.id,
                document=io.BytesIO(log_buffer.getvalue().encode()),
                file_name="cleanup_report.txt",
                caption="🧾 Cleanup Report Log",
            )
            log_buffer.close()

    except Exception as e:
        LOGGER.error(f"Error in cleanup command: {e}")
        await message.reply_text(f"❌ Error: {e}")
