import io
import time
from datetime import datetime, timedelta
from asyncio import sleep as asleep
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db


def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


@Client.on_message(filters.command('cleanup') & filters.private & CustomFilters.owner, group=10)
async def cleanup_broken_links(client: Client, message: Message):
    try:
        args = message.text.split()
        delete_mode = len(args) > 1 and args[1].lower() == "delete"

        # Initial reply
        mode_text = "🧹 Cleanup Mode (deleting broken entries...)" if delete_mode else "🔍 Scan Mode (report only)"
        status_msg = await message.reply_text(
            f"{mode_text}\n\n📊 Pre-scanning database…\n⏳ Estimating total links...",
            parse_mode=ParseMode.MARKDOWN
        )

        from Backend.helper.encrypt import decode_string
        total_storage_dbs = len(db.dbs) - 1

        # --- PRE-SCAN TO COUNT TOTAL LINKS ---
        total_links = 0
        total_movies = 0
        total_tv = 0

        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            # Count movie links
            movies = await db.dbs[db_key]["movie"].find({}).to_list(None)
            total_movies += len(movies)
            for movie in movies:
                for quality in movie.get("telegram", []):
                    total_links += 1

            # Count TV links
            shows = await db.dbs[db_key]["tv"].find({}).to_list(None)
            total_tv += len(shows)
            for show in shows:
                for season in show.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for quality in episode.get("telegram", []):
                            total_links += 1

        # Update message after pre-scan
        await status_msg.edit_text(
            f"{mode_text}\n\n"
            f"📊 Total database entries:\n"
            f"🎬 Movies: `{total_movies}`\n"
            f"📺 Shows: `{total_tv}`\n"
            f"🔗 Total Links to check: `{total_links}`\n\n"
            f"🚀 Starting cleanup…",
            parse_mode=ParseMode.MARKDOWN
        )

        # --- MAIN CLEANUP ---
        start_time = time.time()
        checked = 0
        broken_entries = []
        total_deleted = 0
        smoothed_eta = None

        # MAIN PROCESSING
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            # MOVIES
            movies = await db.dbs[db_key]["movie"].find({}).to_list(None)
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

                    # PROGRESS UPDATE
                    if checked % 15 == 0:
                        elapsed = time.time() - start_time
                        speed = checked / elapsed if elapsed > 0 else 0
                        remaining = total_links - checked
                        raw_eta = remaining / speed if speed > 0 else 0

                        if smoothed_eta is None:
                            smoothed_eta = raw_eta
                        else:
                            smoothed_eta = (0.25 * raw_eta) + (0.75 * smoothed_eta)

                        eta = smoothed_eta
                        finish_time = datetime.now() + timedelta(seconds=eta)
                        finish_clock = finish_time.strftime("%I:%M %p")

                        await status_msg.edit_text(
                            f"{mode_text}\n\n"
                            f"📊 Checked: `{checked}/{total_links}`\n"
                            f"⏳ Elapsed: `{format_time(elapsed)}`\n"
                            f"🚀 Speed: `{speed:.2f} links/sec`\n"
                            f"📅 ETA: `{format_time(eta)}`\n"
                            f"🕒 Finishing at: **{finish_clock}**\n"
                            f"❌ Broken: `{len(broken_entries)}`\n"
                            f"🗑️ Deleted: `{total_deleted}`",
                            parse_mode=ParseMode.MARKDOWN
                        )
                        await asleep(0.05)

                if delete_mode and len(valid_telegram) != len(telegram_data):
                    deleted_count = len(telegram_data) - len(valid_telegram)
                    total_deleted += deleted_count

                    if valid_telegram:
                        await db.dbs[db_key]["movie"].update_one(
                            {"tmdb_id": tmdb_id}, {"$set": {"telegram": valid_telegram}}
                        )
                    else:
                        await db.dbs[db_key]["movie"].delete_one({"tmdb_id": tmdb_id})

            # TV SHOWS
            shows = await db.dbs[db_key]["tv"].find({}).to_list(None)
            for show in shows:
                tmdb_id = show.get("tmdb_id")
                valid_seasons = []
                deleted_links_count = 0

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

                            # PROGRESS UPDATE
                            if checked % 15 == 0:
                                elapsed = time.time() - start_time
                                speed = checked / elapsed if elapsed > 0 else 0
                                remaining = total_links - checked
                                raw_eta = remaining / speed if speed > 0 else 0

                                if smoothed_eta is None:
                                    smoothed_eta = raw_eta
                                else:
                                    smoothed_eta = (0.25 * raw_eta) + (0.75 * smoothed_eta)

                                eta = smoothed_eta
                                finish_time = datetime.now() + timedelta(seconds=eta)
                                finish_clock = finish_time.strftime("%I:%M %p")

                                await status_msg.edit_text(
                                    f"{mode_text}\n\n"
                                    f"📊 Checked: `{checked}/{total_links}`\n"
                                    f"⏳ Elapsed: `{format_time(elapsed)}`\n"
                                    f"🚀 Speed: `{speed:.2f} links/sec`\n"
                                    f"📅 ETA: `{format_time(eta)}`\n"
                                    f"🕒 Finishing at: **{finish_clock}**\n"
                                    f"❌ Broken: `{len(broken_entries)}`\n"
                                    f"🗑️ Deleted: `{total_deleted}`",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                                await asleep(0.05)

                        if delete_mode:
                            deleted_links_count += len(episode.get("telegram", [])) - len(valid_telegram)

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

                    total_deleted += deleted_links_count

        # --- FINAL SUMMARY ---
        elapsed_total = time.time() - start_time

        summary = (
            f"{'🧹 Cleanup Completed!' if delete_mode else '✅ Scan Completed!'}\n\n"
            f"⏱️ Time Taken: `{format_time(elapsed_total)}`\n"
            f"📊 Checked: `{checked}`\n"
            f"❌ Broken Links: `{len(broken_entries)}`\n"
            f"🗑️ {'Deleted' if delete_mode else 'Would Delete'}: `{total_deleted}`\n"
            f"🔗 Total Links: `{total_links}`"
        )

        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN)

        # Log file
        if broken_entries:
            log_buffer = io.StringIO()
            for i, entry in enumerate(broken_entries, start=1):
                log_buffer.write(
                    f"{i}. {entry['type'].upper()} - {entry['title']} | "
                    f"{entry.get('quality','N/A')} | DB {entry['db_index']} | "
                    f"Error: {entry['error']}\n"
                )
            log_buffer.seek(0)

            await client.send_document(
                chat_id=message.chat.id,
                document=io.BytesIO(log_buffer.getvalue().encode()),
                file_name=f"{'cleanup' if delete_mode else 'scan'}_report.txt",
                caption="🧾 Report Log",
            )
            log_buffer.close()

    except Exception as e:
        LOGGER.error(f"Error in cleanup: {e}")
        await message.reply_text(f"❌ Error: {e}")
