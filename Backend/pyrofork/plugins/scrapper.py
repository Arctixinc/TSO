from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums.parse_mode import ParseMode

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
from Backend.scrapper import ScrapperService
import asyncio

SCRAPPER_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 Start Scan", callback_data="scrapper_start")],
    [InlineKeyboardButton("📋 List Sources", callback_data="scrapper_list")],
    [
        InlineKeyboardButton("➕ Add Source", callback_data="scrapper_add"),
        InlineKeyboardButton("➖ Remove Source", callback_data="scrapper_del")
    ],
    [InlineKeyboardButton("❌ Close", callback_data="scrapper_close")]
])

@Client.on_message(filters.command('scrapper') & filters.private & CustomFilters.owner)
async def scrapper_handler(client: Client, message: Message):
    """
    Manage Scrapper Service.
    Usage:
        /scrapper - Show Menu
        /scrapper start - Start scanning
        /scrapper add <channel_id> - Add source channel
        /scrapper del <channel_id> - Remove source channel
        /scrapper list - List source channels
    """
    args = message.text.split()

    # If no args, show menu
    if len(args) < 2:
        return await message.reply_text(
            "🤖 **Scrapper Manager**\n\nSelect an action:",
            reply_markup=SCRAPPER_MENU,
            parse_mode=ParseMode.MARKDOWN
        )

    cmd = args[1].lower()

    if cmd == "start":
        if not ScrapperService.user_client:
            await ScrapperService.start_user_client()

        if not ScrapperService.user_client:
             return await message.reply_text("❌ Failed to start User Client. Check USER_SESSION_STRING.")

        msg = await message.reply_text("🚀 **Starting Scrapper...**")
        asyncio.create_task(ScrapperService.scan_sources(status_msg=msg))

    elif cmd == "add":
        if len(args) < 3:
             return await message.reply_text("Usage: `/scrapper add <channel_id>`")
        try:
            channel_id = int(args[2])
            if await db.add_source_channel(channel_id):
                await message.reply_text(f"✅ Source channel {channel_id} added.")
            else:
                await message.reply_text(f"❌ Failed to add channel {channel_id}.")
        except ValueError:
            await message.reply_text("⚠️ Invalid Channel ID.")

    elif cmd == "del":
        if len(args) < 3:
             return await message.reply_text("Usage: `/scrapper del <channel_id>`")
        try:
            channel_id = int(args[2])
            if await db.remove_source_channel(channel_id):
                await message.reply_text(f"✅ Source channel {channel_id} removed.")
            else:
                await message.reply_text(f"❌ Failed to remove channel {channel_id} (or not found).")
        except ValueError:
            await message.reply_text("⚠️ Invalid Channel ID.")

    elif cmd == "list":
        channels = await db.get_source_channels()
        if not channels:
            await message.reply_text("ℹ️ No source channels configured.")
        else:
            text = "**Source Channels:**\n\n"
            for ch in channels:
                text += f"• `{ch}`\n"
            await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    else:
        # Unknown command, show menu fallback
        await message.reply_text(
            "⚠️ Unknown command. Select an action:",
            reply_markup=SCRAPPER_MENU,
            parse_mode=ParseMode.MARKDOWN
        )

@Client.on_callback_query(filters.regex(r"^scrapper_"))
async def scrapper_callback(client: Client, query: CallbackQuery):
    data = query.data

    if data == "scrapper_start":
        if not ScrapperService.user_client:
            await ScrapperService.start_user_client()
            if not ScrapperService.user_client:
                return await query.message.edit_text("❌ Failed to start User Client. Check `USER_SESSION_STRING`.")

        await query.message.edit_text("🚀 **Starting Scrapper...**")
        # Start task and pass the message for progress updates
        asyncio.create_task(ScrapperService.scan_sources(status_msg=query.message))

    elif data == "scrapper_list":
        channels = await db.get_source_channels()
        if not channels:
            text = "ℹ️ No source channels configured."
        else:
            text = "**Source Channels:**\n\n"
            for ch in channels:
                text += f"• `{ch}`\n"

        # Add back button
        back_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="scrapper_menu")]
        ])
        await query.message.edit_text(text, reply_markup=back_markup, parse_mode=ParseMode.MARKDOWN)

    elif data == "scrapper_add":
        text = (
            "➕ **Add Source Channel**\n\n"
            "To add a channel, send the command:\n"
            "`/scrapper add <channel_id>`\n\n"
            "Example: `/scrapper add -1001234567890`"
        )
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="scrapper_menu")]
        ]), parse_mode=ParseMode.MARKDOWN)

    elif data == "scrapper_del":
        text = (
            "➖ **Remove Source Channel**\n\n"
            "To remove a channel, send the command:\n"
            "`/scrapper del <channel_id>`\n\n"
            "Example: `/scrapper del -1001234567890`"
        )
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="scrapper_menu")]
        ]), parse_mode=ParseMode.MARKDOWN)

    elif data == "scrapper_menu":
        await query.message.edit_text(
            "🤖 **Scrapper Manager**\n\nSelect an action:",
            reply_markup=SCRAPPER_MENU,
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "scrapper_close":
        await query.message.delete()

    await query.answer()
