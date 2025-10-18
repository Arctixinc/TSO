from pyrogram import filters, Client
from pyrogram.types import Message
from os import path as ospath

from Backend.helper.custom_filter import CustomFilters

#@Client.on_message(filters.command('log') & filters.private & CustomFilters.owner, group=10)
async def logg(client: Client, message: Message):
    try:
        path = ospath.abspath('log.txt')
        if not ospath.exists(path):
            return await message.reply_text("> ❌ Log file not found.")
        
        await message.reply_document(
            document=path,
            quote=True,
            disable_notification=True
        )
    except Exception as e:
        await message.reply_text(f"⚠️ Error: {e}")
        print(f"Error in /log: {e}")



from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from os import path as ospath
import aiofiles
import aiohttp
import asyncio
import random
import string

from Backend.helper.custom_filter import CustomFilters


# -------------------------------
#  HELPERS
# -------------------------------

async def generate_random_string(length=32):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


async def paste_to_spacebin(content: str):
    """Async paste to spaceb.in"""
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
    """Async paste to yaso.su"""
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
#  COMMAND HANDLER
# -------------------------------

@Client.on_message(filters.command(["log", "logs"]) & filters.private & CustomFilters.owner, group=10)
async def log(client: Client, message: Message):
    try:
        path = ospath.abspath("log.txt")
        if not ospath.exists(path):
            return await message.reply_text("> ❌ Log file not found.")

        async with aiofiles.open(path, "r") as f:
            content = await f.read()

        # If content is small enough, show directly
        if len(content) < 4000:
            return await message.reply_text(f"<pre language=python>{content}</pre>")

        # For large logs, paste online (Yaso first, fallback to Spacebin)
        yaso_url = await paste_to_yaso(content)
        if not yaso_url.startswith("Error"):
            paste_url = yaso_url
        else:
            paste_url = await paste_to_spacebin(content)

        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🌐 Web View", url=paste_url)]]
        )

        await message.reply_document(
            document=path,
            caption=f"🪵 Log File",
            reply_markup=markup,
            quote=True,
            disable_notification=True
        )

    except Exception as e:
        await message.reply_text(f"⚠️ Error: {e}")
        print(f"Error in /log command: {e}")
        
