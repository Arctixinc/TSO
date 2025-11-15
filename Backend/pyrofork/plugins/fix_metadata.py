import time
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from Backend import db
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.metadata import fetch_tv_metadata, fetch_movie_metadata
from Backend.logger import LOGGER  # existing logger

# -------------------------------
# Global state
# -------------------------------
CANCEL_REQUESTED = False
CURRENT_DONE = 0
CURRENT_TOTAL = 0
CURRENT_TASK = ""  # last movie/tv episode being processed

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

    # Count total items
    total_movies = 0
    total_tv = 0
    for i in range(1, db.current_db_index + 1):
        key = f"storage_{i}"
        total_movies += await db.dbs[key]["movie"].count_documents({})
        total_tv += await db.dbs[key]["tv"].count_documents({})

    CURRENT_TOTAL = total_movies + total_tv
    CURRENT_DONE = 0
    start_time = time.time()

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
    async def update_movies():
        global CURRENT_DONE, CURRENT_TASK
        for i in range(1, db.current_db_index + 1):
            if CANCEL_REQUESTED:
                return

            key = f"storage_{i}"
            collection = db.dbs[key]["movie"]

            cursor = collection.find({})
            async for movie in cursor:
                if CANCEL_REQUESTED:
                    return

                tmdb_id = movie["tmdb_id"]
                title = movie["title"]
                year = movie.get("release_year")
                CURRENT_TASK = f"Movie: {title} ({year})"
                LOGGER.info(f"Updating movie metadata: {CURRENT_TASK}")

                meta = await fetch_movie_metadata(
                    title=title,
                    encoded_string=None,
                    year=year,
                    quality=None,
                    default_id=None
                )

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
                    LOGGER.info(f"Movie metadata updated: {title}")

                CURRENT_DONE += 1

                # Update progress every 5 items
                if CURRENT_DONE % 5 == 0 or CURRENT_DONE == CURRENT_TOTAL:
                    elapsed = time.time() - start_time
                    avg_time = elapsed / CURRENT_DONE
                    eta = avg_time * (CURRENT_TOTAL - CURRENT_DONE)
                    await status.edit_text(
                        f"🎬 Updating Movies…\n"
                        f"{progress_bar(CURRENT_DONE, CURRENT_TOTAL)}\n"
                        f"⏱ ETA: {format_eta(eta)}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
                        ])
                    )
                    LOGGER.info(f"Progress: {CURRENT_DONE}/{CURRENT_TOTAL}, ETA: {format_eta(eta)}")

    # -------------------------
    # UPDATE TV SHOWS + EPISODES
    # -------------------------
    async def update_tv():
        global CURRENT_DONE, CURRENT_TASK
        for i in range(1, db.current_db_index + 1):
            if CANCEL_REQUESTED:
                return

            key = f"storage_{i}"
            collection = db.dbs[key]["tv"]

            cursor = collection.find({})
            async for tv in cursor:
                if CANCEL_REQUESTED:
                    return

                tmdb_id = tv["tmdb_id"]
                title = tv["title"]
                year = tv.get("release_year")

                # Show-level update
                CURRENT_TASK = f"TV Show: {title} (Show-level)"
                LOGGER.info(f"Updating show metadata: {CURRENT_TASK}")
                meta = await fetch_tv_metadata(
                    title=title,
                    season=1,
                    episode=1,
                    encoded_string=None,
                    year=year,
                    quality=None,
                    default_id=None
                )

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
                    LOGGER.info(f"Show metadata updated: {title}")

                # Episode-level update
                for season in tv.get("seasons", []):
                    if CANCEL_REQUESTED:
                        return

                    s = season["season_number"]

                    for ep in season.get("episodes", []):
                        e = ep["episode_number"]
                        CURRENT_TASK = f"TV: {title} S{s}E{e}"
                        LOGGER.info(f"Updating episode metadata: {CURRENT_TASK}")

                        ep_meta = await fetch_tv_metadata(
                            title=title,
                            season=s,
                            episode=e,
                            encoded_string=None,
                            year=year,
                            quality=None,
                            default_id=None
                        )

                        if ep_meta:
                            await collection.update_one(
                                {"tmdb_id": tmdb_id},
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
                            LOGGER.info(f"Episode metadata updated: {title} S{s}E{e}")

                CURRENT_DONE += 1

                if CURRENT_DONE % 5 == 0 or CURRENT_DONE == CURRENT_TOTAL:
                    elapsed = time.time() - start_time
                    avg_time = elapsed / CURRENT_DONE
                    eta = avg_time * (CURRENT_TOTAL - CURRENT_DONE)
                    await status.edit_text(
                        f"📺 Updating TV Shows…\n"
                        f"{progress_bar(CURRENT_DONE, CURRENT_TOTAL)}\n"
                        f"⏱ ETA: {format_eta(eta)}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
                        ])
                    )
                    LOGGER.info(f"Progress: {CURRENT_DONE}/{CURRENT_TOTAL}, ETA: {format_eta(eta)}")

    # Run the process
    await update_movies()
    if not CANCEL_REQUESTED:
        await update_tv()

    if CANCEL_REQUESTED:
        LOGGER.info("Metadata fix cancelled by user.")
        return

    await status.edit_text(
        f"🎉 **Metadata Fix Completed!**\n"
        f"{progress_bar(CURRENT_DONE, CURRENT_TOTAL)}\n"
        f"⏱ Time Taken: {format_eta(time.time() - start_time)}"
    )
    LOGGER.info(f"Metadata fix completed: {CURRENT_DONE}/{CURRENT_TOTAL} items updated.")
