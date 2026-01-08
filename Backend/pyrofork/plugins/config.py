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
# HELPERS
# ==================================================

EXCLUDE_CONFIGS = {"API_ID", "API_HASH", "DATABASE"}

def get_editable_configs():
    return sorted(
        k for k in dir(Telegram)
        if not k.startswith("_")
        and k not in EXCLUDE_CONFIGS
        and not callable(getattr(Telegram, k))
    )

def build_main_menu():
    buttons = [
        [InlineKeyboardButton(f"⚙️ {k}", callback_data=f"cfg_var_{k}")]
        for k in get_editable_configs()
    ]
    buttons.append([
        InlineKeyboardButton("🔄 Restart", callback_data="cfg_restart"),
        InlineKeyboardButton("❌ Close", callback_data="cfg_close")
    ])
    return InlineKeyboardMarkup(buttons)

def build_variable_menu(key: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Value", callback_data=f"cfg_edit_{key}")],
        [
            InlineKeyboardButton("🔄 Restart", callback_data="cfg_restart"),
            InlineKeyboardButton("🔙 Back", callback_data="cfg_back")
        ]
    ])

# ==================================================
# RESTART LOGIC
# ==================================================

async def perform_restart_inline(client: Client, chat_id: int, message_id: int):
    try:
        await client.edit_message_text(
            chat_id,
            message_id,
            "<blockquote>⚙️ Restarting Backend API...\n\n"
            "✨ Please wait as we bring everything back online! 🚀</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

        proc = await create_subprocess_exec("uv", "run", "update.py")
        await gather(proc.wait())

        async with aiopen(".restartmsg", "w") as f:
            await f.write(f"{chat_id}\n{message_id}\n")

        uv_path = shutil.which("uv")
        if not uv_path:
            raise RuntimeError("uv not found in PATH")

        LOGGER.info("Restarting backend using uv…")
        osexecl(uv_path, uv_path, "run", "-m", "Backend")

    except Exception as e:
        LOGGER.error(f"Restart failed: {e}")
        await client.edit_message_text(
            chat_id,
            message_id,
            "❌ **Restart failed. Check logs.**",
            parse_mode=enums.ParseMode.MARKDOWN
        )

# ==================================================
# /config COMMAND
# ==================================================

@Client.on_message(
    filters.command("config") & filters.private & CustomFilters.owner,
    group=-10
)
async def config_entry(client: Client, message: Message):
    await message.reply_text(
        "🧠 **Configuration Manager**\n\nSelect a variable:",
        reply_markup=build_main_menu(),
        parse_mode=enums.ParseMode.MARKDOWN
    )

# ==================================================
# CALLBACK HANDLER
# ==================================================

@Client.on_callback_query(filters.regex("^cfg_"))
async def config_callback(client: Client, query: CallbackQuery):
    await query.answer()
    data = query.data
    msg = query.message

    # ---------- CLOSE ----------
    if data == "cfg_close":
        await msg.delete()
        return

    # ---------- RESTART ----------
    if data == "cfg_restart":
        await perform_restart_inline(client, msg.chat.id, msg.id)
        return

    # ---------- BACK ----------
    if data == "cfg_back":
        await msg.edit_text(
            "🧠 **Configuration Manager**\n\nSelect a variable:",
            reply_markup=build_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------- VARIABLE SELECT ----------
    if data.startswith("cfg_var_"):
        key = data.replace("cfg_var_", "")
        value = getattr(Telegram, key, "N/A")

        await msg.edit_text(
            f"⚙️ **{key}**\n\n"
            f"📌 Current Value:\n`{value}`",
            reply_markup=build_variable_menu(key),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ---------- EDIT VALUE ----------
    if data.startswith("cfg_edit_"):
        key = data.replace("cfg_edit_", "")
        current = getattr(Telegram, key, "N/A")

        try:
            reply: Message = await client.ask(
                chat_id=msg.chat.id,
                text=(
                    f"✏️ **Edit `{key}`**\n\n"
                    f"📌 Current: `{current}`\n\n"
                    "Send the **new value**.\n"
                    "⏱ Timeout: 60 seconds"
                ),
                filters=filters.text,
                timeout=60,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except TimeoutError:
            await msg.edit_text(
                "⌛ **Timed out**\n\nReturning to menu.",
                reply_markup=build_main_menu(),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return

        # cleanup ask messages
        try:
            await reply.delete()
            if reply.reply_to_message:
                await reply.reply_to_message.delete()
        except:
            pass

        raw = reply.text.strip()

        if raw.lower() == "true":
            value = True
        elif raw.lower() == "false":
            value = False
        elif raw.isdigit():
            value = int(raw)
        else:
            value = raw

        await db.set_config(key, value)
        setattr(Telegram, key, value)

        await msg.edit_text(
            f"✅ **Configuration Updated**\n\n"
            f"🔧 `{key}`\n"
            f"🆕 `{value}`\n\n"
            "⚠️ Restart required to apply changes.",
            reply_markup=build_variable_menu(key),
            parse_mode=enums.ParseMode.MARKDOWN
        )
