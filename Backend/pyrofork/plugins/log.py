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
from Backend.helper.custom_filter import CustomFilters

# -------------------------------
# HELPERS
# -------------------------------

PAGE_LINES = 50           # 50 lines per page
MAX_UPLOAD_PAGES = 111    # Only last 111 pages uploaded if log is huge

async def generate_random_string(length=32):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

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
                    return f"https://spaceb.in/{doc_id}"
                else:
                    return f"Error: {(await r.json()).get('error', 'Unknown error')}"
    except Exception as e:
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
                return f"https://yaso.su/raw/{result.get('url')}"
    except Exception as e:
        return f"Error: {e}"

# -------------------------------
# LOG PAGINATION
# -------------------------------

LOG_CACHE = {}  # message_id -> {"file_path": str, "index": int, "selector_start": int, "pages_count": int, "url": str}

async def read_log_page(path: str, page_index: int):
    start_line = page_index * PAGE_LINES
    end_line = start_line + PAGE_LINES
    lines = []

    async with aiofiles.open(path, "r") as f:
        current_line = 0
        async for line in f:
            if start_line <= current_line < end_line:
                lines.append(line.rstrip())
            current_line += 1
            if current_line >= end_line:
                break
    return "\n".join(lines)

async def prepare_upload_content(path: str, total_pages: int):
    async with aiofiles.open(path, "r") as f:
        all_lines = await f.readlines()

    if total_pages > MAX_UPLOAD_PAGES:
        start_line = (total_pages - MAX_UPLOAD_PAGES) * PAGE_LINES
    else:
        start_line = 0

    content = "".join(all_lines[start_line:])
    yaso_url = await paste_to_yaso(content)
    return yaso_url if not yaso_url.startswith("Error") else await paste_to_spacebin(content)

async def safe_answer(query: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception:
        pass

# -------------------------------
# MARKUPS
# -------------------------------

def build_main_markup(index: int, total: int, url: str):
    buttons = []

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
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="log_refresh"),
        InlineKeyboardButton("🌐 Open URL", url=url)
    ])
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="log_close")])
    return InlineKeyboardMarkup(buttons)

def build_selector_markup(msg_id: int, window_size=25):
    data = LOG_CACHE.get(msg_id)
    if not data:
        return None

    total_pages = data["pages_count"]
    url = data["url"]
    start = data.get("selector_start", 0)
    end = min(start + window_size, total_pages)

    buttons = []
    buttons.append([InlineKeyboardButton("📌 Select page number from below", callback_data="selector_null")])

    row = []
    for i in range(start, end):
        row.append(InlineKeyboardButton(str(i + 1), callback_data=f"log_page_{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    selector_nav = []
    if start > 0:
        selector_nav.append(InlineKeyboardButton("⬅ Prev", callback_data="selector_prev"))
        selector_nav.append(InlineKeyboardButton("⏮ First", callback_data="selector_first"))
    selector_nav.append(InlineKeyboardButton("🔙 Back", callback_data="selector_back"))
    if end < total_pages:
        selector_nav.append(InlineKeyboardButton("Next ➡", callback_data="selector_next"))
        selector_nav.append(InlineKeyboardButton("⏭ Last", callback_data="selector_last"))
    buttons.append(selector_nav)

    buttons.append([InlineKeyboardButton("❌ Close", callback_data="log_close"),
                    InlineKeyboardButton("🌐 Open URL", url=url)])
    return InlineKeyboardMarkup(buttons)

# -------------------------------
# LOG COMMAND
# -------------------------------

@Client.on_message(filters.command(["log", "logs"]) & filters.private & CustomFilters.owner)
async def log_command(client: Client, message: Message):
    try:
        path = ospath.abspath("log.txt")
        if not ospath.exists(path):
            return await message.reply_text("> ❌ Log file not found.")

        # Count total lines
        async with aiofiles.open(path, "r") as f:
            all_lines = await f.readlines()
        total_lines = len(all_lines)
        total_pages = (total_lines + PAGE_LINES - 1) // PAGE_LINES

        # Upload last 111 pages if needed
        paste_url = await prepare_upload_content(path, total_pages)

        LOG_CACHE[message.id] = {
            "file_path": path,
            "index": total_pages - 1,
            "selector_start": 0,
            "pages_count": total_pages,
            "url": paste_url
        }

        # Preview last 20 lines
        preview_lines = all_lines[-20:] if total_lines > 20 else all_lines
        preview_text = "<pre>" + "".join(preview_lines) + "</pre>"

        markup = build_main_markup(total_pages - 1, total_pages, paste_url)
        await message.reply_text(preview_text, reply_markup=markup)

    except Exception as e:
        await message.reply_text(f"⚠️ Error: {e}")
        print(f"Error in /log command: {e}")

# -------------------------------
# CALLBACK HANDLERS
# -------------------------------

@Client.on_callback_query(filters.regex("^log_null$"))
async def open_selector(client, query: CallbackQuery):
    markup = build_selector_markup(query.message.id)
    if markup:
        await query.message.edit_reply_markup(markup)
    await safe_answer(query)

@Client.on_callback_query(filters.regex(r"^log_page_(\d+)$"))
async def page_button(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)

    page_index = int(query.data.split("_")[-1])
    data["index"] = page_index
    page_content = await read_log_page(data["file_path"], page_index)
    markup = build_main_markup(page_index, data["pages_count"], data["url"])
    await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=markup)
    await safe_answer(query, f"Page {page_index + 1}")

# -------------------------------
# Selector Navigation
# -------------------------------
@Client.on_callback_query(filters.regex("^selector_prev$"))
async def selector_prev(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await safe_answer(query, "Session expired", show_alert=True)
    window_size = 25
    data["selector_start"] = max(0, data.get("selector_start", 0) - window_size)
    await query.message.edit_reply_markup(build_selector_markup(msg_id))
    await safe_answer(query)

@Client.on_callback_query(filters.regex("^selector_next$"))
async def selector_next(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await safe_answer(query, "Session expired", show_alert=True)
    window_size = 25
    total_pages = data["pages_count"]
    data["selector_start"] = min(total_pages - 1, data.get("selector_start", 0) + window_size)
    await query.message.edit_reply_markup(build_selector_markup(msg_id))
    await safe_answer(query)

@Client.on_callback_query(filters.regex("^selector_back$"))
async def selector_back(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await safe_answer(query, "Session expired", show_alert=True)
    markup = build_main_markup(data["index"], data["pages_count"], data["url"])
    await query.message.edit_reply_markup(markup)
    await safe_answer(query)

@Client.on_callback_query(filters.regex("^selector_null$"))
async def selector_null(client, query: CallbackQuery):
    await safe_answer(query, "📌 Select page number from below ⬇️")

# -------------------------------
# Main Navigation
# -------------------------------
@Client.on_callback_query(filters.regex(r"^log_(prev|next|prev2|next2)$"))
async def navigation_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await safe_answer(query, "Session expired", show_alert=True)

    if query.data == "log_prev" and data["index"] > 0:
        data["index"] -= 1
    elif query.data == "log_next" and data["index"] + 1 < data["pages_count"]:
        data["index"] += 1
    elif query.data == "log_prev2":
        data["index"] = max(0, data["index"] - 2)
    elif query.data == "log_next2":
        data["index"] = min(data["pages_count"] - 1, data["index"] + 2)
    else:
        return await safe_answer(query, "Cannot navigate further", show_alert=False)

    page_content = await read_log_page(data["file_path"], data["index"])
    markup = build_main_markup(data["index"], data["pages_count"], data["url"])
    await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=markup)
    await safe_answer(query)

# -------------------------------
# Refresh Handler
# -------------------------------
@Client.on_callback_query(filters.regex("^log_refresh$"))
async def log_refresh_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await safe_answer(query, "Session expired", show_alert=True)

    markup = build_main_markup(data["index"], data["pages_count"], data["url"])
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data == "log_refresh":
                btn.text = "⏳ Refreshing..."
    await query.message.edit_reply_markup(markup)

    try:
        path = data["file_path"]
        async with aiofiles.open(path, "r") as f:
            all_lines = await f.readlines()
        total_lines = len(all_lines)
        total_pages = (total_lines + PAGE_LINES - 1) // PAGE_LINES

        # Prepare new URL with last 111 pages if needed
        paste_url = await prepare_upload_content(path, total_pages)
        LOG_CACHE[msg_id].update({
            "index": min(data["index"], total_pages - 1),
            "pages_count": total_pages,
            "url": paste_url
        })

        page_content = await read_log_page(path, LOG_CACHE[msg_id]["index"])
        await query.message.edit_text(f"<pre>{page_content}</pre>",
                                      reply_markup=build_main_markup(LOG_CACHE[msg_id]["index"], total_pages, paste_url))
        await safe_answer(query, "✅ Log refreshed")
    except Exception as e:
        await safe_answer(query, "⚠️ Error refreshing log", show_alert=True)
        print(f"Error in log_refresh_handler: {e}")

# -------------------------------
# Close Handler
# -------------------------------
@Client.on_callback_query(filters.regex("^log_close$"))
async def log_close_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    LOG_CACHE.pop(msg_id, None)
    await query.message.delete()
    await safe_answer(query, "Closed.")
