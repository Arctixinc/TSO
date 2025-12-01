import asyncio
from pyrogram import filters, Client, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from Backend.helper.custom_filter import CustomFilters
from Backend.config import Telegram
from Backend.helper.encrypt import decode_string, encode_string
from Backend import db
from Backend.helper.task_manager import delete_message

# Store self-destruct tasks if needed, or just fire and forget via create_task
# For this implementation, we use create_task for fire-and-forget deletion.

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

                            # Schedule deletion
                            asyncio.create_task(schedule_deletion(client, message.chat.id, files_sent + [warning.id]))
                        else:
                            await message.reply_text("❌ Failed to retrieve files.")

                    elif media_type == "tv":
                        # Send Seasons Menu
                        seasons = media.get("seasons", [])
                        if not seasons:
                            await message.reply_text("❌ No seasons found.")
                            return

                        # Sort seasons
                        seasons.sort(key=lambda x: x.get("season_number", 0))

                        buttons = []
                        row = []
                        for s in seasons:
                            s_num = s.get("season_number")
                            # Explicitly pass page 0
                            callback_data = f"tv_S_{tmdb_id}_{db_index}_{s_num}_0"
                            row.append(InlineKeyboardButton(f"Season {s_num}", callback_data=callback_data))
                            if len(row) == 3:
                                buttons.append(row)
                                row = []
                        if row:
                            buttons.append(row)

                        await message.reply_text(
                            f"📺 **{media['title']}**\nSelect a Season:",
                            reply_markup=InlineKeyboardMarkup(buttons)
                        )

                except Exception as e:
                    await message.reply_text(f"❌ Invalid or expired link. Error: {e}")
                return

        # Default Start Message
        base_url = Telegram.BASE_URL
        addon_url = f"{base_url}/stremio/manifest.json"

        # Link to inline search
        # Better: use client.me.username
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

async def schedule_deletion(client, chat_id, message_ids):
    await asyncio.sleep(60)
    try:
        await client.delete_messages(chat_id, message_ids)
    except Exception as e:
        print(f"Failed to auto-delete messages: {e}")

# --- Callback Handlers for TV Show Navigation ---

# Regex to capture optional page number: tv_S_tmdb_dbidx_snum(_page)?
@Client.on_callback_query(filters.regex(r"^tv_S_(\d+)_(\d+)_(\d+)(?:_(\d+))?$"))
async def tv_season_handler(client: Client, callback_query: CallbackQuery):
    try:
        data_parts = callback_query.data.split("_")
        # Existing format: tv_S_tmdb_dbidx_snum (len=5, page default 0)
        # New format: tv_S_tmdb_dbidx_snum_page (len=6)

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

        # Pagination Logic
        limit = 50
        total_episodes = len(episodes)
        start_idx = page * limit
        end_idx = min(start_idx + limit, total_episodes)

        # Slice episodes for this page
        current_episodes = episodes[start_idx:end_idx]

        buttons = []
        row = []
        for ep in current_episodes:
            e_num = ep.get("episode_number")
            # data: tv_E_tmdbId_dbIndex_sNum_eNum
            cb_data = f"tv_E_{tmdb_id}_{db_index}_{s_num}_{e_num}"
            row.append(InlineKeyboardButton(f"E{e_num}", callback_data=cb_data))
            if len(row) == 5: # Compact 5 per row for numbers
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # Navigation Buttons
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"tv_S_{tmdb_id}_{db_index}_{s_num}_{page-1}"))

        if end_idx < total_episodes:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"tv_S_{tmdb_id}_{db_index}_{s_num}_{page+1}"))

        if nav_row:
            buttons.append(nav_row)

        # Back button
        buttons.append([InlineKeyboardButton("🔙 Back to Seasons", callback_data=f"tv_back_S_{tmdb_id}_{db_index}")])

        await callback_query.message.edit_text(
            f"📺 **{media['title']}** - Season {s_num} (Page {page+1})\nSelect an Episode:",
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
    # Re-show the season list
    try:
        data_parts = callback_query.data.split("_")
        # Expected: ['tv', 'back', 'S', tmdb_id, db_index]
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
            # When going back to seasons, we should reset to page 0 for episodes if clicked
            # But here we are building the SEASON list.
            # The SEASON button will link to page 0 of episodes.
            callback_data = f"tv_S_{tmdb_id}_{db_index}_{s_num}_0"
            row.append(InlineKeyboardButton(f"Season {s_num}", callback_data=callback_data))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await callback_query.message.edit_text(
            f"📺 **{media['title']}**\nSelect a Season:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        await callback_query.answer(f"Error: {e}", show_alert=True)
