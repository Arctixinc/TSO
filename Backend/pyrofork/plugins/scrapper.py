import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)

from Backend.helper.custom_filter import CustomFilters
from Backend.scrapper import ScrapperService
from Backend import db

# ==================================================
# INLINE STATE
# ==================================================
# user_id -> {action, chat_id, message_id}
SCR_EDIT_STATE: dict[int, dict] = {}

# ==================================================
# UI BUILDERS
# ==================================================

def scr_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Scan", callback_data="scr_start")],
        [InlineKeyboardButton("📋 View Sources", callback_data="scr_list")],
        [
            InlineKeyboardButton("➕ Add Source", callback_data="scr_add"),
            InlineKeyboardButton("➖ Remove Source", callback_data="scr_del")
        ],
        [InlineKeyboardButton("❌ Close", callback_data="scr_close")]
    ])


def scr_back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data="scr_menu")]
    ])

# ==================================================
# /scrapper ENTRY
# ==================================================

@Client.on_message(filters.command("scrapper") & filters.private & CustomFilters.owner)
async def scrapper_entry(client: Client, message: Message):
    await message.reply_text(
        "🤖 **Scrapper Manager**\n\n"
        "Manage and control the scrapper service below:",
        reply_markup=scr_main_menu(),
        parse_mode=enums.ParseMode.MARKDOWN
    )

# ==================================================
# CALLBACK HANDLER
# ==================================================

@Client.on_callback_query(filters.regex("^scr_"))
async def scrapper_callback(client: Client, query: CallbackQuery):
    await query.answer()
    data = query.data
    msg = query.message
    uid = query.from_user.id

    # ---------------- CLOSE ----------------
    if data == "scr_close":
        SCR_EDIT_STATE.pop(uid, None)
        await msg.delete()
        return

    # ---------------- BACK ----------------
    if data == "scr_menu":
        SCR_EDIT_STATE.pop(uid, None)
        await msg.edit_text(
            "🤖 **Scrapper Manager**\n\nSelect an action:",
            reply_markup=scr_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- START SCAN ----------------
    if data == "scr_start":
        if not ScrapperService.user_client:
            await ScrapperService.start_user_client()
            if not ScrapperService.user_client:
                await msg.edit_text(
                    "❌ **Failed to start User Client**\n\n"
                    "Check `USER_SESSION_STRING`.",
                    reply_markup=scr_back_menu(),
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                return

        await msg.edit_text("🚀 **Starting Scrapper Scan...**")
        asyncio.create_task(ScrapperService.scan_sources(status_msg=msg))
        return

    # ---------------- LIST SOURCES ----------------
    if data == "scr_list":
        channels = await db.get_source_channels()

        if not channels:
            text = "ℹ️ **No source channels configured.**"
        else:
            text = "📋 **Source Channels**\n\n"
            for ch in channels:
                text += f"• `{ch}`\n"

        await msg.edit_text(
            text,
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- ADD SOURCE ----------------
    if data == "scr_add":
        SCR_EDIT_STATE[uid] = {
            "action": "add",
            "chat_id": msg.chat.id,
            "message_id": msg.id
        }

        await msg.edit_text(
            "➕ **Add Source Channel**\n\n"
            "Send the **Channel ID** now.\n"
            "⏱ Timeout: 60 seconds",
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )

        asyncio.create_task(scr_timeout(client, uid))
        return

    # ---------------- REMOVE SOURCE ----------------
    if data == "scr_del":
        SCR_EDIT_STATE[uid] = {
            "action": "del",
            "chat_id": msg.chat.id,
            "message_id": msg.id
        }

        await msg.edit_text(
            "➖ **Remove Source Channel**\n\n"
            "Send the **Channel ID** now.\n"
            "⏱ Timeout: 60 seconds",
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )

        asyncio.create_task(scr_timeout(client, uid))
        return

# ==================================================
# TEXT INPUT HANDLER (INLINE ONLY)
# ==================================================

@Client.on_message(filters.private & filters.text & CustomFilters.owner)
async def scrapper_text_input(client: Client, message: Message):
    uid = message.from_user.id
    state = SCR_EDIT_STATE.get(uid)

    if not state:
        return

    await message.delete()  # ❌ no extra messages

    raw = message.text.strip()
    try:
        channel_id = int(raw)
    except ValueError:
        await client.edit_message_text(
            state["chat_id"],
            state["message_id"],
            "❌ **Invalid Channel ID**",
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        SCR_EDIT_STATE.pop(uid, None)
        return

    if state["action"] == "add":
        ok = await db.add_source_channel(channel_id)
        result = (
            f"✅ Source `{channel_id}` added."
            if ok else
            "⚠️ Channel already exists."
        )

    else:  # del
        ok = await db.remove_source_channel(channel_id)
        result = (
            f"✅ Source `{channel_id}` removed."
            if ok else
            "⚠️ Channel not found."
        )

    await client.edit_message_text(
        state["chat_id"],
        state["message_id"],
        result,
        reply_markup=scr_back_menu(),
        parse_mode=enums.ParseMode.MARKDOWN
    )

    SCR_EDIT_STATE.pop(uid, None)

# ==================================================
# TIMEOUT HANDLER
# ==================================================

async def scr_timeout(client: Client, user_id: int):
    await asyncio.sleep(60)

    state = SCR_EDIT_STATE.get(user_id)
    if not state:
        return

    await client.edit_message_text(
        state["chat_id"],
        state["message_id"],
        "⌛ **Timed Out**\n\nReturning to Scrapper Menu.",
        reply_markup=scr_main_menu(),
        parse_mode=enums.ParseMode.MARKDOWN
    )

    SCR_EDIT_STATE.pop(user_id, None)
