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
# PAGINATION STATE
# -------------------------------
LOG_CACHE = {}  # message_id -> {"pages": [...], "url": str, "index": int}

def chunk_text(text: str, chunk_size=3500):
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

def build_markup(index: int, total: int, url: str):
    buttons = []

    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("⏮ Prev", callback_data="log_prev"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("⏭ Next", callback_data="log_next"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="log_refresh"),
        InlineKeyboardButton("🌐 Open URL", url=url)
    ])

    buttons.append([InlineKeyboardButton("⏹ Close", callback_data="log_close")])
    return InlineKeyboardMarkup(buttons)

# -------------------------------
# COMMAND
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

        if len(content) < 3500:
            # Small logs → show directly
            return await message.reply_text(
                f"<pre>{content}</pre>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open URL", url=paste_url)]]),
            )

        # Large logs → send as document with buttons
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📜 Show here", callback_data="log_show"),
                InlineKeyboardButton("🌐 Open URL", url=paste_url)
            ]
        ])

        await message.reply_document(
            document=path,
            caption="🪵 Log File",
            reply_markup=markup,
            quote=True
        )

    except Exception as e:
        await message.reply_text(f"⚠️ Error: {e}")
        print(f"Error in /log command: {e}")

# -------------------------------
# CALLBACKS
# -------------------------------

@Client.on_callback_query(filters.regex("^log_show$"))
async def log_show_handler(client: Client, query: CallbackQuery):
    try:
        path = ospath.abspath("log.txt")
        async with aiofiles.open(path, "r") as f:
            content = await f.read()

        pages = chunk_text(content)
        paste_url = query.message.reply_markup.inline_keyboard[0][1].url

        # Send new text message with first page
        msg = await query.message.reply_text(
            f"<pre>{pages[0]}</pre>",
            reply_markup=build_markup(0, len(pages), paste_url)
        )

        LOG_CACHE[msg.id] = {"pages": pages, "url": paste_url, "index": 0}

        await query.answer("Log opened ✅")

    except Exception as e:
        await query.answer("Error loading log.", show_alert=True)
        print(f"Error in log_show_handler: {e}")

@Client.on_callback_query(filters.regex("^log_next$"))
async def log_next_handler(client: Client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await query.answer("Session expired.", show_alert=True)

    if data["index"] + 1 >= len(data["pages"]):
        return await query.answer("No more pages.", show_alert=False)

    data["index"] += 1
    page = data["pages"][data["index"]]
    markup = build_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    await query.answer()

@Client.on_callback_query(filters.regex("^log_prev$"))
async def log_prev_handler(client: Client, query: CallbackQuery):
    msg_id = query.message.id
    data = LOG_CACHE.get(msg_id)
    if not data:
        return await query.answer("Session expired.", show_alert=True)

    if data["index"] == 0:
        return await query.answer("Already at first page.", show_alert=False)

    data["index"] -= 1
    page = data["pages"][data["index"]]
    markup = build_markup(data["index"], len(data["pages"]), data["url"])
    await query.message.edit_text(f"<pre>{page}</pre>", reply_markup=markup)
    await query.answer()

@Client.on_callback_query(filters.regex("^log_refresh$"))
async def log_refresh_handler(client: Client, query: CallbackQuery):
    try:
        path = ospath.abspath("log.txt")
        async with aiofiles.open(path, "r") as f:
            content = await f.read()

        msg_id = query.message.id
        data = LOG_CACHE.get(msg_id)
        if not data:
            return await query.answer("Session expired.", show_alert=True)

        pages = chunk_text(content)
        LOG_CACHE[msg_id] = {"pages": pages, "url": data["url"], "index": 0}

        await query.message.edit_text(f"<pre>{pages[0]}</pre>", reply_markup=build_markup(0, len(pages), data["url"]))
        await query.answer("✅ Log refreshed")

    except Exception as e:
        await query.answer("Error refreshing log.", show_alert=True)
        print(f"Error in log_refresh_handler: {e}")

@Client.on_callback_query(filters.regex("^log_close$"))
async def log_close_handler(client: Client, query: CallbackQuery):
    msg_id = query.message.id
    LOG_CACHE.pop(msg_id, None)
    await query.message.delete()
    await query.answer("Closed.")
