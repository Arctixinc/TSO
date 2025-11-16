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
CURRENT_TASK = ""
SEM = asyncio.Semaphore(5)  # Limit concurrency to avoid rate limits
SHOW_EPISODE_PROGRESS = False  # Track if we should show episode line

# -------------------------------
# Progress & ETA helpers
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
# Cancel handler
# -------------------------------
@Client.on_callback_query(filters.regex("cancel_fix"))
async def cancel_fix(_, query):
    global CANCEL_REQUESTED
    CANCEL_REQUESTED = True
    await query.message.edit_text("❌ Metadata fixing has been cancelled by the user.")
    await query.answer("Cancelled")
    LOGGER.info("User requested metadata fix cancellation.")

# -------------------------------
# Main metadata fix handler
# -------------------------------
@Client.on_message(filters.command("fixmetadata") & filters.private & CustomFilters.owner, group=10)
async def fix_metadata_handler(_, message):
    global CANCEL_REQUESTED, CURRENT_TASK, SHOW_EPISODE_PROGRESS
    CANCEL_REQUESTED = False
    CURRENT_TASK = ""
    SHOW_EPISODE_PROGRESS = False
    start_time = time.time()

    # -------------------------------
    # Count totals
    # -------------------------------
    total_movies = 0
    total_tv_shows = 0
    total_episodes = 0

    for i in range(1, db.current_db_index + 1):
        key = f"storage_{i}"
        total_movies += await db.dbs[key]["movie"].count_documents({})
        async for tv in db.dbs[key]["tv"].find({}):
            total_tv_shows += 1
            for season in tv.get("seasons", []):
                total_episodes += len(season.get("episodes", []))

    # -------------------------------
    # Progress counters
    # -------------------------------
    movies_done = 0
    tv_shows_done = 0
    episodes_done = 0
    current_tv_show_total_episodes = 0
    current_tv_show_episodes_done = 0

    # -------------------------------
    # Initial message (no episode line)
    # -------------------------------
    status = await message.reply_text(
        f"⏳ Initializing metadata fixing...\n"
        f"🎬 Movies: 0/{total_movies}\n"
        f"🌄 TV Show: 0/{total_tv_shows}\n"
        f"📺 All Episodes: 0/{total_episodes}\n"
        f"[{'░'*20}] 0/{total_movies + total_episodes}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
        ])
    )

    LOGGER.info(f"Starting metadata fix: Movies={total_movies}, TV Shows={total_tv_shows}, Episodes={total_episodes}")

    # -------------------------------
    # Movie processor
    # -------------------------------
    async def process_movie(movie, collection):
        nonlocal movies_done
        async with SEM:
            if CANCEL_REQUESTED:
                return
            global CURRENT_TASK
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
                movies_done += 1
            except Exception as e:
                LOGGER.error(f"Error updating {CURRENT_TASK}: {e}")

    # -------------------------------
    # TV episode processor
    # -------------------------------
    async def process_tv_episode(tv, season_num, ep, collection):
        nonlocal episodes_done, current_tv_show_episodes_done
        async with SEM:
            if CANCEL_REQUESTED:
                return
            global CURRENT_TASK
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
                        array_filters=[
                            {"s.season_number": season_num},
                            {"e.episode_number": ep["episode_number"]}
                        ]
                    )
                episodes_done += 1
                current_tv_show_episodes_done += 1
            except Exception as e:
                LOGGER.error(f"Error updating {CURRENT_TASK}: {e}")

    # -------------------------------
    # Update movies
    # -------------------------------
    async def update_movies():
        tasks = []
        for i in range(1, db.current_db_index + 1):
            if CANCEL_REQUESTED:
                break
            key = f"storage_{i}"
            collection = db.dbs[key]["movie"]
            async for movie in collection.find({}):
                tasks.append(asyncio.create_task(process_movie(movie, collection)))
        await asyncio.gather(*tasks)

    # -------------------------------
    # Update TV shows
    # -------------------------------
    async def update_tv():
        nonlocal tv_shows_done, current_tv_show_total_episodes, current_tv_show_episodes_done, SHOW_EPISODE_PROGRESS
        SHOW_EPISODE_PROGRESS = True  # Enable episode line
        for i in range(1, db.current_db_index + 1):
            if CANCEL_REQUESTED:
                break
            key = f"storage_{i}"
            collection = db.dbs[key]["tv"]
            async for tv in collection.find({}):
                tv_shows_done += 1
                current_tv_show_total_episodes = sum(len(s.get("episodes", [])) for s in tv.get("seasons", []))
                current_tv_show_episodes_done = 0

                for season in tv.get("seasons", []):
                    for ep in season.get("episodes", []):
                        await process_tv_episode(tv, season["season_number"], ep, collection)

    # -------------------------------
    # Progress updater
    # -------------------------------
    async def run_progress():
        while not CANCEL_REQUESTED and (movies_done + episodes_done < total_movies + total_episodes):
            elapsed = time.time() - start_time
            overall_done = movies_done + episodes_done
            overall_total = total_movies + total_episodes
            bar = progress_bar(overall_done, overall_total)

            msg = (
                f"🎬 Movies: {movies_done}/{total_movies}\n"
                f"🌄 TV Show: {tv_shows_done}/{total_tv_shows}\n"
            )
            if SHOW_EPISODE_PROGRESS and current_tv_show_total_episodes > 0:
                msg += f"→ Episodes: {current_tv_show_episodes_done}/{current_tv_show_total_episodes}\n"
            msg += f"📺 All Episodes: {episodes_done}/{total_episodes}\n\n"
            msg += f"{bar}\n"
            msg += f"⏱ ETA: {format_eta((elapsed / max(1, overall_done)) * (overall_total - overall_done))}\n"
            msg += f"⏲ Elapsed: {format_eta(elapsed)}\n"
            msg += f"Last: {CURRENT_TASK}"

            await status.edit_text(msg, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_fix")]
            ]))
            await asyncio.sleep(5)

    # -------------------------------
    # Run tasks
    # -------------------------------
    progress_task = asyncio.create_task(run_progress())
    await update_movies()
    if not CANCEL_REQUESTED:
        await update_tv()
    progress_task.cancel()

    # -------------------------------
    # Final completion message
    # -------------------------------
    if CANCEL_REQUESTED:
        LOGGER.info("Metadata fix cancelled by user.")
        return

    elapsed = time.time() - start_time
    overall_done = movies_done + episodes_done
    overall_total = total_movies + total_episodes
    final_msg = (
        f"🎉 **Metadata Fix Completed!**\n"
        f"🎬 Movies: {movies_done}/{total_movies}\n"
        f"🌄 TV Show: {tv_shows_done}/{total_tv_shows}\n"
        f"📺 All Episodes: {episodes_done}/{total_episodes}\n\n"
        f"{progress_bar(overall_done, overall_total)}\n"
        f"⏲ Time Taken: {format_eta(elapsed)}"
    )
    await status.edit_text(final_msg)
    LOGGER.info(f"Metadata fix completed: Movies={movies_done}, TV Shows={tv_shows_done}, Episodes={episodes_done}")
