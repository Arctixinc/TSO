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
# MARKUPS
# -------------------------------
def build_main_markup(index: int, total: int, url: str):
    buttons = []

    # Navigation row
    nav_row = []
    if index > 1:
        nav_row.append(InlineKeyboardButton("⏮<<", callback_data="log_prev2"))
    if index > 0:
        nav_row.append(InlineKeyboardButton("⬅", callback_data="log_prev"))

    nav_row.append(InlineKeyboardButton(f"📄 {index + 1}/{total}", callback_data="log_null"))

    if index < total - 1:
        nav_row.append(InlineKeyboardButton("➡", callback_data="log_next"))
    if index < total - 2:
        nav_row.append(InlineKeyboardButton(">>⏭", callback_data="log_next2"))

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
        LOG_CACHE[message.id] = {"pages": pages, "url": paste_url, "index": len(pages)-1, "selector_start": 0}

        if len(content) < 3500:
            # Small logs → show directly
            return await message.reply_text(
                f"<pre>{content}</pre>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open URL", url=paste_url)]]),
            )

        # Large logs → show preview last 20 lines first
        lines = content.strip().splitlines()
        preview_lines = lines[-20:] if len(lines) > 20 else lines
        preview_text = "<pre>" + "\n".join(preview_lines) + "</pre>"

        markup = build_main_markup(len(pages)-1, len(pages), paste_url)

        await message.reply_text(
            preview_text,
            reply_markup=markup,
            quote=True
        )

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
    await query.answer()

@Client.on_callback_query(filters.regex(r"^log_page_(\d+)$"))
async def page_button(client, query: CallbackQuery):
    msg_id = query.message.id
    page_index = int(query.data.split("_")[-1])
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await query.answer("Session expired", show_alert=True)

    data["index"] = page_index
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    page_content = data["pages"][data["index"]]
    await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=markup)
    await query.answer(f"Page {page_index + 1}")

@Client.on_callback_query(filters.regex("^selector_prev$"))
async def selector_prev(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await query.answer("Session expired", show_alert=True)
    data["selector_start"] = max(0, data.get("selector_start", 0) - 25)
    await query.message.edit_reply_markup(build_selector_markup(msg_id))
    await query.answer()

@Client.on_callback_query(filters.regex("^selector_next$"))
async def selector_next(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await query.answer("Session expired", show_alert=True)
    data["selector_start"] = min(len(data["pages"]) - 1, data.get("selector_start", 0) + 25)
    await query.message.edit_reply_markup(build_selector_markup(msg_id))
    await query.answer()

@Client.on_callback_query(filters.regex("^selector_back$"))
async def selector_back(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await query.answer("Session expired", show_alert=True)
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_reply_markup(markup)
    await query.answer()

# -------------------------------
# Single-step & Double-step navigation
# -------------------------------
@Client.on_callback_query(filters.regex("^log_prev$"))
async def log_prev_handler(client: Client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await query.answer("Session expired", show_alert=True)
    if data["index"] == 0: return await query.answer("Already at first page", show_alert=False)
    data["index"] -= 1
    page = data["pages"][data["index"]]
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    await query.answer()

@Client.on_callback_query(filters.regex("^log_next$"))
async def log_next_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await query.answer("Session expired", show_alert=True)
    if data["index"] + 1 >= len(data["pages"]): return await query.answer("No more pages", show_alert=False)
    data["index"] += 1
    page = data["pages"][data["index"]]
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    await query.answer()

@Client.on_callback_query(filters.regex("^log_prev2$"))
async def log_prev2_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await query.answer("Session expired", show_alert=True)
    data["index"] = max(0, data["index"] - 2)
    page = data["pages"][data["index"]]
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    await query.answer()

@Client.on_callback_query(filters.regex("^log_next2$"))
async def log_next2_handler(client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data: return await query.answer("Session expired", show_alert=True)
    data["index"] = min(len(data["pages"]) - 1, data["index"] + 2)
    page = data["pages"][data["index"]]
    markup = build_main_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    await query.answer()

# -------------------------------
# Refresh log
# -------------------------------
@Client.on_callback_query(filters.regex("^log_refresh$"))
async def log_refresh_handler(client: Client, query: CallbackQuery):
    try:
        path = ospath.abspath("log.txt")
        async with aiofiles.open(path, "r") as f:
            content = await f.read()

        msg_id = query.message.id
        data = LOG_CACHE.get(msg_id)
        if not data: return await query.answer("Session expired", show_alert=True)

        yaso_url = await paste_to_yaso(content)
        paste_url = yaso_url if not yaso_url.startswith("Error") else await paste_to_spacebin(content)

        pages = chunk_text(content)
        current_index = min(data["index"], len(pages)-1)
        LOG_CACHE[msg_id] = {"pages": pages, "url": paste_url, "index": current_index, "selector_start": 0}

        page_content = pages[current_index]
        try:
            await query.message.edit_text(f"<pre>{page_content}</pre>", reply_markup=build_main_markup(current_index, len(pages), paste_url))
            await query.answer("✅ Log refreshed")
        except MessageNotModified:
            await query.answer("ℹ️ No updates in the log", show_alert=False)

    except Exception as e:
        await query.answer("⚠️ Error refreshing log", show_alert=True)
        print(f"Error in log_refresh_handler: {e}")

# -------------------------------
# Close
# -------------------------------
@Client.on_callback_query(filters.regex("^log_close$"))
async def log_close_handler(client: Client, query: CallbackQuery):
    msg_id = query.message.id
    LOG_CACHE.pop(msg_id, None)
    await query.message.delete()
    await query.answer("Closed.")
