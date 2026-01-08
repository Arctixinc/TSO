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

    buttons = [
        [InlineKeyboardButton(f"⚙️ {key}", callback_data=f"conf_edit_{key}")]
        for key in batch
    ]

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"conf_page_{page-1}")
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"conf_page_{page+1}")
        )

    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("❌ Close", callback_data="conf_close")])
    buttons.append([InlineKeyboardButton("🔄 Restart", callback_data="conf_restart")])

    return InlineKeyboardMarkup(buttons)

# ==================================================
# SHARED RESTART LOGIC (SINGLE SOURCE)
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

        # Run updater
        proc = await create_subprocess_exec("uv", "run", "update.py")
        await gather(proc.wait())

        # Save restart message reference
        async with aiopen(".restartmsg", "w") as f:
            await f.write(f"{restart_message.chat.id}\n{restart_message.id}\n")

        LOGGER.info("Restarting the bot using uv package manager...")

        uv_path = shutil.which("uv")
        if not uv_path:
            raise RuntimeError("uv not found in PATH")

        osexecl(uv_path, uv_path, "run", "-m", "Backend")

    except Exception as e:
        LOGGER.error(f"Error during restart: {e}")
        await client.send_message(
            chat_id,
            "❌ **Failed to restart. Check logs for details.**"
        )

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
        "🛠 **Configuration Manager**\n\nSelect a variable to edit:",
        reply_markup=build_config_markup(0),
        parse_mode=enums.ParseMode.MARKDOWN
    )

# ==================================================
# CONFIG CALLBACK HANDLER
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
            "🛠 **Configuration Manager**\n\nSelect a variable to edit:",
            reply_markup=build_config_markup(page),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        await query.answer()
        return

    # Restart from button
    if data == "conf_restart":
        await query.answer()
        await perform_restart(
            client,
            chat_id=query.message.chat.id,
            message_id=query.message.id
        )
        return

    # Edit config
    if data.startswith("conf_edit_"):
        key = data.replace("conf_edit_", "")
        current = getattr(Telegram, key, "N/A")

        await query.answer()

        try:
            response: Message = await client.ask(
                chat_id=query.message.chat.id,
                text=(
                    f"✏️ **Edit Configuration**\n\n"
                    f"**Variable:** `{key}`\n"
                    f"**Current Value:** `{current}`\n\n"
                    f"Send the new value (timeout: 60s)"
                ),
                filters=filters.text,
                timeout=60,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except TimeoutError:
            await query.message.edit_text(
                "❌ **Timeout**\n\nNo input received.",
                reply_markup=build_config_markup(0),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        raw = response.text.strip()

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
                "❌ Failed to update configuration.",
                reply_markup=build_config_markup(0)
            )
            return

        await query.message.edit_text(
            f"✅ **Configuration Updated**\n\n"
            f"`{key}` → `{value}`\n\n"
            f"⚠️ Restart required to apply changes.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Restart", callback_data="conf_restart")],
                [InlineKeyboardButton("🔙 Back", callback_data="conf_page_0")]
            ]),
            parse_mode=enums.ParseMode.MARKDOWN
        )
