import asyncio
import time
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.database import Database
from Backend.helper.metadata import fetch_movie_metadata, fetch_tv_metadata
from Backend.logger import LOGGER

# Global state
fix_task = None
is_fixing = False
should_cancel = False

# Concurrency Limit
SEMAPHORE = asyncio.Semaphore(10)

@Client.on_message(filters.command("fixmetadata") & CustomFilters.owner)
async def fix_metadata_command(client: Client, message: Message):
    global fix_task, is_fixing, should_cancel

    if is_fixing:
        await message.reply_text("⚠️ **Metadata fix is already running.**")
        return

    # Initial interaction - Start Button
    start_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Metadata Fix", callback_data="start_fix_menu")]
    ])

    await message.reply_text(
        "**🔧 Metadata Fixer System**\n\n"
        "This tool will backfill missing metadata (Cast, Runtime, Overview, Released) "
        "for your existing media library.\n\n"
        "Tap below to begin.",
        reply_markup=start_btn
    )

@Client.on_callback_query(filters.regex("^start_fix_menu$") & CustomFilters.owner)
async def start_fix_menu_callback(client: Client, callback_query: CallbackQuery):
    menu_btns = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Movies", callback_data="fix_movies"),
            InlineKeyboardButton("📺 TV Shows", callback_data="fix_tv")
        ],
        [InlineKeyboardButton("🔄 Fix All Media", callback_data="fix_all")]
    ])

    await callback_query.message.edit_text(
        "**📂 Select Category to Fix**\n\n"
        "Choose a category to start the metadata update process. "
        "This process allows you to update existing entries without affecting file links.",
        reply_markup=menu_btns
    )

@Client.on_callback_query(filters.regex(r"^fix_(movies|tv|all)$") & CustomFilters.owner)
async def fix_action_callback(client: Client, callback_query: CallbackQuery):
    global fix_task, is_fixing, should_cancel

    if is_fixing:
        await callback_query.answer("⚠️ Fix already in progress.", show_alert=True)
        return

    mode = callback_query.data.split("_")[1]
    is_fixing = True
    should_cancel = False

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Operation", callback_data="cancel_fix")]
    ])

    mode_display = "Movies" if mode == "movies" else "TV Shows" if mode == "tv" else "Full Library"

    await callback_query.message.edit_text(
        f"**🔄 Initializing {mode_display} Fix...**\n"
        "Please wait while we prepare the database...",
        reply_markup=cancel_btn
    )

    try:
        fix_task = asyncio.create_task(run_fix_process(client, callback_query.message, mode))
    except Exception as e:
        is_fixing = False
        await callback_query.message.edit_text(f"❌ **Error starting fix process:**\n`{e}`")

@Client.on_callback_query(filters.regex("^cancel_fix$") & CustomFilters.owner)
async def cancel_fix_callback(client: Client, callback_query: CallbackQuery):
    global should_cancel, is_fixing

    if not is_fixing:
        await callback_query.answer("⚠️ No process to cancel.", show_alert=True)
        return

    should_cancel = True
    await callback_query.answer("🛑 Cancelling...", show_alert=True)
    await callback_query.message.edit_text(
        "**🛑 Cancellation Requested**\n\n"
        "Stopping processes safely. Please wait..."
    )

async def get_total_counts(db, mode):
    total_movies = 0
    total_tv_shows = 0
    total_episodes = 0

    total_dbs = db.current_db_index
    for db_idx in range(1, total_dbs + 1):
        db_key = f"storage_{db_idx}"
        database = db.dbs[db_key]

        if mode in ["movies", "all"]:
            total_movies += await database["movie"].count_documents({})

        if mode in ["tv", "all"]:
            tv_cursor = database["tv"].find({}, {"seasons": 1})
            async for tv in tv_cursor:
                total_tv_shows += 1
                if "seasons" in tv:
                    for s in tv["seasons"]:
                        total_episodes += len(s.get("episodes", []))

    return total_movies, total_tv_shows, total_episodes

def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    return f"{s}s"

async def process_movie_entry(db, db_idx, movie, stats):
    if should_cancel: return

    stats["current_name"] = f"🎬 {movie.get('title', 'Unknown')} ({movie.get('release_year', '')})"

    async with SEMAPHORE:
        try:
            title = movie.get("title")
            year = movie.get("release_year")
            tmdb_id = movie.get("tmdb_id")

            metadata = await fetch_movie_metadata(title, "dummy", year, None, tmdb_id=tmdb_id)

            if metadata:
                update_fields = {
                    "cast": metadata.get("cast"),
                    "runtime": metadata.get("runtime"),
                    "overview": metadata.get("description"),
                    "released": metadata.get("year"),
                }
                update_fields = {k: v for k, v in update_fields.items() if v is not None}

                if update_fields:
                    await db.update_metadata_fields(tmdb_id, "movie", db_idx, update_fields)
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                stats["skipped"] += 1

        except Exception as e:
            LOGGER.error(f"Error fixing movie {movie.get('title')}: {e}")
            stats["errors"] += 1
        finally:
            stats["processed"] += 1

async def process_tv_episode_entry(db, db_idx, tmdb_id, title, s_no, e_no, stats):
    if should_cancel: return

    stats["current_name"] = f"📺 {title}\n💿 Season {s_no} • Episode {e_no}"

    async with SEMAPHORE:
        try:
            ep_meta = await fetch_tv_metadata(title, s_no, e_no, "dummy", tmdb_id=tmdb_id)

            if ep_meta:
                ep_fields = {
                    "overview": ep_meta.get("episode_overview"),
                    "released": ep_meta.get("episode_released")
                }
                ep_fields = {k: v for k, v in ep_fields.items() if v is not None}

                if ep_fields:
                    await db.update_metadata_fields(tmdb_id, "tv", db_idx, ep_fields, s_no, e_no)
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                stats["skipped"] += 1

        except Exception as e:
            LOGGER.error(f"Error fixing episode {title} S{s_no}E{e_no}: {e}")
            stats["errors"] += 1
        finally:
            stats["processed"] += 1

async def run_fix_process(client: Client, status_msg: Message, mode: str):
    global is_fixing, should_cancel

    db = Database()
    await db.connect()

    stats = {
        "processed": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "current_name": "Preparing..."
    }

    start_time = time.time()
    last_update_time = time.time()

    try:
        # Pre-count for progress bar
        total_movies, total_tv_shows, total_episodes = await get_total_counts(db, mode)

        total_items_to_process = 0
        if mode == "movies": total_items_to_process = total_movies
        elif mode == "tv": total_items_to_process = total_tv_shows + total_episodes # Shows + Episodes
        elif mode == "all": total_items_to_process = total_movies + total_tv_shows + total_episodes

        cancel_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Operation", callback_data="cancel_fix")]
        ])

        async def update_status():
            now = time.time()
            elapsed = now - start_time
            rate = stats["processed"] / elapsed if elapsed > 0 else 0
            remaining = total_items_to_process - stats["processed"]
            eta_seconds = remaining / rate if rate > 0 else 0

            percent = (stats["processed"] / total_items_to_process * 100) if total_items_to_process > 0 else 0
            bar_length = 10
            filled_length = int(bar_length * percent / 100)
            bar = "▰" * filled_length + "▱" * (bar_length - filled_length)

            mode_display = "Movies" if mode == "movies" else "TV Shows" if mode == "tv" else "Full Library"

            text = (
                f"**🔄 Metadata Fix in Progress: {mode_display}**\n\n"
                f"**Progress:** `{bar}` **{percent:.1f}%**\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"✅ **Updated:** `{stats['updated']}`\n"
                f"⚠️ **Skipped:** `{stats['skipped']}`\n"
                f"❌ **Errors:** `{stats['errors']}`\n"
                f"📥 **Processed:** `{stats['processed']}/{total_items_to_process}`\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"⏱ **Elapsed:** `{format_time(elapsed)}`\n"
                f"⏳ **ETA:** `{format_time(eta_seconds)}`\n\n"
                f"**Currently Processing:**\n`{stats['current_name']}`"
            )

            try:
                await status_msg.edit_text(text, reply_markup=cancel_btn)
            except Exception:
                pass

        total_dbs = db.current_db_index

        # --- Fix Movies ---
        if mode in ["movies", "all"] and not should_cancel:
            for db_idx in range(1, total_dbs + 1):
                if should_cancel: break

                db_key = f"storage_{db_idx}"
                database = db.dbs[db_key]

                try:
                    movie_ids = await database["movie"].distinct("_id")
                except Exception as e:
                    LOGGER.error(f"Failed to fetch movie IDs: {e}")
                    continue

                batch_size = 20
                for i in range(0, len(movie_ids), batch_size):
                    if should_cancel: break

                    batch_ids = movie_ids[i : i + batch_size]

                    # Fetch documents for this batch
                    cursor = database["movie"].find({"_id": {"$in": batch_ids}})
                    movie_batch = await cursor.to_list(length=batch_size)

                    await asyncio.gather(*[process_movie_entry(db, db_idx, m, stats) for m in movie_batch])

                    if time.time() - last_update_time > 2:
                        await update_status()
                        last_update_time = time.time()

        # --- Fix TV Shows ---
        if mode in ["tv", "all"] and not should_cancel:
            for db_idx in range(1, total_dbs + 1):
                if should_cancel: break

                db_key = f"storage_{db_idx}"
                database = db.dbs[db_key]

                # Fetch IDs first
                try:
                    tv_ids = await database["tv"].distinct("_id")
                except Exception as e:
                    LOGGER.error(f"Failed to fetch TV IDs: {e}")
                    continue

                # Process one TV show at a time
                for tv_id in tv_ids:
                    if should_cancel: break

                    tv = await database["tv"].find_one({"_id": tv_id})
                    if not tv: continue

                    stats["current_name"] = f"📺 Fixing Show: {tv.get('title')}"

                    # 1. Fix Show Level Metadata
                    try:
                        title = tv.get("title")
                        tmdb_id = tv.get("tmdb_id")

                        first_season = tv.get("seasons", [])[0] if tv.get("seasons") else None
                        first_episode = first_season["episodes"][0] if first_season and first_season.get("episodes") else None
                        s_num = first_season.get("season_number", 1) if first_season else 1
                        e_num = first_episode.get("episode_number", 1) if first_episode else 1

                        metadata = await fetch_tv_metadata(title, s_num, e_num, "dummy")

                        if metadata:
                            show_fields = {
                                "cast": metadata.get("cast"),
                                "runtime": metadata.get("runtime"),
                            }
                            show_fields = {k: v for k, v in show_fields.items() if v is not None}
                            if show_fields:
                                await db.update_metadata_fields(tmdb_id, "tv", db_idx, show_fields)
                                stats["updated"] += 1
                            else:
                                stats["skipped"] += 1
                        else:
                            stats["skipped"] += 1

                        stats["processed"] += 1 # Count show itself

                    except Exception as e:
                        LOGGER.error(f"Error fixing tv show level {tv.get('title')}: {e}")
                        stats["errors"] += 1
                        stats["processed"] += 1

                    # 2. Fix Episodes
                    episode_tasks = []
                    if "seasons" in tv:
                        for season in tv["seasons"]:
                            s_no = season.get("season_number")
                            for episode in season.get("episodes", []):
                                e_no = episode.get("episode_number")
                                episode_tasks.append(
                                    process_tv_episode_entry(db, db_idx, tmdb_id, title, s_no, e_no, stats)
                                )

                    # Process episodes in chunks to update UI frequently
                    if episode_tasks:
                        chunk_size = 20
                        for i in range(0, len(episode_tasks), chunk_size):
                            if should_cancel: break
                            chunk = episode_tasks[i:i + chunk_size]
                            await asyncio.gather(*chunk)

                            if time.time() - last_update_time > 2:
                                await update_status()
                                last_update_time = time.time()

        # Final Summary
        summary_text = (
            f"✅ **Metadata Fix Completed!**\n\n"
            f"📊 **Total Scanned:** `{total_items_to_process}`\n"
            f"✅ **Successfully Updated:** `{stats['updated']}`\n"
            f"⚠️ **Skipped (No New Data):** `{stats['skipped']}`\n"
            f"❌ **Errors:** `{stats['errors']}`\n"
            f"⏱ **Total Time:** `{format_time(time.time() - start_time)}`"
        )

        back_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="start_fix_menu")]
        ])

        if should_cancel:
            summary_text = (
                "🛑 **Operation Cancelled**\n\n"
                f"The fix process was stopped by user.\n\n"
                f"**Processed:** `{stats['processed']}/{total_items_to_process}`\n"
                f"⏱ **Elapsed:** `{format_time(time.time() - start_time)}`"
            )

        await status_msg.edit_text(summary_text, reply_markup=back_btn)

    except Exception as e:
        LOGGER.error(f"Fatal error in fix process: {e}")
        await status_msg.edit_text(f"❌ **Fatal Error:**\n`{e}`")
    finally:
        is_fixing = False
        should_cancel = False
