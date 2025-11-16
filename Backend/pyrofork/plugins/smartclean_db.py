import io
import asyncio
from asyncio import Semaphore
from time import time
from pyrogram import filters, Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.errors import FloodWait

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
from Backend.helper.encrypt import decode_string

# -------------------------------
# Global state
# -------------------------------
CANCEL_REQUESTED = False
CURRENT_TASK = ""
SEM = Semaphore(10)  # initial concurrency
INITIAL_CONCURRENCY = 10
MAX_CONCURRENCY = 20
MIN_CONCURRENCY = 2
STATUS_UPDATE_INTERVAL = 5
BATCH_SIZE = 20

# -------------------------------
# Helpers
# -------------------------------
def format_eta(seconds):
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {sec}s"
    if minutes > 0:
        return f"{minutes}m {sec}s"
    return f"{sec}s"

def progress_bar(done, total, length=20):
    filled = int(length * done / max(1, total))
    return f"[{'█'*filled}{'░'*(length-filled)}] {done}/{total}"

# -------------------------------
# Cancel callback
# -------------------------------
@Client.on_callback_query(filters.regex("cancel_smartclean"))
async def cancel_smartclean(_, query):
    global CANCEL_REQUESTED
    CANCEL_REQUESTED = True
    await query.message.edit_text("❌ SmartClean has been cancelled by the user.")
    await query.answer("Cancelled")
    LOGGER.info("User requested smartclean cancellation.")

# -------------------------------
# SmartClean handler
# -------------------------------
@Client.on_message(filters.command("smartclean") & filters.private & CustomFilters.owner, group=10)
async def smartclean(client: Client, message: Message):
    global CANCEL_REQUESTED, CURRENT_TASK, SEM
    CANCEL_REQUESTED = False
    CURRENT_TASK = ""
    start_time = time()

    args = message.text.split()
    delete_mode = len(args) > 1 and args[1].lower() == "delete"
    mode_text = "🧹 Cleanup Mode (deleting broken entries...)" if delete_mode else "🔍 Scan Mode (report only)"

    # -------------------------------
    # Initial status message
    # -------------------------------
    status_msg = await message.reply_text(
        f"{mode_text}\n\n⏳ Initializing...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_smartclean")]
        ])
    )

    broken_entries = []
    checked = total_deleted = total_movies = total_tv = 0
    last_update = 0
    concurrency = INITIAL_CONCURRENCY
    adaptive_lock = asyncio.Lock()
    total_storage_dbs = len(db.dbs) - 1

    # -------------------------------
    # Concurrency adjuster
    # -------------------------------
    async def adjust_concurrency(success=True, flood_wait=None):
        nonlocal concurrency, SEM
        async with adaptive_lock:
            if flood_wait:
                concurrency = max(MIN_CONCURRENCY, concurrency // 2)
                LOGGER.warning(f"⏱️ FloodWait {flood_wait}s → reducing concurrency to {concurrency}")
                SEM = Semaphore(concurrency)
            elif success and concurrency < MAX_CONCURRENCY:
                concurrency = min(MAX_CONCURRENCY, concurrency + 1)
                SEM = Semaphore(concurrency)

    # -------------------------------
    # Safe get message
    # -------------------------------
    async def safe_get_message(chat_id, msg_id):
        async with SEM:
            try:
                start = time()
                msg = await client.get_messages(chat_id, msg_id)
                latency = time() - start
                if latency < 0.2:
                    await adjust_concurrency(success=True)
                return msg if msg and (msg.video or msg.document) else None
            except FloodWait as e:
                await adjust_concurrency(success=False, flood_wait=e.value)
                await asyncio.sleep(e.value + 1)
                return None
            except Exception:
                return None

    # -------------------------------
    # Validate Telegram links
    # -------------------------------
    async def validate_quality(entry, tmdb_id, db_index, content_type, meta):
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

    # -------------------------------
    # Process movies
    # -------------------------------
    async def process_movies(db_key):
        nonlocal total_movies, total_deleted, last_update, CURRENT_TASK
        movies = await db.dbs[db_key]["movie"].find({}, {"_id": 0, "tmdb_id": 1, "telegram": 1, "title": 1}).to_list(None)
        total_movies += len(movies)

        for movie in movies:
            if CANCEL_REQUESTED: break
            telegram_data = movie.get("telegram", [])
            CURRENT_TASK = f"Movie: {movie['title']}"
            tasks = [validate_quality(q, movie["tmdb_id"], db_key.split("_")[1], "movie", movie) for q in telegram_data]

            results = []
            for i in range(0, len(tasks), BATCH_SIZE):
                batch = tasks[i:i + BATCH_SIZE]
                results.extend(await asyncio.gather(*batch))

            valid_telegram = [r for r in results if r]
            if delete_mode and len(valid_telegram) != len(telegram_data):
                diff = len(telegram_data) - len(valid_telegram)
                total_deleted += diff
                if valid_telegram:
                    await db.dbs[db_key]["movie"].update_one({"tmdb_id": movie["tmdb_id"]}, {"$set": {"telegram": valid_telegram}})
                else:
                    await db.dbs[db_key]["movie"].delete_one({"tmdb_id": movie["tmdb_id"]})

            # LIVE STATUS UPDATE (Movies only)
            if time() - last_update > STATUS_UPDATE_INTERVAL:
                await update_status(show_episodes=False)
                last_update = time()

    # -------------------------------
    # Process TV shows
    # -------------------------------
    async def process_tv(db_key):
        nonlocal total_tv, total_deleted, last_update, CURRENT_TASK
        shows = await db.dbs[db_key]["tv"].find({}, {"_id":0,"tmdb_id":1,"title":1,"seasons":1}).to_list(None)
        total_tv += len(shows)

        for show in shows:
            if CANCEL_REQUESTED: break
            CURRENT_TASK = f"TV Show: {show['title']}"
            valid_seasons = []
            deleted_links_count = 0

            total_episodes = sum(len(s.get("episodes", [])) for s in show.get("seasons", []))
            episodes_done = 0

            for season in show.get("seasons", []):
                valid_episodes = []

                for episode in season.get("episodes", []):
                    telegram_data = episode.get("telegram", [])
                    tasks = [validate_quality(q, show["tmdb_id"], db_key.split("_")[1], "tv",
                             {"title": show["title"], "season": season.get("season_number"), "episode": episode.get("episode_number")}) for q in telegram_data]

                    results = []
                    for i in range(0, len(tasks), BATCH_SIZE):
                        batch = tasks[i:i + BATCH_SIZE]
                        results.extend(await asyncio.gather(*batch))

                    valid_telegram = [r for r in results if r]
                    if delete_mode:
                        deleted_links_count += len(telegram_data) - len(valid_telegram)

                    if valid_telegram:
                        episode["telegram"] = valid_telegram
                        valid_episodes.append(episode)

                    # Episode-level tracking
                    episodes_done += 1
                    await update_status(show_episodes=True, current_episode=episodes_done, total_episodes=total_episodes, last_task=f"TV: {show['title']} S{season.get('season_number')}E{episode.get('episode_number')}")

                if valid_episodes:
                    season["episodes"] = valid_episodes
                    valid_seasons.append(season)

            if delete_mode:
                if valid_seasons:
                    await db.dbs[db_key]["tv"].update_one({"tmdb_id": show["tmdb_id"]}, {"$set": {"seasons": valid_seasons}})
                else:
                    await db.dbs[db_key]["tv"].delete_one({"tmdb_id": show["tmdb_id"]})
                total_deleted += deleted_links_count

    # -------------------------------
    # Update status message
    # -------------------------------
    async def update_status(show_episodes=False, current_episode=0, total_episodes=0, last_task=None):
        overall_done = checked
        total_items = total_movies + total_tv  # rough estimate
        bar = progress_bar(overall_done, total_items)
        elapsed = time() - start_time
        eta = (elapsed / overall_done * (total_items - overall_done)) if overall_done else 0

        msg = (
            f"{'🧹 Cleaning TV...' if show_episodes else ('🧹 Cleaning...' if delete_mode else '🔍 Scanning...')}\n"
            f"📊 Checked: {checked} | ❌ Broken: {len(broken_entries)} | 🗑️ Deleted: {total_deleted}\n"
            f"🎬 Movies: {total_movies} | 📺 Shows: {total_tv}\n"
        )

        if show_episodes:
            msg += f"→ Episodes: {current_episode}/{total_episodes}\n"

        msg += f"{bar}\n"
        msg += f"⏱ ETA: {format_eta(eta)} | ⏲ Elapsed: {format_eta(elapsed)}\n"
        msg += f"Last: {last_task or CURRENT_TASK}"

        await status_msg.edit_text(msg, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_smartclean")]
        ]))

    # -------------------------------
    # Progress updater task
    # -------------------------------
    async def run_progress():
        while not CANCEL_REQUESTED:
            await update_status(show_episodes=False)
            await asyncio.sleep(STATUS_UPDATE_INTERVAL)

    # -------------------------------
    # Main execution
    # -------------------------------
    progress_task = asyncio.create_task(run_progress())
    for db_index in range(1, total_storage_dbs + 1):
        if CANCEL_REQUESTED: break
        db_key = f"storage_{db_index}"
        LOGGER.info(f"Processing {db_key} with concurrency={concurrency}")
        await asyncio.gather(process_movies(db_key), process_tv(db_key))

    progress_task.cancel()

    # -------------------------------
    # Final summary
    # -------------------------------
    elapsed = time() - start_time
    final_msg = (
        f"{'🧹 Cleanup Completed!' if delete_mode else '✅ Scan Completed!'}\n"
        f"📊 Checked: {checked}\n"
        f"❌ Broken: {len(broken_entries)}\n"
        f"🗑️ {'Deleted' if delete_mode else 'Would Delete'}: {total_deleted}\n"
        f"🎬 Movies: {total_movies} | 📺 Shows: {total_tv}\n"
        f"⚙️ Concurrency: {concurrency}\n"
        f"⏱ Time Taken: {format_eta(elapsed)}"
    )
    await status_msg.edit_text(final_msg)

    # -------------------------------
    # Report file
    # -------------------------------
    if broken_entries:
        buffer = io.StringIO()
        buffer.write(f"{'CLEANUP' if delete_mode else 'SCAN'} REPORT\n")
        buffer.write("="*60 + "\n\n")
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
