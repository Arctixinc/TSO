import asyncio
import uuid
from pyrogram import filters, Client, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto
from pyrogram.errors import FloodWait, MessageNotModified
from Backend.helper.custom_filter import CustomFilters
from Backend.config import Telegram
from Backend.helper.encrypt import decode_string, encode_string
from Backend import db
from Backend.helper.task_manager import delete_message

# --- Constants ---
SEND_RATE_LIMIT = 1.5
AUTO_DELETE_DELAY = 600
PROGRESS_UPDATE_INTERVAL = 5

# --- Global State ---
# Structure: {chat_id: {"event": asyncio.Event(), "task": asyncio.Task, "id": str}}
active_sends = {}
active_sends_lock = asyncio.Lock()

# --- Helper Functions ---

async def interruptible_sleep(delay: float, event: asyncio.Event) -> bool:
    """
    Sleeps for `delay` seconds, but returns early if `event` is set.
    Returns True if interrupted (cancelled), False if completed.
    """
    try:
        await asyncio.wait_for(event.wait(), timeout=delay)
        return True
    except asyncio.TimeoutError:
        return False

async def safe_edit_message(message: Message, text: str, reply_markup: InlineKeyboardMarkup = None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            pass
    except MessageNotModified:
        pass
    except Exception as e:
        print(f"Failed to edit message: {e}")

async def safe_delete_messages(client: Client, chat_id: int, message_ids: list):
    try:
        await client.delete_messages(chat_id, message_ids)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await client.delete_messages(chat_id, message_ids)
        except Exception:
            pass
    except Exception as e:
        print(f"Failed to delete messages: {e}")

async def schedule_deletion(client: Client, chat_id: int, message_ids: list, delay: int = 60):
    await asyncio.sleep(delay)
    await safe_delete_messages(client, chat_id, message_ids)

# --- Handlers ---

@Client.on_message(filters.command('start') & filters.private & CustomFilters.owner, group=10)
async def send_start_message(client: Client, message: Message):
    try:
        if len(message.command) > 1:
            payload = message.command[1]

            if payload.startswith("get_"):
                encoded_data = payload[4:]
                try:
                    decoded_str = await decode_string(encoded_data)
                    media_type, tmdb_id_str, db_index_str = decoded_str.split(":")
                    tmdb_id = int(tmdb_id_str)
                    db_index = int(db_index_str)

                    media = await db.get_document(media_type, tmdb_id, db_index)
                    if not media:
                        await message.reply_text("❌ Media not found in database.")
                        return

                    if media_type == "movie":
                        files_sent = []
                        files_data = media.get("telegram", [])
                        if not files_data:
                            await message.reply_text("❌ No files available for this movie.")
                            return

                        status_msg = await message.reply_text("📂 Sending movie files...")

                        for file_info in files_data:
                            try:
                                encoded_id = file_info.get("id")
                                file_id_data = await decode_string(encoded_id)
                                chat_id = int(f"-100{file_id_data['chat_id']}")
                                msg_id = int(file_id_data['msg_id'])

                                sent_msg = await client.copy_message(
                                    chat_id=message.chat.id,
                                    from_chat_id=chat_id,
                                    message_id=msg_id,
                                    caption=f"🎬 {media['title']} ({media['release_year']})\n💾 {file_info.get('quality', 'Unknown')} - {file_info.get('size', '')}"
                                )
                                files_sent.append(sent_msg.id)
                            except Exception as e:
                                print(f"Error sending file: {e}")

                        await status_msg.delete()

                        if files_sent:
                            warning = await message.reply_text(
                                "⚠️ **Warning:** These files will be deleted in **1 minute** to prevent copyright issues.\n"
                                "📥 **Save them to your Saved Messages immediately!**"
                            )
                            asyncio.create_task(schedule_deletion(client, message.chat.id, files_sent + [warning.id]))
                        else:
                            await message.reply_text("❌ Failed to retrieve files.")

                    elif media_type == "tv":
                        seasons = media.get("seasons", [])
                        if not seasons:
                            await message.reply_text("❌ No seasons found.")
                            return

                        seasons.sort(key=lambda x: x.get("season_number", 0))

                        buttons = []
                        row = []
                        for s in seasons:
                            s_num = s.get("season_number")
                            callback_data = f"tv_S_{tmdb_id}_{db_index}_{s_num}_0"
                            row.append(InlineKeyboardButton(f"S {s_num:02}", callback_data=callback_data))
                            if len(row) == 4:
                                buttons.append(row)
                                row = []
                        if row:
                            buttons.append(row)

                        genres = ", ".join(media.get("genres", []))
                        caption_text = (
                            f"🎬 **{media['title']}** ({media.get('release_year', 'N/A')})\n"
                            f"⭐ **Rating:** {media.get('rating', 'N/A')}\n"
                            f"🎭 **Genres:** {genres}\n\n"
                            f"📂 **Select a Season:**"
                        )

                        poster = media.get("poster") or media.get("backdrop")
                        if poster and poster.startswith("http"):
                            await message.reply_photo(
                                photo=poster,
                                caption=caption_text,
                                reply_markup=InlineKeyboardMarkup(buttons)
                            )
                        else:
                            await message.reply_text(
                                caption_text,
                                reply_markup=InlineKeyboardMarkup(buttons)
                            )

                except Exception as e:
                    await message.reply_text(f"❌ Invalid or expired link. Error: {e}")
                return

        base_url = Telegram.BASE_URL
        addon_url = f"{base_url}/stremio/manifest.json"
        bot_username = client.me.username

        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 Search Media", switch_inline_query_current_chat="!search ")]
        ])

        await message.reply_text(
            '<b>Welcome to the main Telegram Stremio bot!</b>\n\n'
            'To install the Stremio addon, copy the URL below and add it in the Stremio addons:\n\n'
            f'<b>Your Addon URL:</b>\n<code>{addon_url}</code>\n\n'
            'Click below to search for media:',
            quote=True,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=button
        )

    except Exception as e:
        await message.reply_text(f"⚠️ Error: {e}")
        print(f"Error in /start handler: {e}")

@Client.on_callback_query(filters.regex(r"^tv_S_(\d+)_(\d+)_(\d+)(?:_(\d+))?$"))
async def tv_season_handler(client: Client, callback_query: CallbackQuery):
    try:
        data_parts = callback_query.data.split("_")

        tmdb_id = int(data_parts[2])
        db_index = int(data_parts[3])
        s_num = int(data_parts[4])

        page = 0
        if len(data_parts) > 5:
            page = int(data_parts[5])

        media = await db.get_document("tv", tmdb_id, db_index)
        if not media:
            await callback_query.answer("TV Show not found.", show_alert=True)
            return

        target_season = next((s for s in media.get("seasons", []) if s.get("season_number") == s_num), None)
        if not target_season:
            await callback_query.answer("Season not found.", show_alert=True)
            return

        episodes = target_season.get("episodes", [])
        episodes.sort(key=lambda x: x.get("episode_number", 0))

        limit = 50
        total_episodes = len(episodes)
        start_idx = page * limit
        end_idx = min(start_idx + limit, total_episodes)

        current_episodes = episodes[start_idx:end_idx]

        buttons = []
        row = []
        for ep in current_episodes:
            e_num = ep.get("episode_number")
            cb_data = f"tv_E_{tmdb_id}_{db_index}_{s_num}_{e_num}"
            row.append(InlineKeyboardButton(f"{e_num:02}", callback_data=cb_data))
            if len(row) == 6:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"tv_S_{tmdb_id}_{db_index}_{s_num}_{page-1}"))

        if end_idx < total_episodes:
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"tv_S_{tmdb_id}_{db_index}_{s_num}_{page+1}"))

        if nav_row:
            buttons.append(nav_row)

        action_row = []
        action_row.append(InlineKeyboardButton(
            "📥 Send Full Season",
            callback_data=f"tv_FS_{tmdb_id}_{db_index}_{s_num}"
        ))

        buttons.append(action_row)
        buttons.append([InlineKeyboardButton("🔙 Back to Seasons", callback_data=f"tv_back_S_{tmdb_id}_{db_index}")])

        caption_text = (
            f"📺 **{media['title']}**\n"
            f"📂 **Season {s_num:02}** • Page {page+1}\n\n"
            f"👇 **Select an Episode:**"
        )

        if callback_query.message.photo:
            await callback_query.message.edit_caption(
                caption=caption_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await callback_query.message.edit_text(
                caption_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    except Exception as e:
        print(f"Error in tv_season_handler: {e}")
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex(r"^tv_E_(\d+)_(\d+)_(\d+)_(\d+)$"))
async def tv_episode_handler(client: Client, callback_query: CallbackQuery):
    try:
        _, _, tmdb_id, db_index, s_num, e_num = callback_query.data.split("_")
        tmdb_id, db_index, s_num, e_num = int(tmdb_id), int(db_index), int(s_num), int(e_num)

        media = await db.get_document("tv", tmdb_id, db_index)
        if not media:
            await callback_query.answer("TV Show not found.", show_alert=True)
            return

        target_season = next((s for s in media.get("seasons", []) if s.get("season_number") == s_num), None)
        target_episode = next((e for e in target_season.get("episodes", []) if e.get("episode_number") == e_num), None) if target_season else None

        if not target_episode:
            await callback_query.answer("Episode not found.", show_alert=True)
            return

        files_data = target_episode.get("telegram", [])
        if not files_data:
            await callback_query.answer("No files for this episode.", show_alert=True)
            return

        await callback_query.answer("📂 Sending episode files...")

        files_sent = []
        for file_info in files_data:
            try:
                encoded_id = file_info.get("id")
                file_id_data = await decode_string(encoded_id)
                chat_id = int(f"-100{file_id_data['chat_id']}")
                msg_id = int(file_id_data['msg_id'])

                sent_msg = await client.copy_message(
                    chat_id=callback_query.message.chat.id,
                    from_chat_id=chat_id,
                    message_id=msg_id,
                    caption=f"📺 {media['title']} - S{s_num:02}E{e_num:02}\n💾 {file_info.get('quality', 'Unknown')} - {file_info.get('size', '')}"
                )
                files_sent.append(sent_msg.id)
            except Exception as e:
                print(f"Error sending file: {e}")

        if files_sent:
            warning = await client.send_message(
                callback_query.message.chat.id,
                "⚠️ **Warning:** These files will be deleted in **1 minute** to prevent copyright issues.\n"
                "📥 **Save them to your Saved Messages immediately!**"
            )
            asyncio.create_task(schedule_deletion(client, callback_query.message.chat.id, files_sent + [warning.id]))
        else:
            await client.send_message(callback_query.message.chat.id, "❌ Failed to retrieve files.")

    except Exception as e:
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex(r"^tv_back_S_(\d+)_(\d+)$"))
async def tv_back_season_handler(client: Client, callback_query: CallbackQuery):
    try:
        data_parts = callback_query.data.split("_")
        tmdb_id = int(data_parts[3])
        db_index = int(data_parts[4])

        media = await db.get_document("tv", tmdb_id, db_index)
        if not media:
            await callback_query.answer("Media not found.", show_alert=True)
            return

        seasons = media.get("seasons", [])
        seasons.sort(key=lambda x: x.get("season_number", 0))

        buttons = []
        row = []
        for s in seasons:
            s_num = s.get("season_number")
            callback_data = f"tv_S_{tmdb_id}_{db_index}_{s_num}_0"
            row.append(InlineKeyboardButton(f"S {s_num:02}", callback_data=callback_data))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        genres = ", ".join(media.get("genres", []))
        caption_text = (
            f"🎬 **{media['title']}** ({media.get('release_year', 'N/A')})\n"
            f"⭐ **Rating:** {media.get('rating', 'N/A')}\n"
            f"🎭 **Genres:** {genres}\n\n"
            f"📂 **Select a Season:**"
        )

        if callback_query.message.photo:
            await callback_query.message.edit_caption(
                caption=caption_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await callback_query.message.edit_text(
                caption_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    except Exception as e:
        await callback_query.answer(f"Error: {e}", show_alert=True)

# --- Full Season Handler ---
@Client.on_callback_query(filters.regex(r"^tv_FS_(\d+)_(\d+)_(\d+)$"))
async def tv_full_season_handler(client: Client, callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id

    # 1. Atomic Race Protection
    async with active_sends_lock:
        if chat_id in active_sends:
            await callback_query.answer("⚠️ A task is already active. Please wait or cancel it.", show_alert=True)
            return

        task_id = str(uuid.uuid4())
        cancel_event = asyncio.Event()
        active_sends[chat_id] = {
            "event": cancel_event,
            "task": asyncio.current_task(),
            "id": task_id
        }

    status_msg = None
    files_sent = []

    try:
        data_parts = callback_query.data.split("_")
        tmdb_id = int(data_parts[2])
        db_index = int(data_parts[3])
        s_num = int(data_parts[4])

        media = await db.get_document("tv", tmdb_id, db_index)
        if not media:
            await callback_query.answer("Media not found.", show_alert=True)
            return # active_sends cleaned up in finally

        target_season = next((s for s in media.get("seasons", []) if s.get("season_number") == s_num), None)
        if not target_season:
            await callback_query.answer("Season not found.", show_alert=True)
            return

        episodes = target_season.get("episodes", [])
        episodes.sort(key=lambda x: x.get("episode_number", 0))

        if not episodes:
            await callback_query.answer("No episodes found.", show_alert=True)
            return

        await callback_query.answer("🚀 Starting full season send...", show_alert=False)

        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Sending", callback_data=f"cancel_send_{task_id}")]])
        status_msg = await client.send_message(
            chat_id,
            f"🚀 **Preparing Season {s_num:02}...**\n\nTotal Episodes: {len(episodes)}",
            reply_markup=cancel_btn
        )

        total_files = 0
        for ep in episodes:
            total_files += len(ep.get("telegram", []))

        if total_files == 0:
             await safe_edit_message(status_msg, "❌ No files found for this season.")
             return

        current_count = 0

        for ep in episodes:
            if cancel_event.is_set():
                break

            e_num = ep.get("episode_number")
            files_data = ep.get("telegram", [])

            for file_info in files_data:
                if cancel_event.is_set():
                    break

                try:
                    encoded_id = file_info.get("id")
                    file_id_data = await decode_string(encoded_id)
                    src_chat_id = int(f"-100{file_id_data['chat_id']}")
                    src_msg_id = int(file_id_data['msg_id'])

                    # Rate Limiting with Cancel Check
                    if await interruptible_sleep(SEND_RATE_LIMIT, cancel_event):
                        break

                    sent_msg = await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=src_chat_id,
                        message_id=src_msg_id,
                        caption=f"📺 {media['title']} - S{s_num:02}E{e_num:02}\n💾 {file_info.get('quality', 'Unknown')} - {file_info.get('size', '')}"
                    )
                    files_sent.append(sent_msg.id)
                    current_count += 1

                    if current_count % PROGRESS_UPDATE_INTERVAL == 0 or current_count == total_files:
                        await safe_edit_message(
                            status_msg,
                            f"🚀 **Sending Season {s_num:02}**\n\n"
                            f"📤 Sent: {current_count}/{total_files} files\n"
                            f"⏳ Please wait...",
                            reply_markup=cancel_btn
                        )

                except FloodWait as e:
                    wait_time = e.value + 2
                    await safe_edit_message(status_msg, f"⏳ Telegram FloodWait hit. Sleeping for {wait_time}s...")
                    if await interruptible_sleep(wait_time, cancel_event):
                        break
                except Exception as e:
                    print(f"Error sending file S{s_num}E{e_num}: {e}")

        if cancel_event.is_set():
            await safe_edit_message(status_msg, "❌ **Operation Cancelled.**", reply_markup=None)
        else:
            await safe_delete_messages(client, chat_id, [status_msg.id])

        if files_sent:
            warning = await client.send_message(
                chat_id,
                f"✅ **Season {s_num:02} Sent!**\n\n"
                f"⚠️ **Warning:** {len(files_sent)} files will be deleted in **{AUTO_DELETE_DELAY//60} minutes**.\n"
                "📥 **Save them to your Saved Messages immediately!**"
            )
            asyncio.create_task(schedule_deletion(client, chat_id, files_sent + [warning.id], delay=AUTO_DELETE_DELAY))

    except Exception as e:
        print(f"Global Error in tv_full_season_handler: {e}")
        if status_msg:
            await safe_edit_message(status_msg, f"❌ Error occurred: {e}")

    finally:
        async with active_sends_lock:
            if chat_id in active_sends and active_sends[chat_id]["id"] == task_id:
                del active_sends[chat_id]

@Client.on_callback_query(filters.regex(r"^cancel_send_(.*)$"))
async def cancel_send_handler(client: Client, callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id
    target_task_id = callback_query.data.split("_")[-1]

    async with active_sends_lock:
        if chat_id in active_sends:
            task_info = active_sends[chat_id]
            if task_info["id"] == target_task_id:
                task_info["event"].set()
                await callback_query.answer("Stopping... Please wait.", show_alert=True)
                return

    await callback_query.answer("Task expired or already finished.", show_alert=True)
    try:
         await callback_query.message.delete()
    except:
        pass
