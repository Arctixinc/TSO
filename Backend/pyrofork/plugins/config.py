from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)

from asyncio import create_subprocess_exec, gather
from aiofiles import open as aiopen
from os import execl as osexecl
import shutil

from Backend.config import Telegram
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db

# ==================================================
# UI TEXT TEMPLATES
# ==================================================

CONFIG_HEADER = (
    "🧠 **Backend Configuration Panel**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "Manage system variables dynamically.\n\n"
    "Select a setting below:"
)

EDIT_TEMPLATE = (
    "✏️ **Edit Configuration**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🔧 **Variable:** `{key}`\n"
    "📌 **Current Value:** `{current}`\n\n"
    "📝 Send the **new value** below.\n"
    "⏱ Timeout: 60 seconds"
)

SUCCESS_TEMPLATE = (
    "✅ **Configuration Updated**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🔧 **Variable:** `{key}`\n"
    "🆕 **New Value:** `{value}`\n\n"
    "⚠️ Restart required to apply changes."
)

TIMEOUT_TEMPLATE = (
    "⌛ **Timed Out**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "No input received.\n"
    "Please try again."
)

# ==================================================
# CONFIG HELPERS
# ==================================================

EXCLUDE_CONFIGS = {"API_ID", "API_HASH", "DATABASE"}

def get_editable_configs():
    return sorted(
        key for key in dir(Telegram)
        if not key.startswith("_")
        and key not in EXCLUDE_CONFIGS
        and not callable(getattr(Telegram, key))
    )


def build_config_markup(page: int = 0, page_size: int = 6):
    configs = get_editable_configs()
    total_pages = (len(configs) + page_size - 1) // page_size

    start = page * page_size
    batch = configs[start:start + page_size]

    buttons = []

    # Config buttons (2 per row)
    row = []
    for key in batch:
        row.append(
            InlineKeyboardButton(f"⚙️ {key}", callback_data=f"conf_edit_{key}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"conf_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"conf_page_{page+1}"))
    if nav:
        buttons.append(nav)

    # Footer
    buttons.append([
        InlineKeyboardButton("🔄 Restart Backend", callback_data="conf_restart"),
        InlineKeyboardButton("❌ Close Panel", callback_data="conf_close")
    ])

    return InlineKeyboardMarkup(buttons)

# ==================================================
# RESTART LOGIC
# ==================================================

async def perform_restart(client: Client, chat_id: int, message_id: int | None = None):
    try:
        text = (
            "<blockquote>"
            "⚙️ Restarting Backend API...\n\n"
            "✨ Please wait as we bring everything back online! 🚀"
            "</blockquote>"
        )

        if message_id:
            restart_message = await client.edit_message_text(
                chat_id,
                message_id,
                text,
                parse_mode=enums.ParseMode.HTML
            )
        else:
            restart_message = await client.send_message(
                chat_id,
                text,
                parse_mode=enums.ParseMode.HTML
            )

        proc = await create_subprocess_exec("uv", "run", "update.py")
        await gather(proc.wait())

        async with aiopen(".restartmsg", "w") as f:
            await f.write(f"{restart_message.chat.id}\n{restart_message.id}\n")

        uv_path = shutil.which("uv")
        if not uv_path:
            raise RuntimeError("uv not found in PATH")

        LOGGER.info("Restarting backend using uv…")
        osexecl(uv_path, uv_path, "run", "-m", "Backend")

    except Exception as e:
        LOGGER.error(f"Restart failed: {e}")
        await client.send_message(chat_id, "❌ **Restart failed. Check logs.**")

# ==================================================
# /restart COMMAND
# ==================================================

@Client.on_message(filters.command("restart") & filters.private & CustomFilters.owner, group=10)
async def restart_command(client: Client, message: Message):
    await perform_restart(client, message.chat.id)

# ==================================================
# /config COMMAND
# ==================================================

@Client.on_message(filters.command("config") & filters.private & CustomFilters.owner)
async def config_handler(client: Client, message: Message):
    await message.reply_text(
        CONFIG_HEADER,
        reply_markup=build_config_markup(0),
        parse_mode=enums.ParseMode.MARKDOWN
    )

# ==================================================
# CALLBACK HANDLER
# ==================================================

@Client.on_callback_query(filters.regex("^conf_"))
async def config_callback(client: Client, query: CallbackQuery):
    data = query.data

    # Close
    if data == "conf_close":
        await query.message.delete()
        await query.answer()
        return

    # Pagination
    if data.startswith("conf_page_"):
        page = int(data.split("_")[-1])
        await query.message.edit_text(
            CONFIG_HEADER,
            reply_markup=build_config_markup(page),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        await query.answer()
        return

    # Restart
    if data == "conf_restart":
        await query.answer()
        await perform_restart(client, query.message.chat.id, query.message.id)
        return

    # Edit
    if data.startswith("conf_edit_"):
        key = data.replace("conf_edit_", "")
        current = getattr(Telegram, key, "N/A")

        await query.answer()

        try:
            reply: Message = await client.ask(
                chat_id=query.message.chat.id,
                text=EDIT_TEMPLATE.format(key=key, current=current),
                filters=filters.text,
                timeout=60,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except TimeoutError:
            await query.message.edit_text(
                TIMEOUT_TEMPLATE,
                reply_markup=build_config_markup(0),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        raw = reply.text.strip()

        # Cleanup
        try:
            await reply.delete()
            if reply.reply_to_message:
                await reply.reply_to_message.delete()
        except:
            pass

        # Type inference
        if raw.lower() == "true":
            value = True
        elif raw.lower() == "false":
            value = False
        elif raw.isdigit():
            value = int(raw)
        else:
            value = raw

        try:
            await db.set_config(key, value)
            setattr(Telegram, key, value)
        except Exception as e:
            LOGGER.error(f"Config update failed: {e}")
            await query.message.edit_text(
                "❌ **Failed to update configuration.**",
                reply_markup=build_config_markup(0)
            )
            return

        await query.message.edit_text(
            SUCCESS_TEMPLATE.format(key=key, value=value),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Restart Now", callback_data="conf_restart")],
                [InlineKeyboardButton("🔙 Back to Settings", callback_data="conf_page_0")]
            ]),
            parse_mode=enums.ParseMode.MARKDOWN
        )
