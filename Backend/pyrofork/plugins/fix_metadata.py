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
CURRENT_TASK = ""  # last movie/tv episode being processed

# Semaphore for concurrency
SEM = asyncio.Semaphore(10)  # adjust for your speed/API limit

# -------------------------------
# Progress Bar Helper
# -------------------------------
def progress_bar(done, total, length=20):
    filled = int(length * (done / total)) if total else 0
    return f"[{'█' * filled}{'░' * (length - filled)}] {done}/{total}"

# -------------------------------
# ETA Helper
# -------------------------------
def format_eta(seconds):
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {sec}s"
    if minutes > 0:
        return f"{minutes}m {sec}s"
    return f"{sec}s"

# -------------------------------
# CANCEL BUTTON HANDLER
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

    # -------------------------------
    # Count total movies and TV episodes
    # -------------------------------
    total_movies = 0
    total_tv = 0
    for i in range(1, db.current_db_index + 1):
        key = f"storage_{i}"
        total_movies += await db.dbs[key]["movie"].count_documents({})
        async for tv in db.dbs[key]["tv"].find({}):
            for season in tv.get("seasons", []):
                total_tv += len(season.get("episodes", []))

    CURRENT_TOTAL = total_movies + total_tv
    start_time = time.time()

    status = await message.reply_text(
        "⏳ Initializing metadata fixing...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
        ])
    )

    LOGGER.info(f"Starting metadata fix: {total_movies} movies, {total_tv} TV episodes.")

    # -------------------------------
    # Helper to update progress
    # -------------------------------
    async def update_progress():
        elapsed = time.time() - start_time
        avg_time = elapsed / max(CURRENT_DONE, 1)
        eta = avg_time * (CURRENT_TOTAL - CURRENT_DONE)
        await status.edit_text(
            f"🔄 Updating Metadata…\n"
            f"{progress_bar(CURRENT_DONE, CURRENT_TOTAL)}\n"
            f"⏱ ETA: {format_eta(eta)} | ⏳ Elapsed: {format_eta(elapsed)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
            ])
        )
        LOGGER.info(f"Progress: {CURRENT_DONE}/{CURRENT_TOTAL}, ETA: {format_eta(eta)}, Last: {CURRENT_TASK}")

    # -------------------------------
    # Process a single movie
    # -------------------------------
    async def process_movie(movie, collection):
        global CURRENT_DONE, CURRENT_TASK
        async with SEM:
            if CANCEL_REQUESTED:
                return
            try:
                CURRENT_TASK = f"Movie: {movie['title']} ({movie.get('release_year')})"
                meta = await fetch_movie_metadata(
                    title=movie["title"],
                    year=movie.get("release_year")
                )
                if meta:
                    await collection.update_one(
                        {"tmdb_id": movie["tmdb_id"]},
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
                await update_progress()
            except Exception as e:
                LOGGER.error(f"Error updating {CURRENT_TASK}: {e}")

    # -------------------------------
    # Process a single TV episode
    # -------------------------------
    async def process_tv_episode(tv, season_num, episode):
        global CURRENT_DONE, CURRENT_TASK
        async with SEM:
            if CANCEL_REQUESTED:
                return
            try:
                s = season_num
                e = episode["episode_number"]
                CURRENT_TASK = f"TV: {tv['title']} S{s}E{e}"
                ep_meta = await fetch_tv_metadata(
                    title=tv["title"],
                    season=s,
                    episode=e,
                    year=tv.get("release_year")
                )
                if ep_meta:
                    key = None
                    # Find collection
                    for i in range(1, db.current_db_index + 1):
                        if await db.dbs[f"storage_{i}"]["tv"].count_documents({"tmdb_id": tv["tmdb_id"]}) > 0:
                            key = f"storage_{i}"
                            break
                    if key:
                        await db.dbs[key]["tv"].update_one(
                            {"tmdb_id": tv["tmdb_id"]},
                            {"$set": {
                                "seasons.$[s].episodes.$[e].overview": ep_meta.get("episode_overview"),
                                "seasons.$[s].episodes.$[e].released": ep_meta.get("episode_released"),
                                "seasons.$[s].episodes.$[e].episode_backdrop": ep_meta.get("episode_backdrop"),
                            }},
                            array_filters=[
                                {"s.season_number": s},
                                {"e.episode_number": e}
                            ]
                        )
                CURRENT_DONE += 1
                await update_progress()
            except Exception as e:
                LOGGER.error(f"Error updating {CURRENT_TASK}: {e}")

    # -------------------------------
    # Update all movies concurrently
    # -------------------------------
    async def update_movies():
        tasks = []
        for i in range(1, db.current_db_index + 1):
            key = f"storage_{i}"
            collection = db.dbs[key]["movie"]
            async for movie in collection.find({}):
                if CANCEL_REQUESTED:
                    break
                tasks.append(asyncio.create_task(process_movie(movie, collection)))
        await asyncio.gather(*tasks)

    # -------------------------------
    # Update all TV shows concurrently
    # -------------------------------
    async def update_tv():
        tasks = []
        for i in range(1, db.current_db_index + 1):
            collection = db.dbs[f"storage_{i}"]["tv"]
            async for tv in collection.find({}):
                if CANCEL_REQUESTED:
                    break
                # Show-level metadata (optional)
                try:
                    CURRENT_TASK = f"TV Show: {tv['title']} (Show-level)"
                    meta = await fetch_tv_metadata(title=tv["title"], season=1, episode=1, year=tv.get("release_year"))
                    if meta:
                        await collection.update_one(
                            {"tmdb_id": tv["tmdb_id"]},
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
                except Exception as e:
                    LOGGER.error(f"Error updating show-level {tv['title']}: {e}")

                for season in tv.get("seasons", []):
                    s = season["season_number"]
                    for episode in season.get("episodes", []):
                        if CANCEL_REQUESTED:
                            break
                        tasks.append(asyncio.create_task(process_tv_episode(tv, s, episode)))
        await asyncio.gather(*tasks)

    # -------------------------------
    # Run movies + TV shows concurrently
    # -------------------------------
    await asyncio.gather(update_movies(), update_tv())

    if CANCEL_REQUESTED:
        LOGGER.info("Metadata fix cancelled by user.")
        return

    elapsed = time.time() - start_time
    await status.edit_text(
        f"🎉 **Metadata Fix Completed!**\n"
        f"{progress_bar(CURRENT_DONE, CURRENT_TOTAL)}\n"
        f"⏱ Time Taken: {format_eta(elapsed)}"
    )
    LOGGER.info(f"Metadata fix completed: {CURRENT_DONE}/{CURRENT_TOTAL} items updated.")
