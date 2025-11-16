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
# CONFIGURABLE CONSTANTS
# -------------------------------
CHUNK_SIZE = 3500
MAX_PASTE_PAGES = 100
LOG_FILE_PATH = ospath.abspath("log.txt")

# -------------------------------
# STATIC CONSTANTS
# -------------------------------
LOG_CONTEXT_LOST_MSG = "⚠️ Log data not available — please reopen logs."
MAX_CHARS = 50000

def trim_content(content: str) -> str:
    if len(content) > MAX_CHARS:
        return content[-MAX_CHARS:]
    return content
    
# -------------------------------
# HELPERS
# -------------------------------
async def generate_random_string(length=32):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def paste_to_spacebin(content: str):
    content = trim_content(content)
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
                else:
                    error_msg = (await r.json()).get('error', 'Unknown error')
                    LOGGER.warning(f"Spacebin paste failed: {error_msg}")
                    return f"Error: {error_msg}"
    except Exception as e:
        LOGGER.exception(f"Exception in paste_to_spacebin: {e}")
        return f"Error: {e}"

async def paste_to_yaso(content: str):
    content = trim_content(content)
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.post("https://api.yaso.su/v1/auth/guest") as auth:
                auth.raise_for_status()
                LOGGER.info("Yaso guest auth successful")

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

def get_total_pages(file_path: str, chunk_size=3500) -> int:
    file_size = ospath.getsize(file_path)
    return (file_size + chunk_size - 1) // chunk_size

async def get_page(file_path: str, page_index: int, chunk_size=3500) -> str:
    async with aiofiles.open(file_path, "r") as f:
        await f.seek(page_index * chunk_size)
        return await f.read(chunk_size)

# -------------------------------
# PAGINATION STATE
# -------------------------------
# In-memory cache for log sessions
LOG_CACHE = {}  # message_id -> {"file_path": str, "total_pages": int, "url": str, "index": int, "selector_start": int, "view_mode": str}

# -------------------------------
# SAFE ANSWER FUNCTION
# -------------------------------
async def safe_answer(query: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception as e:
        LOGGER.debug(f"safe_answer failed: {e}")

# -------------------------------
# MARKUPS
# -------------------------------
def build_main_markup(index: int, total: int, url: str, view_mode: str):
    """Builds the main UI markup with a modern look."""
    buttons = []

    # Page number and selector
    page_row = [
        InlineKeyboardButton(f"📄 Page {index + 1}/{total}", callback_data="log_selector"),
    ]
    buttons.append(page_row)

    # Main navigation
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("⏪ First", callback_data="log_first"))
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="log_prev"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="log_next"))
        nav_row.append(InlineKeyboardButton("Last ⏩", callback_data="log_last"))
    buttons.append(nav_row)

    # Dynamic page jump buttons
    jump_row = []
    if index > 1:
        jump_row.append(InlineKeyboardButton("-2", callback_data="log_prev2"))
    if index < total - 2:
        jump_row.append(InlineKeyboardButton("+2", callback_data="log_next2"))
    if jump_row:
        buttons.append(jump_row)

    # Actions row
    actions_row = [
        InlineKeyboardButton("🔄 Refresh", callback_data="log_refresh"),
        InlineKeyboardButton(f"View: {'Tail' if view_mode == 'tail' else 'Head'}", callback_data="log_toggle_view_mode"),
        InlineKeyboardButton("📎 Send File", callback_data="log_sendfile"),
    ]
    buttons.append(actions_row)

    # Footer row
    footer_row = [
        InlineKeyboardButton("🌐 URL", url=url),
        InlineKeyboardButton("❌ Close", callback_data="log_close"),
    ]
    buttons.append(footer_row)

    return InlineKeyboardMarkup(buttons)

# -------------------------------
# SELECTOR MARKUP
# -------------------------------
def build_selector_markup(msg_id: int, page_range_start: int = -1):
    data = LOG_CACHE.get(msg_id)
    if not data:
        return None

    total_pages = data["total_pages"]
    buttons = []

    # If total pages are manageable, show the simple selector
    if total_pages <= 50:
        window_size = 25
        start = data.get("selector_start", 0)
        end = min(start + window_size, total_pages)

        # Page buttons
        row = []
        for i in range(start, end):
            row.append(InlineKeyboardButton(str(i + 1), callback_data=f"log_page_{i}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # Navigation
        nav_row = []
        if start > 0:
            nav_row.append(InlineKeyboardButton("⏪", callback_data="selector_prev"))
        nav_row.append(InlineKeyboardButton("Back", callback_data="selector_back"))
        if end < total_pages:
            nav_row.append(InlineKeyboardButton("⏩", callback_data="selector_next"))
        buttons.append(nav_row)

    # Otherwise, show the ranged selector
    else:
        # If a range is selected, show the pages in that range
        if page_range_start != -1:
            start = page_range_start
            end = min(start + 50, total_pages)

            row = []
            for i in range(start, end):
                row.append(InlineKeyboardButton(str(i + 1), callback_data=f"log_page_{i}"))
                if len(row) == 5:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([InlineKeyboardButton("Back to Ranges", callback_data="log_selector")])

        # Otherwise, show the ranges
        else:
            buttons.append([InlineKeyboardButton("Select Page Range", callback_data="selector_null")])
            ranges = list(range(0, total_pages, 50))
            row = []
            for i in ranges:
                start_page = i + 1
                end_page = min(i + 50, total_pages)
                row.append(InlineKeyboardButton(f"{start_page}-{end_page}", callback_data=f"log_range_{i}"))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([InlineKeyboardButton("Back", callback_data="selector_back")])

    return InlineKeyboardMarkup(buttons)

# -------------------------------
# LOG COMMAND
# -------------------------------
@Client.on_message(filters.command(["log", "logs"]) & filters.private & CustomFilters.owner, group=10)
async def log_command(client: Client, message: Message):
    try:
        file_path = ospath.abspath("log.txt")
        if not ospath.exists(file_path) or ospath.getsize(file_path) == 0:
            return await message.reply_text("> Log file not found or is empty.")

        total_pages = get_total_pages(file_path)

        # Smartly decide what to paste
        async with aiofiles.open(file_path, 'r') as f:
            await f.seek(0, 2)
            size = await f.tell()
            await f.seek(max(0, size - MAX_PASTE_PAGES * CHUNK_SIZE), 0)
            paste_content = await f.read()

        yaso_url = await paste_to_yaso(paste_content)
        paste_url = yaso_url if not yaso_url.startswith("Error") else await paste_to_spacebin(paste_content)

        view_mode = 'tail'
        index = total_pages - 1

        temp_cache = {
            "file_path": file_path,
            "total_pages": total_pages,
            "url": paste_url,
            "index": index,
            "selector_start": 0,
            "view_mode": view_mode
        }

        # For small files, just send the content
        if total_pages == 1:
            sent_msg = await message.reply_text(
                f"<pre>{await get_page(file_path, 0)}</pre>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("URL", url=paste_url)]])
            )
            LOG_CACHE[sent_msg.id] = temp_cache
            return

        initial_page_content = await get_page(file_path, index)
        markup = build_main_markup(index, total_pages, paste_url, view_mode)
        sent_msg = await message.reply_text(f"<pre>{initial_page_content}</pre>", reply_markup=markup, quote=True)
        LOG_CACHE[sent_msg.id] = temp_cache

    except Exception as e:
        LOGGER.exception(f"Error in /log command: {e}")
        await message.reply_text(f"Error: {e}")

# -------------------------------
# CALLBACK HANDLERS
# -------------------------------
@Client.on_callback_query(filters.regex("^log_selector$"))
async def open_selector(client, query: CallbackQuery):
    try:
        markup = build_selector_markup(query.message.id)
        if markup:
            await query.message.edit_reply_markup(markup)
        await safe_answer(query, "Select a page or range")
    except Exception as e:
        LOGGER.exception(f"Error in open_selector: {e}")

@Client.on_callback_query(filters.regex(r"^log_range_(\d+)$"))
async def range_button(client, query: CallbackQuery):
    try:
        page_range_start = int(query.data.split("_")[-1])
        markup = build_selector_markup(query.message.id, page_range_start=page_range_start)
        if markup:
            await query.message.edit_reply_markup(markup)
        await safe_answer(query, f"Showing pages {page_range_start+1}-{page_range_start+50}")
    except Exception as e:
        LOGGER.exception(f"Error in range_button: {e}")

@Client.on_callback_query(filters.regex(r"^log_page_(\d+)$"))
async def page_button(client, query: CallbackQuery):
    try:
        msg_id = query.message.id
        page_index = int(query.data.split("_")[-1])
        data = LOG_CACHE.get(msg_id)
        if not data:
            return await safe_answer(query, LOG_CONTEXT_LOST_MSG, show_alert=True)

        data["index"] = page_index
        markup = build_main_markup(data["index"], data["total_pages"], data["url"], data["view_mode"])
        page_content = await get_page(data["file_path"], data["index"])

        await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=markup)
        await safe_answer(query, f"Page {page_index + 1}")
    except Exception as e:
        LOGGER.exception(f"Error in page_button: {e}")

@Client.on_callback_query(filters.regex("^log_toggle_view_mode$"))
async def toggle_view_mode(client, query: CallbackQuery):
    try:
        msg_id = query.message.id
        data = LOG_CACHE.get(msg_id)
        if not data:
            return await safe_answer(query, LOG_CONTEXT_LOST_MSG, show_alert=True)

        if data["view_mode"] == "tail":
            data["view_mode"] = "head"
            data["index"] = 0
        else:
            data["view_mode"] = "tail"
            data["index"] = data["total_pages"] - 1

        markup = build_main_markup(data["index"], data["total_pages"], data["url"], data["view_mode"])
        page_content = await get_page(data["file_path"], data["index"])

        await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=markup)
        await safe_answer(query, f"Switched to {'Head' if data['view_mode'] == 'head' else 'Tail'} mode")
    except Exception as e:
        LOGGER.exception(f"Error in toggle_view_mode: {e}")
        
# -------------------------------
# SELECTOR NAVIGATION HANDLERS
# -------------------------------
@Client.on_callback_query(filters.regex("^selector_(prev|next)$"))
async def selector_navigation(client, query: CallbackQuery):
    try:
        msg_id = query.message.id
        data = LOG_CACHE.get(msg_id)
        if not data:
            return await safe_answer(query, LOG_CONTEXT_LOST_MSG, show_alert=True)

        action = query.data.split("_")[-1]
        window_size = 25

        if action == "prev":
            data["selector_start"] = max(0, data["selector_start"] - window_size)
        elif action == "next":
            total_pages = data["total_pages"]
            data["selector_start"] = min(data["selector_start"] + window_size, total_pages - window_size)

        await query.message.edit_reply_markup(build_selector_markup(msg_id))
        await safe_answer(query)
    except Exception as e:
        LOGGER.exception(f"Error in selector_navigation: {e}")

@Client.on_callback_query(filters.regex("^selector_back$"))
async def selector_back(client, query: CallbackQuery):
    try:
        msg_id = query.message.id
        data = LOG_CACHE.get(msg_id)
        if not data:
            return await safe_answer(query, LOG_CONTEXT_LOST_MSG, show_alert=True)
        markup = build_main_markup(data["index"], data["total_pages"], data["url"], data["view_mode"])
        await query.message.edit_reply_markup(markup)
        await safe_answer(query)
    except Exception as e:
        LOGGER.exception(f"Error in selector_back: {e}")

@Client.on_callback_query(filters.regex("^selector_null$"))
async def selector_null(client, query: CallbackQuery):
    await safe_answer(query, "Select a page number")

# -------------------------------
# NAVIGATION HANDLERS
# -------------------------------
@Client.on_callback_query(filters.regex(r"^log_(prev|next|first|last|prev2|next2)$"))
async def navigation_handler(client, query: CallbackQuery):
    try:
        msg_id = query.message.id
        data = LOG_CACHE.get(msg_id)
        if not data:
            return await safe_answer(query, LOG_CONTEXT_LOST_MSG, show_alert=True)

        action = query.data.split("_")[-1]
        total_pages = data["total_pages"]

        if action == "first":
            if data["index"] == 0:
                return await safe_answer(query, "You are already on the first page.")
            data["index"] = 0
        elif action == "last":
            if data["index"] == total_pages - 1:
                return await safe_answer(query, "You are already on the last page.")
            data["index"] = total_pages - 1
        elif action == "prev":
            if data["index"] == 0:
                return await safe_answer(query, "You are already on the first page.")
            data["index"] -= 1
        elif action == "next":
            if data["index"] == total_pages - 1:
                return await safe_answer(query, "You are already on the last page.")
            data["index"] += 1
        elif action == "prev2":
            data["index"] = max(0, data["index"] - 2)
        elif action == "next2":
            data["index"] = min(total_pages - 1, data["index"] + 2)

        page_content = await get_page(data["file_path"], data["index"])
        markup = build_main_markup(data["index"], total_pages, data["url"], data["view_mode"])

        await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=markup)
        await safe_answer(query)
    except Exception as e:
        LOGGER.exception(f"Error in navigation_handler: {e}")

# -------------------------------
# REFRESH HANDLER HELPERS
# -------------------------------
async def show_refreshing_state(query: CallbackQuery, data: dict):
    markup = build_main_markup(data["index"], data["total_pages"], data["url"], data["view_mode"])
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data == "log_refresh":
                btn.text = "Refreshing..."
    await query.message.edit_reply_markup(markup)

async def reload_log_data(data: dict):
    file_path = data["file_path"]
    total_pages = get_total_pages(file_path)

    if total_pages == 0:
        return None

    async with aiofiles.open(file_path, 'r') as f:
        await f.seek(0, 2)
        size = await f.tell()
        await f.seek(max(0, size - MAX_PASTE_PAGES * CHUNK_SIZE), 0)
        paste_content = await f.read()

    yaso_url = await paste_to_yaso(paste_content)
    paste_url = yaso_url if not yaso_url.startswith("Error") else await paste_to_spacebin(paste_content)

    data["total_pages"] = total_pages
    data["url"] = paste_url
    if data["view_mode"] == "tail":
        data["index"] = total_pages - 1
    else:
        data["index"] = min(data["index"], total_pages - 1)

    return total_pages

async def update_message_after_refresh(query: CallbackQuery, data: dict):
    page_content = await get_page(data["file_path"], data["index"])
    final_markup = build_main_markup(data["index"], data["total_pages"], data["url"], data["view_mode"])
    await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=final_markup)

# -------------------------------
# REFRESH HANDLER
# -------------------------------
@Client.on_callback_query(filters.regex("^log_refresh$"))
async def log_refresh_handler(client, query: CallbackQuery):
    try:
        msg_id = query.message.id
        data = LOG_CACHE.get(msg_id)
        if not data:
            return await safe_answer(query, "Log context expired, please resend command.", show_alert=True)

        await show_refreshing_state(query, data)

        total_pages = await reload_log_data(data)
        if total_pages is None:
            return await query.message.edit_text("> Log file is empty after refresh.")

        await update_message_after_refresh(query, data)
        await safe_answer(query, "Log refreshed successfully")

    except Exception as e:
        await safe_answer(query, "Error refreshing log", show_alert=True)
        LOGGER.exception(f"Error in log_refresh_handler: {e}")

# -------------------------------
# SEND LOG FILE HANDLER
# -------------------------------
@Client.on_callback_query(filters.regex("^log_sendfile$"))
async def send_log_file(client, query: CallbackQuery):
    try:
        path = ospath.abspath("log.txt")
        if not ospath.exists(path):
            return await safe_answer(query, "❌ Log file not found.", show_alert=True)

        await query.message.reply_document(path, caption="📄 Full log file")
        await safe_answer(query, "Sent log file!")
        LOGGER.info(f"Sent log file for message_id {query.message.id}")
    except Exception as e:
        await safe_answer(query, "⚠️ Failed to send log file.", show_alert=True)
        LOGGER.exception(f"Error in send_log_file: {e}")


# -------------------------------
# CLOSE HANDLER
# -------------------------------
@Client.on_callback_query(filters.regex("^log_close$"))
async def log_close_handler(client, query: CallbackQuery):
    try:
        msg_id = query.message.id
        LOG_CACHE.pop(msg_id, None)
        await query.message.delete()
        await safe_answer(query, "Closed.")
        LOGGER.debug(f"Closed log message_id {msg_id}")
    except Exception as e:
        LOGGER.exception(f"Error in log_close_handler: {e}")
