import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)

from pyrogram.errors.pyromod.listener_timeout import ListenerTimeout

from Backend.helper.custom_filter import CustomFilters
from Backend.scrapper import ScrapperService
from Backend import db

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

@Client.on_message(
    filters.command("scrapper") & filters.private & CustomFilters.owner,
    group=-10
)
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

    # ---------------- CLOSE ----------------
    if data == "scr_close":
        await msg.delete()
        return

    # ---------------- BACK ----------------
    if data == "scr_menu":
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
        try:
            reply: Message = await client.ask(
                chat_id=msg.chat.id,
                text=(
                    "➕ **Add Source Channel**\n\n"
                    "Send the **Channel ID** now.\n\n"
                    "`-100xxxxxxxxxx`\n"
                    "⏱ Timeout: 60 seconds"
                ),
                filters=filters.text,
                timeout=60,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except ListenerTimeout:
            await msg.edit_text(
                "⌛ **Timed out**\n\nReturning to menu.",
                reply_markup=scr_main_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        # cleanup ask messages
        try:
            prompt = reply.reply_to_message
            await reply.delete()
            if prompt:
                await prompt.delete()
        except Exception:
            pass

        try:
            channel_id = int(reply.text.strip())
            added = await db.add_source_channel(channel_id)
            result = (
                f"✅ Source `{channel_id}` added."
                if added else
                "⚠️ Channel already exists."
            )
        except ValueError:
            result = "❌ **Invalid Channel ID**"

        await msg.edit_text(
            result,
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- REMOVE SOURCE ----------------
    if data == "scr_del":
        try:
            reply: Message = await client.ask(
                chat_id=msg.chat.id,
                text=(
                    "➖ **Remove Source Channel**\n\n"
                    "Send the **Channel ID** now.\n\n"
                    "⏱ Timeout: 60 seconds"
                ),
                filters=filters.text,
                timeout=60,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except ListenerTimeout:
            await msg.edit_text(
                "⌛ **Timed out**\n\nReturning to menu.",
                reply_markup=scr_main_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        # cleanup
        try:
            prompt = reply.reply_to_message
            await reply.delete()
            if prompt:
                await prompt.delete()
        except Exception:
            pass

        try:
            channel_id = int(reply.text.strip())
            removed = await db.remove_source_channel(channel_id)
            result = (
                f"✅ Source `{channel_id}` removed."
                if removed else
                "⚠️ Channel not found."
            )
        except ValueError:
            result = "❌ **Invalid Channel ID**"

        await msg.edit_text(
            result,
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return
