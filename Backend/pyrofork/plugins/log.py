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

# -------------------------------
# HELPERS
# -------------------------------

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

def chunk_text(text: str, chunk_size=3500):
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

# -------------------------------
# PAGINATION STATE
# -------------------------------
LOG_CACHE = {}  # message_id -> {"pages": [...], "url": str, "index": int, "selector_start": int}

# -------------------------------
# SAFE ANSWER FUNCTION
# -------------------------------
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

    # Navigation row
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

    # Actions row
    buttons.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="log_refresh"),
        InlineKeyboardButton("🌐 Open URL", url=url)
    ])

    # Close row
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="log_close")])

    return InlineKeyboardMarkup(buttons)

def build_selector_markup(msg_id: int, window_size=25):
    data = LOG_CACHE.get(msg_id)
    if not data:
        return None

    pages = data["pages"]
    url = data["url"]
    start = data.get("selector_start", 0)
    end = min(start + window_size, len(pages))

    buttons = []
    
    buttons.append([InlineKeyboardButton("📌 Select page number from below", callback_data="selector_null")])

    # Grid of page numbers (max 5 per row)
    row = []
    for i in range(start, end):
        row.append(InlineKeyboardButton(str(i + 1), callback_data=f"log_page_{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Selector navigation row
    selector_nav = []
    if start > 0:
        selector_nav.append(InlineKeyboardButton("⬅ Prev", callback_data="selector_prev"))
    selector_nav.append(InlineKeyboardButton("🔙 Back", callback_data="selector_back"))
    if end < len(pages):
        selector_nav.append(InlineKeyboardButton("Next ➡", callback_data="selector_next"))
    buttons.append(selector_nav)

    # Close and URL row
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="log_close"),
                    InlineKeyboardButton("🌐 Open URL", url=url)])

    return InlineKeyboardMarkup(buttons)

# -------------------------------
# LOG COMMAND
# -------------------------------
@Client.on_message(filters.command(["log", "logs"]) & filters.private & CustomFilters.owner, group=10)
async def log_command(client: Client, message: Message):
    try:
        path = ospath.abspath("log.txt")
        if not ospath.exists(path):
            return await message.reply_text("> ❌ Log file not found.")

        async with aiofiles.open(path, "r") as f:
            content = await f.read()

        yaso_url = await paste_to_yaso(content)
        paste_url = yaso_url if not yaso_url.startswith("Error") else await paste_to_spacebin(content)

        pages = chunk_text(content)
        temp_cache = {"pages": pages, "url": paste_url, "index": len(pages)-1, "selector_start": 0}

        if len(content) < 3500:
            sent_msg = await message.reply_text(
                f"<pre>{content}</pre>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open URL", url=paste_url)]]),
            )
            LOG_CACHE[sent_msg.id] = temp_cache
            return

        lines = content.strip().splitlines()
        preview_lines = lines[-20:] if len(lines) > 20 else lines
        preview_text = "<pre>" + "\n".join(preview_lines) + "</pre>"

        markup = build_main_markup(len(pages)-1, len(pages), paste_url)
        sent_msg = await message.reply_text(preview_text, reply_markup=markup, quote=True)
        LOG_CACHE[sent_msg.id] = temp_cache
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
    page_index = int(query.data.split("_")[-1])
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)

    data["index"] = page_index
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    page_content = data["pages"][data["index"]]
    await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=markup)
    await safe_answer(query, f"Page {page_index + 1}")

@Client.on_callback_query(filters.regex("^selector_prev$"))
async def selector_prev(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)
    data["selector_start"] = max(0, data.get("selector_start", 0) - 25)
    await query.message.edit_reply_markup(build_selector_markup(msg_id))
    await safe_answer(query)

@Client.on_callback_query(filters.regex("^selector_next$"))
async def selector_next(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)
    data["selector_start"] = min(len(data["pages"]) - 1, data.get("selector_start", 0) + 25)
    await query.message.edit_reply_markup(build_selector_markup(msg_id))
    await safe_answer(query)

@Client.on_callback_query(filters.regex("^selector_back$"))
async def selector_back(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_reply_markup(markup)
    await safe_answer(query)

# -------------------------------
# NAVIGATION HANDLERS
# -------------------------------
@Client.on_callback_query(filters.regex(r"^log_(prev|next|prev2|next2)$"))
async def navigation_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)

    if query.data == "log_prev" and data["index"] > 0:
        data["index"] -= 1
    elif query.data == "log_next" and data["index"] + 1 < len(data["pages"]):
        data["index"] += 1
    elif query.data == "log_prev2":
        data["index"] = max(0, data["index"] - 2)
    elif query.data == "log_next2":
        data["index"] = min(len(data["pages"]) - 1, data["index"] + 2)
    else:
        return await safe_answer(query, "Cannot navigate further", show_alert=False)

    page = data["pages"][data["index"]]
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    await safe_answer(query)

# -------------------------------
# REFRESH HANDLER
# -------------------------------
@Client.on_callback_query(filters.regex("^log_refresh$"))
async def log_refresh_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await safe_answer(query, "Session expired", show_alert=True)

    # Temporarily change only the refresh button
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data == "log_refresh":
                btn.text = "⏳ Refreshing..."
    await query.message.edit_reply_markup(markup)

    try:
        path = ospath.abspath("log.txt")
        async with aiofiles.open(path, "r") as f:
            content = await f.read()

        yaso_url = await paste_to_yaso(content)
        paste_url = yaso_url if not yaso_url.startswith("Error") else await paste_to_spacebin(content)

        pages = chunk_text(content)
        current_index = min(data["index"], len(pages) - 1)
        LOG_CACHE[msg_id] = {"pages": pages, "url": paste_url, "index": current_index, "selector_start": 0}

        page_content = pages[current_index]
        await query.message.edit_text(f"<pre>{page_content}</pre>",
                                      reply_markup=build_main_markup(current_index, len(pages), paste_url))
        await safe_answer(query, "✅ Log refreshed")
    except Exception as e:
        await safe_answer(query, "⚠️ Error refreshing log", show_alert=True)
        print(f"Error in log_refresh_handler: {e}")

# -------------------------------
# CLOSE HANDLER
# -------------------------------
@Client.on_callback_query(filters.regex("^log_close$"))
async def log_close_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    LOG_CACHE.pop(msg_id, None)
    await query.message.delete()
    await safe_answer(query, "Closed.")

@Client.on_callback_query(filters.regex("^selector_null$"))
async def selector_null(client, query: CallbackQuery):
    await safe_answer(query, "📌 Select page number from below ⬇️")
