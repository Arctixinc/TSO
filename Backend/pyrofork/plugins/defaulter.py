import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

from Backend import db
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.defaulter_helper import DefaulterManager
from Backend.helper.metadata import fetch_movie_metadata, fetch_tv_metadata
from Backend.helper.encrypt import encode_string
from Backend.logger import LOGGER

# State management for candidates only
# {user_id: {"uid": "file_unique_id", "candidates": {...}}}
USER_STATE = {}

# Pagination size
GRID_SIZE = 16

@Client.on_message(filters.command("defaulter") & CustomFilters.owner)
async def list_defaulters(client, message):
    await show_defaulter_page(client, message.chat.id, 0)

async def show_defaulter_page(client, chat_id, page):
    defaulters = await DefaulterManager.get_defaulters(page, GRID_SIZE)
    count = await DefaulterManager.get_defaulter_count()

    if not defaulters:
        return await client.send_message(chat_id, "✅ No unmatched files found.")

    buttons = []
    # 4x4 Grid
    row = []
    for d in defaulters:
        # Shorten name for button
        name = d.get("file_name", "Unknown")[:15]
        row.append(InlineKeyboardButton(name, callback_data=f"def_show|{d['file_unique_id']}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"def_page|{page-1}"))
    if (page + 1) * GRID_SIZE < count:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"def_page|{page+1}"))
    if nav:
        buttons.append(nav)

    await client.send_message(
        chat_id,
        f"📂 **Unmatched Files** (Total: {count})\nSelect a file to manage:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex(r"^def_"))
async def defaulter_callback(client: Client, query: CallbackQuery):
    data = query.data.split("|")
    action = data[0]

    if action == "def_page":
        await query.message.delete()
        await show_defaulter_page(client, query.message.chat.id, int(data[1]))

    elif action == "def_show":
        uid = data[1]
        doc = await DefaulterManager.get_defaulter_by_id(uid)
        if not doc:
            return await query.answer("❌ File no longer exists.", show_alert=True)

        text = (
            f"📄 **File:** `{doc['file_name']}`\n"
            f"🆔 **Msg ID:** `{doc['message_id']}`\n"
        )
        btns = [
            [InlineKeyboardButton("🔧 Manual Match", callback_data=f"def_man|{uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="def_page|0")]
        ]
        await query.message.edit(text, reply_markup=InlineKeyboardMarkup(btns))

    elif action == "def_man":
        uid = data[1]
        doc = await DefaulterManager.get_defaulter_by_id(uid)
        if not doc:
            return await query.answer("❌ File not found.", show_alert=True)

        await query.answer() # Answer prompt immediately

        # Ask user for ID
        prompt_msg = await query.message.reply(
            "🆔 **Please send the TMDB ID or IMDB ID.**\n"
            "Examples:\n"
            "- `tt1234567` (IMDb)\n"
            "- `550` (TMDb ID)\n\n"
            "Send /cancel to abort."
        )

        try:
            reply = await client.ask(
                chat_id=query.message.chat.id,
                text="",
                user_id=query.from_user.id,
                filters=filters.text,
                timeout=60
            )
            input_id = reply.text.strip()
            await reply.delete()
            await prompt_msg.delete()

            if input_id.lower() == "/cancel":
                return await query.message.reply("❌ Cancelled.")

        except asyncio.TimeoutError:
            await prompt_msg.delete()
            return await query.message.reply("❌ Timeout. Please try again.")
        except Exception as e:
            LOGGER.error(f"Error in client.ask: {e}")
            await prompt_msg.delete()
            return await query.message.reply("❌ Error occurred.")

        # Process Input
        status = await query.message.reply("🔍 Searching...")

        encoded = await encode_string({"chat_id": doc["chat_id"], "msg_id": doc["message_id"]})
        movie_meta = await fetch_movie_metadata(doc["file_name"], encoded, default_id=input_id)
        tv_meta = await fetch_tv_metadata(doc["file_name"], season=1, episode=1, encoded_string=encoded, default_id=input_id)

        btns = []

        # We need to store context for the next step (selection/confirmation)
        # Re-using USER_STATE logic just for candidates is fine
        if query.from_user.id not in USER_STATE:
            USER_STATE[query.from_user.id] = {}

        USER_STATE[query.from_user.id]["uid"] = uid
        USER_STATE[query.from_user.id]["candidates"] = {}

        if movie_meta and movie_meta.get("tmdb_id"):
            USER_STATE[query.from_user.id]["candidates"]["movie"] = movie_meta
            btns.append([InlineKeyboardButton(f"🎬 Movie: {movie_meta['title']}", callback_data=f"def_sel|{uid}|movie")])

        if tv_meta and tv_meta.get("tmdb_id"):
            USER_STATE[query.from_user.id]["candidates"]["tv"] = tv_meta
            btns.append([InlineKeyboardButton(f"📺 TV: {tv_meta['title']}", callback_data=f"def_sel|{uid}|tv")])

        btns.append([InlineKeyboardButton("❌ Cancel", callback_data="def_cancel")])

        if not (movie_meta or tv_meta):
            await status.edit(
                "❌ No results found for that ID. Try again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"def_show|{uid}")]])
            )
            return

        await status.edit(
            "✅ **Result Found!**\nPlease select the correct match:",
            reply_markup=InlineKeyboardMarkup(btns)
        )

    elif action == "def_cancel":
        if query.from_user.id in USER_STATE:
            del USER_STATE[query.from_user.id]
        await query.message.delete()
        await show_defaulter_page(client, query.message.chat.id, 0)

    elif action == "def_sel":
        # def_sel|uid|type
        uid = data[1]
        m_type = data[2]

        state = USER_STATE.get(query.from_user.id)
        if not state or "candidates" not in state:
             return await query.answer("❌ Session expired.", show_alert=True)

        meta = state["candidates"].get(m_type)
        if not meta:
             return await query.answer("❌ Error retrieving data.", show_alert=True)

        # Store selected meta as final
        USER_STATE[query.from_user.id]["meta"] = meta

        # Ask for final confirmation
        await query.message.edit(
            f"✅ **Selected:** {meta['title']} ({m_type.upper()})\n"
            f"Is this correct?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, Add it", callback_data=f"def_confirm|{uid}|{m_type}|{meta['tmdb_id']}")],
                [InlineKeyboardButton("❌ No, Retry", callback_data=f"def_man|{uid}")]
            ])
        )

    elif action == "def_confirm":
        # Format: def_confirm|uid|type|tmdb_id
        uid = data[1]
        m_type = data[2]
        tmdb_id = data[3]

        doc = await DefaulterManager.get_defaulter_by_id(uid)
        if not doc:
            return await query.answer("❌ File not found.", show_alert=True)

        # Retrieve stored metadata from state
        state = USER_STATE.get(query.from_user.id)
        if not state or state.get("meta", {}).get("tmdb_id") != int(tmdb_id):
             return await query.answer("❌ Session expired or mismatch. Try again.", show_alert=True)

        meta = state["meta"]

        # Add to DB
        await db.insert_media(
            metadata_info=meta,
            channel=doc["chat_id"],
            msg_id=doc["message_id"],
            size="0", # We don't have size handy here unless we fetch msg or store it.
            name=doc["file_name"]
        )

        # Remove from Defaulters
        await DefaulterManager.remove_defaulter(uid)
        if query.from_user.id in USER_STATE:
            del USER_STATE[query.from_user.id]

        await query.message.edit("✅ **Successfully added to Database!**")
