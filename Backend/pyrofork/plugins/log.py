import asyncio
import aiofiles
import aiohttp
import random
import string
from os import path as ospath
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.errors import MessageNotModified
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER

# -------------------------------
# CONSTANTS
# -------------------------------
LOG_FILE_PATH = ospath.abspath("log.txt")
MAX_PASTE_PAGES = 100
LOG_CACHE = {}

# -------------------------------
# HELPERS
# -------------------------------
async def generate_random_string(length=32):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def chunk_text(text: str, chunk_size=3500):
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


async def get_log_content():
    if not ospath.exists(LOG_FILE_PATH):
        LOGGER.warning("Log file not found")
        return None
    async with aiofiles.open(LOG_FILE_PATH, "r") as f:
        return await f.read()


async def paste_to_spacebin(content: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://spaceb.in/api/v1/documents",
                data={"content": content, "extension": "txt"},
            ) as r:
                if r.status == 201:
                    data = await r.json()
                    doc_id = data.get("payload", {}).get("id")
                    LOGGER.info(f"Spacebin paste success: {doc_id}")
                    return f"https://spaceb.in/{doc_id}"
                error = (await r.json()).get('error', 'Unknown error')
                LOGGER.warning(f"Spacebin paste failed: {error}")
                return f"Error: {error}"
    except Exception as e:
        LOGGER.exception(f"Exception in paste_to_spacebin: {e}")
        return f"Error: {e}"


async def paste_to_yaso(content: str):
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.post("https://api.yaso.su/v1/auth/guest") as auth:
                auth.raise_for_status()

            async with session.post(
                "https://api.yaso.su/v1/records",
                json={
                    "captcha": await generate_random_string(64),
                    "codeLanguage": "auto",
                    "content": content,
                    "extension": "txt",
                    "expirationTime": 1000000,
                },
            ) as paste:
                paste.raise_for_status()
                result = await paste.json()
                url = result.get("url")
                LOGGER.info(f"Yaso paste successful: {url}")
                return f"https://yaso.su/raw/{url}"
    except Exception as e:
        LOGGER.exception(f"Exception in paste_to_yaso: {e}")
        return f"Error: {e}"


async def get_paste_url(content: str):
    """Tries Yaso first, then falls back to Spacebin."""
    yaso_url = await paste_to_yaso(content)
    if not yaso_url.startswith("Error"):
        return yaso_url
    LOGGER.warning("Yaso failed, falling back to Spacebin")
    return await paste_to_spacebin(content)


async def safe_answer(query: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception:
        pass


def build_main_markup(index: int, total: int, url: str):
    nav_row = []
    if index > 1:
        nav_row.append(InlineKeyboardButton("⏮", callback_data="log_prev2"))
    if index > 0:
        nav_row.append(InlineKeyboardButton("⬅", callback_data="log_prev"))
    nav_row.append(InlineKeyboardButton(f"📄 {index + 1}/{total}", callback_data="log_null"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("➡", callback_data="log_next"))
    if index < total - 2:
        nav_row.append(InlineKeyboardButton("⏭", callback_data="log_next2"))

    actions_row = [
        InlineKeyboardButton("🔄 Refresh", callback_data="log_refresh"),
        InlineKeyboardButton("🌐 Open URL", url=url),
        InlineKeyboardButton("📤 Send Log File", callback_data="log_sendfile")
    ]
    close_row = [InlineKeyboardButton("❌ Close", callback_data="log_close")]

    return InlineKeyboardMarkup([nav_row, actions_row, close_row])


def build_selector_markup(msg_id: int):
    data = LOG_CACHE.get(msg_id)
    if not data:
        return None
    total = len(data["pages"])
    url = data["url"]
    start = data.get("selector_start", 0)
    window = 25 if total <= 100 else 50
    end = min(start + window, total)

    buttons = [[InlineKeyboardButton("📌 Select Page Number", callback_data="selector_null")]]
    for i in range(start, end, 5):
        row = [InlineKeyboardButton(str(j + 1), callback_data=f"log_page_{j}") for j in range(i, min(i + 5, end))]
        buttons.append(row)

    nav = []
    if start > 0:
        nav.extend([
            InlineKeyboardButton("⏮", callback_data="selector_first"),
            InlineKeyboardButton("⬅", callback_data="selector_prev")
        ])
    nav.append(InlineKeyboardButton("🔙 Back", callback_data="selector_back"))
    if end < total:
        nav.extend([
            InlineKeyboardButton("➡", callback_data="selector_next"),
            InlineKeyboardButton("⏭", callback_data="selector_last")
        ])
    buttons.append(nav)

    buttons.append([
        InlineKeyboardButton("❌ Close", callback_data="log_close"),
        InlineKeyboardButton("🌐 Open URL", url=url),
        InlineKeyboardButton("📤 Send Log File", callback_data="log_sendfile")
    ])
    return InlineKeyboardMarkup(buttons)


async def update_message_page(query, msg_id, data):
    """Helper to update message text with a new page."""
    index = data["index"]
    page = data["pages"][index]
    markup = build_main_markup(index, len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    LOGGER.debug(f"Updated to page {index+1} for message_id {msg_id}")


# -------------------------------
# LOG COMMAND
# -------------------------------
@Client.on_message(filters.command(["log", "logs"]) & filters.private & CustomFilters.owner)
async def log_command(client, message: Message):
    try:
        content = await get_log_content()
        if not content:
            return await message.reply_text("❌ Log file not found.")

        pages = chunk_text(content)
        paste_content = "".join(pages[-MAX_PASTE_PAGES:]) if len(pages) > MAX_PASTE_PAGES else content
        paste_url = await get_paste_url(paste_content)
        cache = {"pages": pages, "url": paste_url, "index": len(pages) - 1, "selector_start": 0}

        # Short logs → show directly
        if len(content) < 3500:
            sent = await message.reply_text(
                f"<pre>{content}</pre>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open URL", url=paste_url)]]),
            )
            LOG_CACHE[sent.id] = cache
            return

        # Long logs → show preview
        preview = "<pre>" + "\n".join(content.strip().splitlines()[-20:]) + "</pre>"
        sent = await message.reply_text(preview, reply_markup=build_main_markup(len(pages)-1, len(pages), paste_url))
        LOG_CACHE[sent.id] = cache
    except Exception as e:
        LOGGER.exception(f"Error in /log: {e}")
        await message.reply_text(f"⚠️ Error: {e}")


# -------------------------------
# CALLBACK HANDLERS (DRY)
# -------------------------------
@Client.on_callback_query(filters.regex(r"^log_page_(\d+)$"))
async def page_button(client, query):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)
    data["index"] = int(query.data.split("_")[-1])
    await update_message_page(query, msg_id, data)
    await safe_answer(query, f"Page {data['index'] + 1}")


@Client.on_callback_query(filters.regex(r"^log_(prev|next|prev2|next2)$"))
async def navigation_handler(client, query):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)
    total = len(data["pages"])

    move_map = {
        "log_prev": -1,
        "log_next": +1,
        "log_prev2": -2,
        "log_next2": +2
    }
    move = move_map.get(query.data, 0)
    new_index = max(0, min(total - 1, data["index"] + move))
    if new_index == data["index"]:
        return await safe_answer(query, "Can't move further")

    data["index"] = new_index
    await update_message_page(query, msg_id, data)
    await safe_answer(query)


# -------------------------------
# REFRESH HANDLER (DRY)
# -------------------------------
@Client.on_callback_query(filters.regex("^log_refresh$"))
async def log_refresh_handler(client, query):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)

    try:
        content = await get_log_content()
        if not content:
            return await safe_answer(query, "❌ Log file missing.", show_alert=True)

        pages = chunk_text(content)
        paste_content = "".join(pages[-MAX_PASTE_PAGES:]) if len(pages) > MAX_PASTE_PAGES else content
        paste_url = await get_paste_url(paste_content)
        data.update({"pages": pages, "url": paste_url})

        await update_message_page(query, msg_id, data)
        await safe_answer(query, "✅ Log refreshed")
    except Exception as e:
        LOGGER.exception(f"Error in log_refresh: {e}")
        await safe_answer(query, "⚠️ Error refreshing log", show_alert=True)


# -------------------------------
# SEND LOG FILE
# -------------------------------
@Client.on_callback_query(filters.regex("^log_sendfile$"))
async def send_log_file(client, query):
    if not ospath.exists(LOG_FILE_PATH):
        return await safe_answer(query, "❌ Log file not found.", show_alert=True)
    await query.message.reply_document(LOG_FILE_PATH, caption="📄 Full log file")
    await safe_answer(query, "📤 Log file sent!")


# -------------------------------
# SELECTOR NAVIGATION
# -------------------------------
@Client.on_callback_query(filters.regex("^log_null$"))
async def open_selector(client, query):
    markup = build_selector_markup(query.message.id)
    if markup:
        await query.message.edit_reply_markup(markup)
    await safe_answer(query)


@Client.on_callback_query(filters.regex("^selector_(prev|next|first|last|back|null)$"))
async def selector_handler(client, query):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)

    total = len(data["pages"])
    window = 25 if total <= 100 else 50
    cmd = query.data.split("_")[-1]

    if cmd == "prev":
        data["selector_start"] = max(0, data["selector_start"] - window)
    elif cmd == "next":
        data["selector_start"] = min(total - window, data["selector_start"] + window)
    elif cmd == "first":
        data["selector_start"] = 0
    elif cmd == "last":
        data["selector_start"] = max(0, total - window)
    elif cmd == "back":
        await query.message.edit_reply_markup(build_main_markup(data["index"], total, data["url"]))
        return await safe_answer(query)
    elif cmd == "null":
        return await safe_answer(query, "📌 Select page number from below ⬇️")

    await query.message.edit_reply_markup(build_selector_markup(msg_id))
    await safe_answer(query)


# -------------------------------
# CLOSE HANDLER
# -------------------------------
@Client.on_callback_query(filters.regex("^log_close$"))
async def log_close_handler(client, query):
    msg_id = query.message.id
    LOG_CACHE.pop(msg_id, None)
    await query.message.delete()
    await safe_answer(query, "Closed.")
