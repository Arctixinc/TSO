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
# UI MARKUPS
# ==================================================

SCRAPPER_MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 Start Scan", callback_data="scr_start")],
    [InlineKeyboardButton("📋 View Sources", callback_data="scr_list")],
    [
        InlineKeyboardButton("➕ Add Source", callback_data="scr_add"),
        InlineKeyboardButton("➖ Remove Source", callback_data="scr_del")
    ],
    [InlineKeyboardButton("❌ Close", callback_data="scr_close")]
])

BACK_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔙 Back", callback_data="scr_menu")]
])

# ==================================================
# ENTRY COMMAND
# ==================================================

@Client.on_message(filters.command("scrapper") & filters.private & CustomFilters.owner)
async def scrapper_entry(client: Client, message: Message):
    await message.reply_text(
        "🤖 **Scrapper Manager**\n\n"
        "Manage and control the scrapper service using the buttons below.",
        reply_markup=SCRAPPER_MAIN_MENU,
        parse_mode=enums.ParseMode.MARKDOWN
    )

# ==================================================
# CALLBACK HANDLER
# ==================================================

@Client.on_callback_query(filters.regex("^scr_"))
async def scrapper_callback(client: Client, query: CallbackQuery):
    await query.answer()
    data = query.data

    # --------------------------------------------------
    # CLOSE
    # --------------------------------------------------
    if data == "scr_close":
        await query.message.delete()
        return

    # --------------------------------------------------
    # BACK TO MAIN MENU
    # --------------------------------------------------
    if data == "scr_menu":
        await query.message.edit_text(
            "🤖 **Scrapper Manager**\n\nSelect an action:",
            reply_markup=SCRAPPER_MAIN_MENU,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # --------------------------------------------------
    # START SCRAPPER
    # --------------------------------------------------
    if data == "scr_start":
        if not ScrapperService.user_client:
            await ScrapperService.start_user_client()
            if not ScrapperService.user_client:
                await query.message.edit_text(
                    "❌ **Failed to start User Client**\n\n"
                    "Check `USER_SESSION_STRING`.",
                    reply_markup=BACK_MENU,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                return

        await query.message.edit_text("🚀 **Starting Scrapper Scan...**")
        asyncio.create_task(
            ScrapperService.scan_sources(status_msg=query.message)
        )
        return

    # --------------------------------------------------
    # LIST SOURCES
    # --------------------------------------------------
    if data == "scr_list":
        channels = await db.get_source_channels()

        if not channels:
            text = "ℹ️ **No source channels configured.**"
        else:
            text = "📋 **Source Channels**\n\n"
            for ch in channels:
                text += f"• `{ch}`\n"

        await query.message.edit_text(
            text,
            reply_markup=BACK_MENU,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # --------------------------------------------------
    # ADD SOURCE (ASK)
    # --------------------------------------------------
    if data == "scr_add":
        try:
            reply = await client.ask(
                chat_id=query.message.chat.id,
                text=(
                    "➕ **Add Source Channel**\n\n"
                    "Send the **Channel ID** to add:\n\n"
                    "`-100xxxxxxxxxx`"
                ),
                filters=filters.text,
                timeout=60,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except TimeoutError:
            await query.message.edit_text("❌ Timeout.", reply_markup=BACK_MENU)
            return

        try:
            channel_id = int(reply.text.strip())
            added = await db.add_source_channel(channel_id)
            result = (
                f"✅ Source `{channel_id}` added."
                if added else
                "⚠️ Channel already exists."
            )
        except ValueError:
            result = "❌ Invalid Channel ID."

        # 🧹 cleanup ask messages
        try:
            await reply.delete()
            if reply.reply_to_message:
                await reply.reply_to_message.delete()
        except:
            pass

        await query.message.edit_text(
            result,
            reply_markup=BACK_MENU,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # --------------------------------------------------
    # REMOVE SOURCE (ASK)
    # --------------------------------------------------
    if data == "scr_del":
        try:
            reply = await client.ask(
                chat_id=query.message.chat.id,
                text=(
                    "➖ **Remove Source Channel**\n\n"
                    "Send the **Channel ID** to remove:"
                ),
                filters=filters.text,
                timeout=60,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except TimeoutError:
            await query.message.edit_text("❌ Timeout.", reply_markup=BACK_MENU)
            return

        try:
            channel_id = int(reply.text.strip())
            removed = await db.remove_source_channel(channel_id)
            result = (
                f"✅ Source `{channel_id}` removed."
                if removed else
                "⚠️ Channel not found."
            )
        except ValueError:
            result = "❌ Invalid Channel ID."

        # 🧹 cleanup
        try:
            await reply.delete()
            if reply.reply_to_message:
                await reply.reply_to_message.delete()
        except:
            pass

        await query.message.edit_text(
            result,
            reply_markup=BACK_MENU,
            parse_mode=enums.ParseMode.MARKDOWN
        )
