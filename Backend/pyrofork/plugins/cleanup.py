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
    Scans or cleans up database entries with broken Telegram links.
    Usage:
        /cleanup          → scan & report
        /cleanup delete   → scan, delete broken links & send log
    """
    try:
        args = message.text.split()
        delete_mode = len(args) > 1 and args[1].lower() == "delete"

        # Initial message
        mode_text = "🧹 Cleanup Mode (deleting broken entries...)" if delete_mode else "🔍 Scan Mode (report only)"
        status_msg = await message.reply_text(
            f"{mode_text}\n\n📊 Checking all database entries...\n⏳ This may take a while...",
            parse_mode=ParseMode.MARKDOWN
        )

        broken_entries = []
        checked = total_deleted = total_movies = total_tv = 0

        from Backend.helper.encrypt import decode_string
        total_storage_dbs = len(db.dbs) - 1

        # === MAIN LOOP ===
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            # MOVIES
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

                    if checked % 15 == 0:
                        await status_msg.edit_text(
                            f"{'🧹 Deleting broken links...' if delete_mode else '🔍 Scanning for broken links...'}\n"
                            f"📊 Checked: `{checked}`\n"
                            f"❌ Broken: `{len(broken_entries)}`\n"
                            f"{'🗑️ Deleted' if delete_mode else '💾 Pending Deletion'}: `{total_deleted}`\n"
                            f"🎬 Movies: `{total_movies}`\n"
                            f"📺 Shows: `{total_tv}`",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    await asleep(0.1)

                # Handle deletion/update
                if delete_mode and len(valid_telegram) != len(telegram_data):
                    if valid_telegram:
                        await db.dbs[db_key]["movie"].update_one(
                            {"tmdb_id": tmdb_id}, {"$set": {"telegram": valid_telegram}}
                        )
                    else:
                        await db.dbs[db_key]["movie"].delete_one({"tmdb_id": tmdb_id})
                    total_deleted += len(telegram_data) - len(valid_telegram)

            # TV SHOWS
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

                            if checked % 15 == 0:
                                await status_msg.edit_text(
                                    f"{'🧹 Cleaning up database...' if delete_mode else '🔍 Scanning database...'}\n"
                                    f"📊 Checked: `{checked}`\n"
                                    f"❌ Broken: `{len(broken_entries)}`\n"
                                    f"{'🗑️ Deleted' if delete_mode else '💾 Pending Deletion'}: `{total_deleted}`\n"
                                    f"🎬 Movies: `{total_movies}`\n"
                                    f"📺 Shows: `{total_tv}`",
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
                    deleted_links_count = 0
                    for season, valid_season in zip(show.get("seasons", []), valid_seasons):
                        for episode, valid_episode in zip(season.get("episodes", []), valid_season.get("episodes", [])):
                            t_data = episode.get("telegram", [])
                            v_data = valid_episode.get("telegram", [])
                            deleted_links_count += len(t_data) - len(v_data)

                    if valid_seasons:
                        await db.dbs[db_key]["tv"].update_one(
                            {"tmdb_id": tmdb_id}, {"$set": {"seasons": valid_seasons}}
                        )
                    else:
                        await db.dbs[db_key]["tv"].delete_one({"tmdb_id": tmdb_id})

                    total_deleted += deleted_links_count

        # === FINAL SUMMARY ===
        summary_header = "🧹 **Cleanup Completed!**" if delete_mode else "✅ **Scan Completed!**"
        summary = (
            f"{summary_header}\n\n"
            f"📊 Total Checked: `{checked}`\n"
            f"❌ Broken Links: `{len(broken_entries)}`\n"
            f"🗑️ {'Deleted' if delete_mode else 'Would Delete'} Entries: `{total_deleted}`\n"
            f"🎬 Movies: `{total_movies}`\n"
            f"📺 TV Shows: `{total_tv}`\n\n"
        )

        if broken_entries:
            summary += "**Top 10 Broken Entries:**\n"
            for i, entry in enumerate(broken_entries[:10]):
                if entry["type"] == "movie":
                    summary += f"{i+1}. 🎬 {entry['title']} ({entry.get('quality','N/A')})\n"
                else:
                    summary += f"{i+1}. 📺 {entry['title']} S{entry.get('season')}E{entry.get('episode')} ({entry.get('quality','N/A')})\n"
            if len(broken_entries) > 10:
                summary += f"\n...and `{len(broken_entries) - 10}` more.\n"

        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN)

        # === LOG FILE ===
        if broken_entries:
            log_buffer = io.StringIO()
            log_buffer.write(f"{'CLEANUP' if delete_mode else 'SCAN'} REPORT\n")
            log_buffer.write("=" * 60 + "\n\n")
            for i, entry in enumerate(broken_entries, start=1):
                log_buffer.write(
                    f"{i}. [{'MOVIE' if entry['type']=='movie' else 'TV'}] "
                    f"{entry['title']} | {entry.get('quality','N/A')} | "
                    f"DB: {entry['db_index']} | Error: {entry.get('error','-')}\n"
                )
            log_buffer.write("\n--- SUMMARY ---\n")
            log_buffer.write(f"Checked: {checked}\nBroken: {len(broken_entries)}\nDeleted: {total_deleted}\n")
            log_buffer.seek(0)

            await client.send_document(
                chat_id=message.chat.id,
                document=io.BytesIO(log_buffer.getvalue().encode()),
                file_name=f"{'cleanup' if delete_mode else 'scan'}_report.txt",
                caption=f"🧾 {'Cleanup' if delete_mode else 'Scan'} Report Log",
            )
            log_buffer.close()

    except Exception as e:
        LOGGER.error(f"Error in cleanup: {e}")
        await message.reply_text(f"❌ Error: {e}")
            
