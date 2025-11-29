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
        await message.reply_text("⚠️ Metadata fix is already running.")
        return

    is_fixing = True
    should_cancel = False

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancel Fix", callback_data="cancel_fix")]
    ])

    status_msg = await message.reply_text(
        "🔄 Starting metadata fix... Fetching all media from database.",
        reply_markup=cancel_btn
    )

    try:
        fix_task = asyncio.create_task(run_fix_process(client, status_msg))
    except Exception as e:
        is_fixing = False
        await status_msg.edit_text(f"❌ Error starting fix process: {e}")

@Client.on_callback_query(filters.regex("^cancel_fix$") & CustomFilters.owner)
async def cancel_fix_callback(client: Client, callback_query: CallbackQuery):
    global should_cancel, is_fixing

    if not is_fixing:
        await callback_query.answer("⚠️ No metadata fix is currently running.", show_alert=True)
        return

    should_cancel = True
    await callback_query.answer("🛑 Cancellation requested...", show_alert=True)
    await callback_query.message.edit_text("🛑 Cancellation requested. Stopping after current batch...")

async def process_movie_entry(db, db_idx, movie, stats):
    if should_cancel: return

    async with SEMAPHORE:
        try:
            title = movie.get("title")
            year = movie.get("release_year")
            tmdb_id = movie.get("tmdb_id")

            metadata = await fetch_movie_metadata(title, "dummy", year, None)

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

    async with SEMAPHORE:
        try:
            ep_meta = await fetch_tv_metadata(title, s_no, e_no, "dummy")

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

async def run_fix_process(client: Client, status_msg: Message):
    global is_fixing, should_cancel

    db = Database()
    await db.connect()

    stats = {
        "processed": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0
    }

    last_update_time = time.time()

    try:
        total_dbs = db.current_db_index
        cancel_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Cancel Fix", callback_data="cancel_fix")]
        ])

        async def update_status():
            try:
                await status_msg.edit_text(
                    f"🔄 Fixing Metadata... (Async Mode)\n"
                    f"🎬 Processed: {stats['processed']}\n"
                    f"✅ Updated: {stats['updated']}\n"
                    f"⚠️ Skipped: {stats['skipped']}\n"
                    f"❌ Errors: {stats['errors']}\n\n",
                    reply_markup=cancel_btn
                )
            except Exception:
                pass

        for db_idx in range(1, total_dbs + 1):
            if should_cancel: break

            db_key = f"storage_{db_idx}"
            database = db.dbs[db_key]

            # --- Fix Movies (Batching) ---
            movie_batch = []
            async for movie in database["movie"].find({}):
                if should_cancel: break

                movie_batch.append(movie)

                if len(movie_batch) >= 20:
                    await asyncio.gather(*[process_movie_entry(db, db_idx, m, stats) for m in movie_batch])
                    movie_batch = []

                    if time.time() - last_update_time > 3:
                        await update_status()
                        last_update_time = time.time()

            # Process remaining movies
            if movie_batch and not should_cancel:
                await asyncio.gather(*[process_movie_entry(db, db_idx, m, stats) for m in movie_batch])
                await update_status()

            # --- Fix TV Shows ---
            async for tv in database["tv"].find({}):
                if should_cancel: break

                # 1. Fix Show Level Metadata
                try:
                    title = tv.get("title")
                    tmdb_id = tv.get("tmdb_id")

                    # Heuristics to get a valid season/episode for the show-level fetch
                    first_season = tv.get("seasons", [])[0] if tv.get("seasons") else None
                    first_episode = first_season["episodes"][0] if first_season and first_season.get("episodes") else None
                    s_num = first_season.get("season_number", 1) if first_season else 1
                    e_num = first_episode.get("episode_number", 1) if first_episode else 1

                    # We process show-level metadata serially per show, but that's fast
                    metadata = await fetch_tv_metadata(title, s_num, e_num, "dummy")

                    if metadata:
                        show_fields = {
                            "cast": metadata.get("cast"),
                            "runtime": metadata.get("runtime"),
                        }
                        show_fields = {k: v for k, v in show_fields.items() if v is not None}
                        if show_fields:
                            await db.update_metadata_fields(tmdb_id, "tv", db_idx, show_fields)
                            # We count show-level update as 1 processed item?
                            # User likely cares about total media items or total operations.
                            # Let's count it.
                            stats["updated"] += 1
                            stats["processed"] += 1
                        else:
                             stats["skipped"] += 1
                             stats["processed"] += 1
                    else:
                        stats["skipped"] += 1
                        stats["processed"] += 1

                except Exception as e:
                    LOGGER.error(f"Error fixing tv show level {tv.get('title')}: {e}")
                    stats["errors"] += 1
                    stats["processed"] += 1

                # 2. Fix Episodes (Parallelize all episodes in this show)
                episode_tasks = []
                if "seasons" in tv:
                    for season in tv["seasons"]:
                        s_no = season.get("season_number")
                        for episode in season.get("episodes", []):
                            e_no = episode.get("episode_number")
                            episode_tasks.append(
                                process_tv_episode_entry(db, db_idx, tmdb_id, title, s_no, e_no, stats)
                            )

                if episode_tasks:
                    # Run all episodes for this show concurrently (limited by global semaphore)
                    await asyncio.gather(*episode_tasks)

                if time.time() - last_update_time > 3:
                    await update_status()
                    last_update_time = time.time()

        final_text = (
            f"✅ **Metadata Fix Completed!**\n\n"
            f"🎬 Processed: {stats['processed']}\n"
            f"✅ Updated: {stats['updated']}\n"
            f"⚠️ Skipped: {stats['skipped']}\n"
            f"❌ Errors: {stats['errors']}"
        )

        if should_cancel:
            final_text = "🛑 **Metadata Fix Cancelled.**\n\n" + final_text

        await status_msg.edit_text(final_text)

    except Exception as e:
        LOGGER.error(f"Fatal error in fix process: {e}")
        await status_msg.edit_text(f"❌ Fatal error: {e}")
    finally:
        is_fixing = False
        should_cancel = False
