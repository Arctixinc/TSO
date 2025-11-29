import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.database import Database
from Backend.helper.metadata import fetch_movie_metadata, fetch_tv_metadata
from Backend.logger import LOGGER

# Global state for cancellation
fix_task = None
is_fixing = False
should_cancel = False

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
        # Run the fix process in background
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
    await callback_query.message.edit_text("🛑 Cancellation requested. Stopping after current item...")

async def run_fix_process(client: Client, status_msg: Message):
    global is_fixing, should_cancel

    db = Database()
    await db.connect()

    try:
        total_processed = 0
        total_updated = 0
        errors = 0
        skipped = 0

        # We need to iterate over all DBs
        total_dbs = db.current_db_index

        cancel_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Cancel Fix", callback_data="cancel_fix")]
        ])

        for db_idx in range(1, total_dbs + 1):
            if should_cancel: break

            db_key = f"storage_{db_idx}"
            database = db.dbs[db_key]

            # --- Fix Movies ---
            async for movie in database["movie"].find({}):
                if should_cancel: break

                total_processed += 1
                if total_processed % 10 == 0:
                    try:
                        await status_msg.edit_text(
                            f"🔄 Fixing Metadata...\n"
                            f"📁 DB: {db_idx}/{total_dbs}\n"
                            f"🎬 Processed: {total_processed}\n"
                            f"✅ Updated: {total_updated}\n"
                            f"⚠️ Skipped: {skipped}\n"
                            f"❌ Errors: {errors}\n\n",
                            reply_markup=cancel_btn
                        )
                    except Exception:
                        pass

                try:
                    title = movie.get("title")
                    year = movie.get("release_year")
                    tmdb_id = movie.get("tmdb_id")

                    # Fetch fresh metadata
                    metadata = await fetch_movie_metadata(title, "dummy", year, None)

                    if metadata:
                        update_fields = {
                            "cast": metadata.get("cast"),
                            "runtime": metadata.get("runtime"),
                            "overview": metadata.get("description"), # Ensure description is synced too
                            "released": metadata.get("year"),
                        }

                        update_fields = {k: v for k, v in update_fields.items() if v is not None}

                        if update_fields:
                            await db.update_metadata_fields(tmdb_id, "movie", db_idx, update_fields)
                            total_updated += 1
                        else:
                            skipped += 1
                    else:
                        skipped += 1
                except Exception as e:
                    LOGGER.error(f"Error fixing movie {movie.get('title')}: {e}")
                    errors += 1

            # --- Fix TV Shows ---
            async for tv in database["tv"].find({}):
                if should_cancel: break

                total_processed += 1
                if total_processed % 10 == 0:
                     try:
                        await status_msg.edit_text(
                            f"🔄 Fixing Metadata...\n"
                            f"📁 DB: {db_idx}/{total_dbs}\n"
                            f"🎬 Processed: {total_processed}\n"
                            f"✅ Updated: {total_updated}\n"
                            f"⚠️ Skipped: {skipped}\n"
                            f"❌ Errors: {errors}\n\n",
                            reply_markup=cancel_btn
                        )
                     except Exception:
                        pass

                try:
                    title = tv.get("title")
                    tmdb_id = tv.get("tmdb_id")

                    first_season = tv.get("seasons", [])[0] if tv.get("seasons") else None
                    first_episode = first_season["episodes"][0] if first_season and first_season.get("episodes") else None

                    s_num = first_season.get("season_number", 1) if first_season else 1
                    e_num = first_episode.get("episode_number", 1) if first_episode else 1

                    metadata = await fetch_tv_metadata(title, s_num, e_num, "dummy")

                    if metadata:
                        # Update Show Level fields
                        show_fields = {
                            "cast": metadata.get("cast"),
                            "runtime": metadata.get("runtime"),
                        }
                        show_fields = {k: v for k, v in show_fields.items() if v is not None}

                        if show_fields:
                            await db.update_metadata_fields(tmdb_id, "tv", db_idx, show_fields)

                        # Now iterate over all episodes in DB and update them
                        if "seasons" in tv:
                            for season in tv["seasons"]:
                                s_no = season.get("season_number")
                                for episode in season.get("episodes", []):
                                    if should_cancel: break # Fast break

                                    e_no = episode.get("episode_number")

                                    # Fetch specific episode data
                                    ep_meta = await fetch_tv_metadata(title, s_no, e_no, "dummy")

                                    if ep_meta:
                                        ep_fields = {
                                            "overview": ep_meta.get("episode_overview"),
                                            "released": ep_meta.get("episode_released")
                                        }
                                        ep_fields = {k: v for k, v in ep_fields.items() if v is not None}

                                        if ep_fields:
                                            await db.update_metadata_fields(tmdb_id, "tv", db_idx, ep_fields, s_no, e_no)

                                if should_cancel: break

                        total_updated += 1
                    else:
                        skipped += 1

                except Exception as e:
                    LOGGER.error(f"Error fixing tv show {tv.get('title')}: {e}")
                    errors += 1

        final_text = (
            f"✅ **Metadata Fix Completed!**\n\n"
            f"🎬 Total Processed: {total_processed}\n"
            f"✅ Total Updated: {total_updated}\n"
            f"⚠️ Total Skipped: {skipped}\n"
            f"❌ Errors: {errors}"
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
