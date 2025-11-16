import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from Backend import db
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.metadata import fetch_tv_metadata, fetch_movie_metadata
from Backend.logger import LOGGER

# -------------------------------
# Global state
# -------------------------------
CANCEL_REQUESTED = False
CURRENT_DONE = 0
CURRENT_TOTAL = 0
CURRENT_TASK = ""

# Limit concurrency to avoid hitting TMDB/mongo rate limits
SEM = asyncio.Semaphore(10)

# -------------------------------
# Progress Bar & ETA Helpers
# -------------------------------
def progress_bar(done, total, length=20):
    filled = int(length * (done / total)) if total else 0
    return f"[{'█' * filled}{'░' * (length - filled)}] {done}/{total}"

def format_eta(seconds):
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {sec}s"
    if minutes > 0:
        return f"{minutes}m {sec}s"
    return f"{sec}s"

# -------------------------------
# Cancel Handler
# -------------------------------
@Client.on_callback_query(filters.regex("cancel_fix"))
async def cancel_fix(_, query):
    global CANCEL_REQUESTED
    CANCEL_REQUESTED = True
    await query.message.edit_text("❌ Metadata fixing has been cancelled by the user.")
    await query.answer("Cancelled")
    LOGGER.info("User requested metadata fix cancellation.")

# -------------------------------
# MAIN COMMAND
# -------------------------------
@Client.on_message(filters.command("fixmetadata") & filters.private & CustomFilters.owner, group=10)
async def fix_metadata_handler(_, message):
    global CANCEL_REQUESTED, CURRENT_DONE, CURRENT_TOTAL, CURRENT_TASK
    CANCEL_REQUESTED = False
    CURRENT_DONE = 0
    CURRENT_TOTAL = 0
    CURRENT_TASK = ""
    start_time = time.time()

    # Count total items
    total_movies = 0
    total_tv = 0
    for i in range(1, db.current_db_index + 1):
        key = f"storage_{i}"
        total_movies += await db.dbs[key]["movie"].count_documents({})
        total_tv += await db.dbs[key]["tv"].count_documents({})

    CURRENT_TOTAL = total_movies + total_tv

    status = await message.reply_text(
        "⏳ Initializing metadata fixing...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
        ])
    )

    LOGGER.info(f"Starting metadata fix: {total_movies} movies, {total_tv} TV shows.")

    # -------------------------
    # UPDATE MOVIES
    # -------------------------
    async def process_movie(movie, collection):
        global CURRENT_DONE, CURRENT_TASK
        async with SEM:
            if CANCEL_REQUESTED:
                return
            try:
                tmdb_id = movie["tmdb_id"]
                title = movie["title"]
                year = movie.get("release_year")
                CURRENT_TASK = f"Movie: {title} ({year})"

                meta = await fetch_movie_metadata(title=title, encoded_string=None, year=year)
                if meta:
                    await collection.update_one(
                        {"tmdb_id": tmdb_id},
                        {"$set": {
                            "imdb_id": meta.get("imdb_id"),
                            "cast": meta.get("cast"),
                            "description": meta.get("description"),
                            "genres": meta.get("genres"),
                            "poster": meta.get("poster"),
                            "backdrop": meta.get("backdrop"),
                            "logo": meta.get("logo"),
                            "rating": meta.get("rate"),
                        }}
                    )
                CURRENT_DONE += 1
            except Exception as e:
                LOGGER.error(f"Error updating {CURRENT_TASK}: {e}")

    async def update_movies():
        tasks = []
        for i in range(1, db.current_db_index + 1):
            if CANCEL_REQUESTED:
                break
            key = f"storage_{i}"
            collection = db.dbs[key]["movie"]
            async for movie in collection.find({}):
                if CANCEL_REQUESTED:
                    break
                tasks.append(asyncio.create_task(process_movie(movie, collection)))
        await asyncio.gather(*tasks)

    # -------------------------
    # UPDATE TV SHOWS
    # -------------------------
    async def process_tv_episode(tv, season_num, ep, collection):
        global CURRENT_DONE, CURRENT_TASK
        async with SEM:
            if CANCEL_REQUESTED:
                return
            try:
                tmdb_id = tv["tmdb_id"]
                title = tv["title"]
                year = tv.get("release_year")
                CURRENT_TASK = f"TV: {title} S{season_num}E{ep['episode_number']}"

                ep_meta = await fetch_tv_metadata(
                    title=title,
                    season=season_num,
                    episode=ep["episode_number"],
                    encoded_string=None,
                    year=year
                )
                if ep_meta:
                    await collection.update_one(
                        {"tmdb_id": tmdb_id},
                        {"$set": {
                            "seasons.$[s].episodes.$[e].overview": ep_meta.get("episode_overview"),
                            "seasons.$[s].episodes.$[e].released": ep_meta.get("episode_released"),
                            "seasons.$[s].episodes.$[e].episode_backdrop": ep_meta.get("episode_backdrop"),
                        }},
                        array_filters=[{"s.season_number": season_num}, {"e.episode_number": ep["episode_number"]}]
                    )
                CURRENT_DONE += 1
            except Exception as e:
                LOGGER.error(f"Error updating {CURRENT_TASK}: {e}")

    async def update_tv():
        tasks = []
        for i in range(1, db.current_db_index + 1):
            if CANCEL_REQUESTED:
                break
            key = f"storage_{i}"
            collection = db.dbs[key]["tv"]
            async for tv in collection.find({}):
                if CANCEL_REQUESTED:
                    break
                # show-level metadata
                tasks.append(asyncio.create_task(process_tv_episode(tv, 1, {"episode_number":1}, collection)))
                for season in tv.get("seasons", []):
                    for ep in season.get("episodes", []):
                        tasks.append(asyncio.create_task(process_tv_episode(tv, season["season_number"], ep, collection)))
        # update progress concurrently
        await asyncio.gather(*tasks)

    # -------------------------
    # RUN EVERYTHING WITH PROGRESS UPDATE
    # -------------------------
    async def run_update():
        while not CANCEL_REQUESTED and CURRENT_DONE < CURRENT_TOTAL:
            await status.edit_text(
                f"⏳ Updating Metadata...\n"
                f"{progress_bar(CURRENT_DONE, CURRENT_TOTAL)}\n"
                f"⏱ ETA: {format_eta(((time.time()-start_time)/max(1,CURRENT_DONE))*(CURRENT_TOTAL-CURRENT_DONE))}\n"
                f"⏲ Elapsed: {format_eta(time.time()-start_time)}\n"
                f"Last: {CURRENT_TASK}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
                ])
            )
            await asyncio.sleep(5)

    # Run movies and TV concurrently
    updater_task = asyncio.create_task(run_update())
    await asyncio.gather(update_movies(), update_tv())
    updater_task.cancel()

    if CANCEL_REQUESTED:
        LOGGER.info("Metadata fix cancelled by user.")
        return

    await status.edit_text(
        f"🎉 **Metadata Fix Completed!**\n"
        f"{progress_bar(CURRENT_DONE, CURRENT_TOTAL)}\n"
        f"⏱ Time Taken: {format_eta(time.time() - start_time)}"
    )
    LOGGER.info(f"Metadata fix completed: {CURRENT_DONE}/{CURRENT_TOTAL} items updated.")
