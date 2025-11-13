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
INITIAL_CONCURRENCY = 10
MAX_CONCURRENCY = 20
MIN_CONCURRENCY = 2
STATUS_UPDATE_INTERVAL = 5
BATCH_SIZE = 20


@Client.on_message(filters.command("smartclean") & filters.private & CustomFilters.owner, group=10)
async def smartclean(client: Client, message: Message):
    """
    Smart Cleanup:
    • Adaptive concurrency
    • Accurate ETA
    • Finish time prediction
    """
    try:
        args = message.text.split()
        delete_mode = len(args) > 1 and args[1].lower() == "delete"
        mode_text = "🧹 Cleanup Mode (deleting broken entries...)" if delete_mode else "🔍 Scan Mode (report only)"

        status_msg = await message.reply_text(
            f"{mode_text}\n\n📊 Pre-scanning database…\n⏳ Counting all Telegram links…",
            parse_mode=ParseMode.MARKDOWN,
        )

        from Backend.helper.encrypt import decode_string

        # === PRE-SCAN FOR TOTAL LINKS ===
        total_links = 0
        total_movies = total_tv = 0

        total_storage_dbs = len(db.dbs) - 1

        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            movies = await db.dbs[db_key]["movie"].find({}, {"_id": 0, "telegram": 1}).to_list(None)
            total_movies += len(movies)
            for mv in movies:
                total_links += len(mv.get("telegram", []))

            shows = await db.dbs[db_key]["tv"].find({}, {"_id": 0, "seasons": 1}).to_list(None)
            total_tv += len(shows)
            for show in shows:
                for season in show.get("seasons", []):
                    for ep in season.get("episodes", []):
                        total_links += len(ep.get("telegram", []))

        # ETA variables
        checked_links = 0
        start_time = time()
        smooth_eta = None

        await status_msg.edit_text(
            f"{mode_text}\n\n"
            f"📊 Total Links: `{total_links}`\n"
            f"🎬 Movies: `{total_movies}` | 📺 TV Shows: `{total_tv}`\n\n"
            f"⚙️ Starting smart scan…",
            parse_mode=ParseMode.MARKDOWN,
        )

        # RUNTIME VARIABLES
        concurrency = INITIAL_CONCURRENCY
        semaphore = Semaphore(concurrency)
        adaptive_lock = asyncio.Lock()
        last_update = 0
        broken_entries = []
        total_deleted = 0

        # === ADAPTIVE CONCURRENCY ===
        async def adjust_concurrency(success=True, flood_wait=None):
            nonlocal concurrency, semaphore
            async with adaptive_lock:
                if flood_wait:
                    concurrency = max(MIN_CONCURRENCY, concurrency // 2)
                    LOGGER.warning(f"⏱️ FloodWait {flood_wait}s → reducing concurrency to {concurrency}")
                    semaphore = Semaphore(concurrency)
                elif success and concurrency < MAX_CONCURRENCY:
                    concurrency = min(MAX_CONCURRENCY, concurrency + 1)
                    semaphore = Semaphore(concurrency)

        # === SAFE TELEGRAM FETCH ===
        async def safe_get_message(chat_id, msg_id):
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

        # === ETA CALCULATION ===
        def get_eta_text():
            nonlocal smooth_eta

            if checked_links == 0:
                return "⏳ ETA: calculating…"

            elapsed = time() - start_time
            rate = checked_links / elapsed
            remaining = total_links - checked_links
            raw_eta = remaining / rate if rate > 0 else 999999

            # smooth
            if smooth_eta is None:
                smooth_eta = raw_eta
            else:
                smooth_eta = (smooth_eta * 0.7) + (raw_eta * 0.3)

            # finish time
            finish_ts = time() + smooth_eta
            finish_clock = time_to_clock(finish_ts)

            return f"⏳ ETA: `{format_duration(smooth_eta)}`\n🕒 Finishing at: `{finish_clock}`"

        def format_duration(sec):
            sec = int(sec)
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            return f"{h}h {m}m {s}s"

        def time_to_clock(ts):
            import datetime
            return datetime.datetime.fromtimestamp(ts).strftime("%I:%M %p")

        # === VALIDATE A SINGLE QUALITY ===
        async def validate_quality(entry, tmdb_id, db_index, content_type, meta):
            nonlocal checked_links
            try:
                decoded = await decode_string(entry["id"])
                chat_id = int(f"-100{decoded['chat_id']}")
                msg_id = int(decoded["msg_id"])

                msg = await safe_get_message(chat_id, msg_id)
                checked_links += 1

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

        # === PROCESS MOVIES ===
        async def process_movies(db_key):
            nonlocal total_deleted, last_update

            movies = await db.dbs[db_key]["movie"].find(
                {}, {"_id": 0, "tmdb_id": 1, "telegram": 1, "title": 1}
            ).to_list(None)

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

                # === STATUS UPDATE ===
                if time() - last_update > STATUS_UPDATE_INTERVAL:
                    await status_msg.edit_text(
                        f"{mode_text}\n\n"
                        f"📊 Checked: `{checked_links}` / `{total_links}`\n"
                        f"❌ Broken: `{len(broken_entries)}` | 🗑️ Deleted: `{total_deleted}`\n"
                        f"⚙️ Concurrency: `{concurrency}`\n\n"
                        f"{get_eta_text()}",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    last_update = time()

        # === PROCESS TV SHOWS ===
        async def process_tv(db_key):
            nonlocal total_deleted, last_update

            shows = await db.dbs[db_key]["tv"].find(
                {}, {"_id": 0, "tmdb_id": 1, "title": 1, "seasons": 1}
            ).to_list(None)

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

                # === STATUS UPDATE ===
                if time() - last_update > STATUS_UPDATE_INTERVAL:
                    await status_msg.edit_text(
                        f"{mode_text}\n\n"
                        f"📊 Checked: `{checked_links}` / `{total_links}`\n"
                        f"❌ Broken: `{len(broken_entries)}` | 🗑️ Deleted: `{total_deleted}`\n"
                        f"⚙️ Concurrency: `{concurrency}`\n\n"
                        f"{get_eta_text()}",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    last_update = time()

        # === MAIN LOOP ===
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            LOGGER.info(f"Processing {db_key} with concurrency={concurrency}")
            await asyncio.gather(
                process_movies(db_key),
                process_tv(db_key)
            )

        # === SUMMARY ===
        summary = (
            f"{'🧹 Cleanup Completed!' if delete_mode else '✅ Scan Completed!'}\n\n"
            f"📊 Checked: `{checked_links}` / `{total_links}`\n"
            f"❌ Broken Links: `{len(broken_entries)}`\n"
            f"🗑️ {'Deleted' if delete_mode else 'Would Delete'}: `{total_deleted}`\n"
            f"🎬 Movies: `{total_movies}` | 📺 TV: `{total_tv}`\n"
            f"⚙️ Final Concurrency: `{concurrency}`\n"
            f"⏳ Total Time: `{format_duration(time() - start_time)}`"
        )

        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN)

        # === REPORT FILE ===
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

    except Exception as e:
        LOGGER.error(f"Error in smartclean: {e}")
        await message.reply_text(f"❌ Error: {e}")
