import io
import asyncio
from asyncio import Semaphore, sleep as asleep
from time import time
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.errors import FloodWait

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db


# === CONFIG ===
INITIAL_CONCURRENCY = 10          # starting parallel requests
MAX_CONCURRENCY = 20              # upper limit for adaptive scaling
MIN_CONCURRENCY = 2               # lower bound to stay safe
STATUS_UPDATE_INTERVAL = 5        # seconds between progress updates
BATCH_SIZE = 20                   # how many entries to gather at once


@Client.on_message(filters.command("smartclean") & filters.private & CustomFilters.owner, group=10)
async def smartclean(client: Client, message: Message):
    """
    Adaptive Cleanup:
    Automatically balances Telegram API calls for maximum speed without hitting FloodWait.
    """
    try:
        # START TIME TRACKING
        overall_start = time()

        args = message.text.split()
        delete_mode = len(args) > 1 and args[1].lower() == "delete"
        mode_text = "🧹 Cleanup Mode (deleting broken entries...)" if delete_mode else "🔍 Scan Mode (report only)"

        status_msg = await message.reply_text(
            f"{mode_text}\n\n📊 Checking database entries...\n⏳ Please wait...",
            parse_mode=ParseMode.MARKDOWN,
        )

        from Backend.helper.encrypt import decode_string

        broken_entries = []
        checked = total_deleted = total_movies = total_tv = 0
        last_update = 0
        total_storage_dbs = len(db.dbs) - 1
        concurrency = INITIAL_CONCURRENCY
        semaphore = Semaphore(concurrency)
        adaptive_lock = asyncio.Lock()

        async def adjust_concurrency(success=True, flood_wait=None):
            """Adapt concurrency based on results."""
            nonlocal concurrency, semaphore
            async with adaptive_lock:
                if flood_wait:
                    concurrency = max(MIN_CONCURRENCY, concurrency // 2)
                    LOGGER.warning(f"⏱️ FloodWait {flood_wait}s → reducing concurrency to {concurrency}")
                    semaphore = Semaphore(concurrency)
                elif success and concurrency < MAX_CONCURRENCY:
                    concurrency = min(MAX_CONCURRENCY, concurrency + 1)
                    semaphore = Semaphore(concurrency)

        async def safe_get_message(chat_id, msg_id):
            """Get Telegram message safely with adaptive rate limit."""
            async with semaphore:
                try:
                    start = time()
                    msg = await client.get_messages(chat_id, msg_id)
                    latency = time() - start
                    if latency < 0.2:
                        await adjust_concurrency(success=True)
                    return msg if msg and (msg.video or msg.document) else None
                except FloodWait as e:
                    await adjust_concurrency(success=False, flood_wait=e.value)
                    await asleep(e.value + 1)
                    return None
                except Exception:
                    return None

        async def validate_quality(entry, tmdb_id, db_index, content_type, meta):
            """Validate a single Telegram quality entry."""
            nonlocal checked
            try:
                decoded = await decode_string(entry["id"])
                chat_id = int(f"-100{decoded['chat_id']}")
                msg_id = int(decoded["msg_id"])
                msg = await safe_get_message(chat_id, msg_id)
                checked += 1
                if msg:
                    return entry
                else:
                    raise Exception("Invalid or missing message")
            except Exception as e:
                info = {
                    "type": content_type,
                    "tmdb_id": tmdb_id,
                    "db_index": db_index,
                    "error": str(e),
                    "title": meta.get("title", "Unknown"),
                    "quality": entry.get("quality"),
                }
                if content_type == "tv":
                    info.update({
                        "season": meta.get("season"),
                        "episode": meta.get("episode"),
                    })
                broken_entries.append(info)
                return None

        async def process_movies(db_key):
            nonlocal total_movies, total_deleted, last_update
            movies = await db.dbs[db_key]["movie"].find(
                {}, {"_id": 0, "tmdb_id": 1, "telegram": 1, "title": 1}
            ).to_list(None)
            total_movies += len(movies)

            for movie in movies:
                telegram_data = movie.get("telegram", [])
                tasks = [
                    validate_quality(q, movie["tmdb_id"], db_key.split("_")[1], "movie", movie)
                    for q in telegram_data
                ]
                results = []
                for i in range(0, len(tasks), BATCH_SIZE):
                    batch = tasks[i:i + BATCH_SIZE]
                    results.extend(await asyncio.gather(*batch))
                valid_telegram = [r for r in results if r]

                if delete_mode and len(valid_telegram) != len(telegram_data):
                    diff = len(telegram_data) - len(valid_telegram)
                    total_deleted += diff
                    if valid_telegram:
                        await db.dbs[db_key]["movie"].update_one(
                            {"tmdb_id": movie["tmdb_id"]}, {"$set": {"telegram": valid_telegram}}
                        )
                    else:
                        await db.dbs[db_key]["movie"].delete_one({"tmdb_id": movie["tmdb_id"]})

                if time() - last_update > STATUS_UPDATE_INTERVAL:
                    await status_msg.edit_text(
                        f"{'🧹 Cleaning...' if delete_mode else '🔍 Scanning...'}\n"
                        f"📊 Checked: `{checked}` | ❌ Broken: `{len(broken_entries)}`\n"
                        f"🗑️ Deleted: `{total_deleted}` | ⚙️ Concurrency: `{concurrency}`\n"
                        f"🎬 Movies: `{total_movies}` | 📺 Shows: `{total_tv}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    last_update = time()

        async def process_tv(db_key):
            nonlocal total_tv, total_deleted, last_update
            shows = await db.dbs[db_key]["tv"].find(
                {}, {"_id": 0, "tmdb_id": 1, "title": 1, "seasons": 1}
            ).to_list(None)
            total_tv += len(shows)

            for show in shows:
                valid_seasons = []
                deleted_links_count = 0

                for season in show.get("seasons", []):
                    valid_episodes = []
                    for episode in season.get("episodes", []):
                        telegram_data = episode.get("telegram", [])
                        tasks = [
                            validate_quality(
                                q,
                                show["tmdb_id"],
                                db_key.split("_")[1],
                                "tv",
                                {
                                    "title": show["title"],
                                    "season": season.get("season_number"),
                                    "episode": episode.get("episode_number"),
                                },
                            )
                            for q in telegram_data
                        ]
                        results = []
                        for i in range(0, len(tasks), BATCH_SIZE):
                            batch = tasks[i:i + BATCH_SIZE]
                            results.extend(await asyncio.gather(*batch))
                        valid_telegram = [r for r in results if r]

                        if delete_mode:
                            deleted_links_count += len(telegram_data) - len(valid_telegram)
                        if valid_telegram or not delete_mode:
                            episode["telegram"] = valid_telegram
                            valid_episodes.append(episode)

                    if valid_episodes:
                        season["episodes"] = valid_episodes
                        valid_seasons.append(season)

                if delete_mode:
                    if valid_seasons:
                        await db.dbs[db_key]["tv"].update_one(
                            {"tmdb_id": show["tmdb_id"]}, {"$set": {"seasons": valid_seasons}}
                        )
                    else:
                        await db.dbs[db_key]["tv"].delete_one({"tmdb_id": show["tmdb_id"]})
                    total_deleted += deleted_links_count

                if time() - last_update > STATUS_UPDATE_INTERVAL:
                    await status_msg.edit_text(
                        f"{'🧹 Cleaning TV...' if delete_mode else '🔍 Scanning TV...'}\n"
                        f"📊 Checked: `{checked}` | ❌ Broken: `{len(broken_entries)}`\n"
                        f"🗑️ Deleted: `{total_deleted}` | ⚙️ Concurrency: `{concurrency}`\n"
                        f"🎬 Movies: `{total_movies}` | 📺 Shows: `{total_tv}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    last_update = time()

        # === MAIN LOOP ===
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            LOGGER.info(f"Processing {db_key} with concurrency={concurrency}")
            await asyncio.gather(process_movies(db_key), process_tv(db_key))

        # === END TIME TRACKING ===
        total_time = time() - overall_start
        minutes = int(total_time // 60)
        seconds = int(total_time % 60)
        time_taken_text = f"{minutes}m {seconds}s"

        # === SUMMARY ===
        summary = (
            f"{'🧹 Cleanup Completed!' if delete_mode else '✅ Scan Completed!'}\n\n"
            f"📊 Checked: `{checked}`\n"
            f"❌ Broken Links: `{len(broken_entries)}`\n"
            f"🗑️ {'Deleted' if delete_mode else 'Would Delete'}: `{total_deleted}`\n"
            f"🎬 Movies: `{total_movies}` | 📺 TV: `{total_tv}`\n"
            f"⚙️ Final Concurrency: `{concurrency}`\n"
            f"⏱️ Time Taken: `{time_taken_text}`\n"
        )

        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN)

        # === LOG REPORT ===
        if broken_entries:
            buffer = io.StringIO()
            buffer.write(f"{'CLEANUP' if delete_mode else 'SCAN'} REPORT\n")
            buffer.write("=" * 60 + "\n\n")
            for i, entry in enumerate(broken_entries, start=1):
                buffer.write(
                    f"{i}. [{'MOVIE' if entry['type']=='movie' else 'TV'}] "
                    f"{entry['title']} | {entry.get('quality','N/A')} | "
                    f"DB: {entry['db_index']} | Error: {entry.get('error','-')}\n"
                )
            buffer.seek(0)

            await client.send_document(
                chat_id=message.chat.id,
                document=io.BytesIO(buffer.getvalue().encode()),
                file_name=f"{'cleanup' if delete_mode else 'scan'}_report.txt",
                caption=f"🧾 {'Cleanup' if delete_mode else 'Scan'} Report Log",
            )
            buffer.close()

    except Exception as e:
        LOGGER.error(f"Error in cleanup: {e}")
        await message.reply_text(f"❌ Error: {e}")
