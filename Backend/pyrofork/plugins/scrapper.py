import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.errors import MessageNotModified
from pyrogram.errors.pyromod.listener_timeout import ListenerTimeout

from Backend.helper.custom_filter import CustomFilters
from Backend.scrapper import ScrapperService, SCR_RUNNING_TASKS
from Backend import db

# Global map for cancellation events: {user_id: asyncio.Event}
# Note: This is separate from SCR_RUNNING_TASKS because the event is needed for the UI "Cancel" button
SCR_CANCEL_EVENTS = {}

# ==================================================
# UI BUILDERS
# ==================================================

def scr_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Scan", callback_data="scr_mode")],
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

def scr_mode_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Scan All Channels", callback_data="scr_scan_all")],
        [InlineKeyboardButton("🎯 Scan Single Channel", callback_data="scr_pick_page_0")],
        [InlineKeyboardButton("🔙 Back", callback_data="scr_menu")]
    ])

def scr_channel_picker(channels, page=0):
    PER_PAGE = 10
    total_pages = (len(channels) + PER_PAGE - 1) // PER_PAGE
    start = page * PER_PAGE
    end = start + PER_PAGE
    current_batch = channels[start:end]

    buttons = []
    for ch_id in current_batch:
        buttons.append([InlineKeyboardButton(f"ID: {ch_id}", callback_data=f"scr_scan_{ch_id}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"scr_pick_page_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"scr_pick_page_{page+1}"))

    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="scr_mode")])
    return InlineKeyboardMarkup(buttons)

def scr_running_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancel Scan", callback_data="scr_cancel")]
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
    user_id = query.from_user.id

    # ---------------- CLOSE ----------------
    if data == "scr_close":
        await msg.delete()
        return

    # ---------------- BACK TO MAIN ----------------
    if data == "scr_menu":
        await msg.edit_text(
            "🤖 **Scrapper Manager**\n\nSelect an action:",
            reply_markup=scr_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- MODE SELECTION ----------------
    if data == "scr_mode":
        await msg.edit_text(
            "⚙️ **Select Scan Mode**",
            reply_markup=scr_mode_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- CHANNEL PICKER (PAGINATION) ----------------
    if data.startswith("scr_pick_page_"):
        page = int(data.split("_")[-1])
        channels = await db.get_source_channels()
        if not channels:
            await msg.edit_text(
                "ℹ️ **No source channels found.**",
                reply_markup=scr_back_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        await msg.edit_text(
            f"🎯 **Select a Channel to Scan** (Page {page + 1})",
            reply_markup=scr_channel_picker(channels, page),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- START SCAN (SINGLE OR ALL) ----------------
    if data == "scr_scan_all" or data.startswith("scr_scan_"):

        # Check if user client exists
        if not ScrapperService.user_client:
            await ScrapperService.start_user_client()
            if not ScrapperService.user_client:
                await msg.edit_text(
                    "❌ **Failed to start User Client**\n\nCheck `USER_SESSION_STRING`.",
                    reply_markup=scr_main_menu(),
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                return

        target_channels = None
        if data.startswith("scr_scan_") and data != "scr_scan_all":
            try:
                target_channels = [int(data.split("_")[-1])]
            except ValueError:
                return

        # Setup Cancellation Event
        cancel_event = asyncio.Event()
        SCR_CANCEL_EVENTS[user_id] = cancel_event

        await msg.edit_text(
            "🚀 **Initializing Scan...**",
            reply_markup=scr_running_menu()
        )

        # Progress Callback
        async def progress_callback(payload: dict):
            status = payload.get("status")
            text = ""

            if status == "starting":
                count = payload.get("channel_count")
                text = f"🚀 **Starting Scan**\n\nChannels: `{count}`"

            elif status == "running":
                ch_name = payload.get("channel_name", "Unknown")
                scanned = payload.get("scanned", 0)
                copied = payload.get("copied", 0)
                remaining = payload.get("remaining", 0)

                text = (
                    f"🔄 **Scanning In Progress**\n\n"
                    f"📺 **Channel:** `{ch_name}`\n"
                    f"📨 **Remaining Backlog:** `{remaining}`\n"
                    f"📊 **Scanned:** `{scanned}`\n"
                    f"📤 **Copied:** `{copied}`"
                )

            elif status == "floodwait":
                wait = payload.get("wait_time", 0)
                text = f"⏳ **FloodWait Detected**\n\nSleeping for `{wait}` seconds..."

            elif status == "completed":
                scanned = payload.get("total_scanned", 0)
                copied = payload.get("total_copied", 0)
                text = (
                    f"✅ **Scan Completed**\n\n"
                    f"📊 Total Scanned: `{scanned}`\n"
                    f"📤 Total Copied: `{copied}`"
                )

            elif status == "cancelled":
                text = "🚫 **Scan Cancelled by User**"

            elif status == "error":
                err = payload.get("message", "Unknown Error")
                text = f"❌ **Error**\n\n{err}"

            # Update Message
            try:
                if status in ["completed", "cancelled", "error"]:
                    await msg.edit_text(text, reply_markup=scr_back_menu(), parse_mode=enums.ParseMode.MARKDOWN)
                else:
                    await msg.edit_text(text, reply_markup=scr_running_menu(), parse_mode=enums.ParseMode.MARKDOWN)
            except MessageNotModified:
                pass
            except Exception as e:
                # Log but don't crash
                print(f"UI Update Error: {e}")

        # Start background task
        asyncio.create_task(
            ScrapperService.scan_sources(
                user_id=user_id,
                target_channels=target_channels,
                progress_callback=progress_callback,
                cancel_event=cancel_event
            )
        )
        return

    # ---------------- CANCEL SCAN ----------------
    if data == "scr_cancel":
        evt = SCR_CANCEL_EVENTS.get(user_id)
        if evt:
            evt.set()
            await query.answer("🛑 Cancelling...", show_alert=True)
        else:
            await query.answer("⚠️ No active scan to cancel.", show_alert=True)
            await msg.edit_text(
                "⚠️ **No active scan found.**",
                reply_markup=scr_main_menu()
            )
        return

    # ---------------- LIST SOURCES ----------------
    if data == "scr_list":
        channels = await db.get_source_channels()
        text = (
            "ℹ️ **No source channels configured.**"
            if not channels
            else "📋 **Source Channels**\n\n" + "\n".join(f"• `{c}`" for c in channels)
        )
        await msg.edit_text(
            text,
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- ADD SOURCE ----------------
    if data == "scr_add":
        await msg.edit_text(
            "➕ **Add Source Channel**\n\n"
            "Send the **Channel ID** now.\n"
            "`-100xxxxxxxxxx`\n"
            "⏱ Timeout: 60 seconds",
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        try:
            reply: Message = await client.ask(
                chat_id=msg.chat.id,
                text="",
                user_id=user_id,
                filters=filters.text,
                timeout=60
            )
        except ListenerTimeout:
            await msg.edit_text(
                "⌛ **Timed out**\n\nSelect an action:",
                reply_markup=scr_main_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        try:
            channel_id = int(reply.text.strip())
            added = await db.add_source_channel(channel_id)
            result = (f"✅ Source `{channel_id}` added." if added else "⚠️ Channel already exists.")
        except ValueError:
            result = "❌ **Invalid Channel ID**"

        try:
            await reply.delete()
        except Exception:
            pass

        await msg.edit_text(
            result + "\n\nSelect an action:",
            reply_markup=scr_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- REMOVE SOURCE ----------------
    if data == "scr_del":
        await msg.edit_text(
            "➖ **Remove Source Channel**\n\n"
            "Send the **Channel ID** now.\n"
            "⏱ Timeout: 60 seconds",
            reply_markup=scr_back_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        try:
            reply: Message = await client.ask(
                chat_id=msg.chat.id,
                text="",
                user_id=user_id,
                filters=filters.text,
                timeout=60
            )
        except ListenerTimeout:
            await msg.edit_text(
                "⌛ **Timed out**\n\nSelect an action:",
                reply_markup=scr_main_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        try:
            channel_id = int(reply.text.strip())
            removed = await db.remove_source_channel(channel_id)
            result = (f"✅ Source `{channel_id}` removed." if removed else "⚠️ Channel not found.")
        except ValueError:
            result = "❌ **Invalid Channel ID**"

        try:
            await reply.delete()
        except Exception:
            pass

        await msg.edit_text(
            result + "\n\nSelect an action:",
            reply_markup=scr_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return
