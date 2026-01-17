import asyncio
import time
from datetime import datetime

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

# ==================================================
# CALLBACK DATA (COMPRESSED & SAFE)
# ==================================================
# Format: "scr|<action>|<arg>"
# Examples:
# scr|m              → main menu
# scr|mo             → mode
# scr|p|2            → picker page 2
# scr|b|mo           → back to mode
# scr|s|123          → scan channel 123
# scr|sa             → scan all
# scr|c              → cancel

def cb(*parts):
    return "scr|" + "|".join(map(str, parts))

def parse_cb(data: str):
    return data[4:].split("|")

# ==================================================
# UI BUILDERS
# ==================================================

def back_btn(target: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data=cb("b", target))]
    ])

def scr_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Scan", callback_data=cb("mo"))],
        [InlineKeyboardButton("📋 View Sources", callback_data=cb("ls"))],
        [
            InlineKeyboardButton("➕ Add Source", callback_data=cb("add")),
            InlineKeyboardButton("➖ Remove Source", callback_data=cb("del"))
        ],
        [InlineKeyboardButton("❌ Close", callback_data=cb("x"))]
    ])

def scr_mode_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Scan All Channels", callback_data=cb("sa"))],
        [InlineKeyboardButton("🎯 Scan Single Channel", callback_data=cb("p", 0))],
        [InlineKeyboardButton("🔙 Back", callback_data=cb("b", "m"))]
    ])

def scr_channel_picker(channels, page=0):
    PER_PAGE = 10
    total_pages = (len(channels) + PER_PAGE - 1) // PER_PAGE
    start = page * PER_PAGE
    end = start + PER_PAGE

    buttons = []

    for ch_id in channels[start:end]:
        buttons.append([
            InlineKeyboardButton(
                f"ID: {ch_id}",
                callback_data=cb("s", ch_id)
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=cb("p", page - 1)))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=cb("p", page + 1)))
    if nav:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data=cb("b", "mo"))
    ])

    return InlineKeyboardMarkup(buttons)

def scr_running_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancel Scan", callback_data=cb("c"))]
    ])

# ==================================================
# ENTRY
# ==================================================

@Client.on_message(
    filters.command("scrapper") & filters.private & CustomFilters.owner,
    group=-10
)
async def scrapper_entry(client: Client, message: Message):
    await message.reply_text(
        "🤖 **Scrapper Manager**\n\nSelect an action:",
        reply_markup=scr_main_menu(),
        parse_mode=enums.ParseMode.MARKDOWN
    )

# ==================================================
# CALLBACK HANDLER
# ==================================================

@Client.on_callback_query(filters.regex("^scr\\|"))
async def scrapper_callback(client: Client, query: CallbackQuery):
    await query.answer()

    msg = query.message
    user_id = query.from_user.id
    parts = parse_cb(query.data)
    action = parts[0]

    # ---------------- CLOSE ----------------
    if action == "x":
        await msg.delete()
        return

    # ---------------- BACK (STATELESS) ----------------
    if action == "b":
        target = parts[1]

        if target == "m":
            await msg.edit_text(
                "🤖 **Scrapper Manager**\n\nSelect an action:",
                reply_markup=scr_main_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )

        elif target == "mo":
            await msg.edit_text(
                "⚙️ **Select Scan Mode**",
                reply_markup=scr_mode_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )

        elif target == "p":
            page = int(parts[2])
            channels = await db.get_source_channels()
            await msg.edit_text(
                f"🎯 **Select a Channel** (Page {page + 1})",
                reply_markup=scr_channel_picker(channels, page),
                parse_mode=enums.ParseMode.MARKDOWN
            )
        return

    # ---------------- MODE ----------------
    if action == "mo":
        await msg.edit_text(
            "⚙️ **Select Scan Mode**",
            reply_markup=scr_mode_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- PICKER ----------------
    if action == "p":
        page = int(parts[1])
        channels = await db.get_source_channels()

        if not channels:
            await msg.edit_text(
                "ℹ️ **No source channels found.**",
                reply_markup=back_btn("m"),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        await msg.edit_text(
            f"🎯 **Select a Channel** (Page {page + 1})",
            reply_markup=scr_channel_picker(channels, page),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- LIST SOURCES ----------------
    if action == "ls":
        channels = await db.get_source_channels()
        text = (
            "ℹ️ **No source channels configured.**"
            if not channels
            else "📋 **Source Channels**\n\n" + "\n".join(f"• `{c}`" for c in channels)
        )
        await msg.edit_text(
            text,
            reply_markup=back_btn("m"),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- ADD SOURCE ----------------
    if action == "add":
        await msg.edit_text(
            "➕ **Add Source Channel**\n\nSend the **Channel ID**",
            reply_markup=back_btn("m"),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        try:
            reply = await client.ask(
                chat_id=msg.chat.id,
                text="",
                user_id=user_id,
                filters=filters.text,
                timeout=60
            )
            channel_id = int(reply.text.strip())
            added = await db.add_source_channel(channel_id)
            result = "✅ Source added." if added else "⚠️ Channel already exists."
            await reply.delete()
        except Exception:
            result = "❌ Invalid input."

        await msg.edit_text(
            result,
            reply_markup=scr_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- REMOVE SOURCE ----------------
    if action == "del":
        await msg.edit_text(
            "➖ **Remove Source Channel**\n\nSend the **Channel ID**",
            reply_markup=back_btn("m"),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        try:
            reply = await client.ask(
                chat_id=msg.chat.id,
                text="",
                user_id=user_id,
                filters=filters.text,
                timeout=60
            )
            channel_id = int(reply.text.strip())
            removed = await db.remove_source_channel(channel_id)
            result = "✅ Source removed." if removed else "⚠️ Channel not found."
            await reply.delete()
        except Exception:
            result = "❌ Invalid input."

        await msg.edit_text(
            result,
            reply_markup=scr_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------------- START SCAN ----------------
    if action in ("sa", "s"):
        if not ScrapperService.user_client:
            await ScrapperService.start_user_client()

        target_channels = None
        if action == "s":
            target_channels = [int(parts[1])]

        cancel_event = asyncio.Event()
        SCR_RUNNING_TASKS[user_id] = cancel_event

        start_ts = time.time()

        await msg.edit_text(
            "🚀 **Initializing Scan...**",
            reply_markup=scr_running_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )

        async def progress_callback(payload: dict):
            status = payload.get("status", "")

            try:
                if status == "completed":
                    end_ts = time.time()
                    duration = int(end_ts - start_ts)
                    mins, secs = divmod(duration, 60)
                    finished_at = datetime.now().strftime("%d %b %Y, %H:%M:%S")

                    text = (
                        "✅ **Scan Completed**\n\n"
                        f"⏱ **Duration:** {mins}m {secs}s\n"
                        f"🕒 **Finished At:** `{finished_at}`\n"
                        f"📊 **Total Scanned:** `{payload.get('total_scanned', 0)}`\n"
                        f"📤 **Total Copied:** `{payload.get('total_copied', 0)}`"
                    )

                    await msg.edit_text(
                        text,
                        reply_markup=back_btn("m"),
                        parse_mode=enums.ParseMode.MARKDOWN
                    )
                else:
                    await msg.edit_text(
                        payload.get("message", status),
                        reply_markup=scr_running_menu(),
                        parse_mode=enums.ParseMode.MARKDOWN
                    )
            except MessageNotModified:
                pass

        asyncio.create_task(
            ScrapperService.scan_sources(
                user_id=user_id,
                target_channels=target_channels,
                progress_callback=progress_callback,
                cancel_event=cancel_event
            )
        )
        return

    # ---------------- CANCEL ----------------
    if action == "c":
        evt = SCR_RUNNING_TASKS.get(user_id)
        if evt:
            evt.set()
            await query.answer("🛑 Cancelling...", show_alert=True)
        else:
            await query.answer("⚠️ No active scan.", show_alert=True)
        return
