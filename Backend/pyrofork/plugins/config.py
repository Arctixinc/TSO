import asyncio
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
# INLINE EDIT STATE
# ==================================================
CONFIG_EDIT_STATE: dict[int, dict] = {}

# ==================================================
# CONFIG HELPERS
# ==================================================
EXCLUDE_CONFIGS = {"API_ID", "API_HASH", "DATABASE"}

def get_editable_configs():
    return sorted(
        k for k in dir(Telegram)
        if not k.startswith("_")
        and k not in EXCLUDE_CONFIGS
        and not callable(getattr(Telegram, k))
    )

# ==================================================
# UI BUILDERS
# ==================================================

def build_main_menu(page: int = 0, page_size: int = 6):
    configs = get_editable_configs()
    total_pages = (len(configs) + page_size - 1) // page_size
    batch = configs[page * page_size:(page + 1) * page_size]

    buttons = [
        [InlineKeyboardButton(f"⚙️ {k}", callback_data=f"cfg_var_{k}")]
        for k in batch
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cfg_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"cfg_page_{page+1}"))
    if nav:
        buttons.append(nav)

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
# RESTART LOGIC (INLINE)
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
# /config ENTRY
# ==================================================

@Client.on_message(filters.command("config") & filters.private & CustomFilters.owner)
async def config_entry(client: Client, message: Message):
    await message.reply_text(
        "🧠 **Configuration Panel**\n\nSelect a variable:",
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
    uid = query.from_user.id

    # Close
    if data == "cfg_close":
        CONFIG_EDIT_STATE.pop(uid, None)
        await msg.delete()
        return

    # Restart
    if data == "cfg_restart":
        CONFIG_EDIT_STATE.pop(uid, None)
        await perform_restart_inline(client, msg.chat.id, msg.id)
        return

    # Pagination
    if data.startswith("cfg_page_"):
        page = int(data.split("_")[-1])
        await msg.edit_text(
            "🧠 **Configuration Panel**\n\nSelect a variable:",
            reply_markup=build_main_menu(page),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # Back
    if data == "cfg_back":
        CONFIG_EDIT_STATE.pop(uid, None)
        await msg.edit_text(
            "🧠 **Configuration Panel**\n\nSelect a variable:",
            reply_markup=build_main_menu(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # Variable select
    if data.startswith("cfg_var_"):
        key = data.replace("cfg_var_", "")
        value = getattr(Telegram, key, "N/A")

        await msg.edit_text(
            f"⚙️ **{key}**\n\nCurrent Value:\n`{value}`",
            reply_markup=build_variable_menu(key),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # Edit
    if data.startswith("cfg_edit_"):
        key = data.replace("cfg_edit_", "")
        CONFIG_EDIT_STATE[uid] = {
            "key": key,
            "chat_id": msg.chat.id,
            "message_id": msg.id
        }

        await msg.edit_text(
            f"✏️ **Editing `{key}`**\n\n"
            "Send the **new value** now.\n"
            "⏱ Timeout: 60 seconds",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="cfg_back")]
            ]),
            parse_mode=enums.ParseMode.MARKDOWN
        )

        asyncio.create_task(edit_timeout(client, uid))

# ==================================================
# TEXT INPUT HANDLER
# ==================================================

@Client.on_message(filters.private & filters.text & CustomFilters.owner)
async def config_text_input(client: Client, message: Message):
    uid = message.from_user.id
    state = CONFIG_EDIT_STATE.get(uid)

    if not state:
        return

    await message.delete()

    key = state["key"]
    raw = message.text.strip()

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

    await client.edit_message_text(
        state["chat_id"],
        state["message_id"],
        f"✅ **Updated Successfully**\n\n"
        f"🔧 `{key}`\n"
        f"🆕 `{value}`",
        reply_markup=build_variable_menu(key),
        parse_mode=enums.ParseMode.MARKDOWN
    )

    CONFIG_EDIT_STATE.pop(uid, None)

# ==================================================
# TIMEOUT HANDLER
# ==================================================

async def edit_timeout(client: Client, user_id: int):
    await asyncio.sleep(60)

    state = CONFIG_EDIT_STATE.get(user_id)
    if not state:
        return

    await client.edit_message_text(
        state["chat_id"],
        state["message_id"],
        "⌛ **Timed Out**\n\nReturning to configuration menu.",
        reply_markup=build_main_menu(),
        parse_mode=enums.ParseMode.MARKDOWN
    )

    CONFIG_EDIT_STATE.pop(user_id, None)
