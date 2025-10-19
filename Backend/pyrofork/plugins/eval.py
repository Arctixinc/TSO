import asyncio
import io
import contextlib
import traceback
import random
import string
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from io import StringIO
from Backend.helper.custom_filter import CustomFilters



# ---------------- HELPERS ----------------
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


async def run_shell(cmd: str, timeout: int = 60) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return "❌ Timeout reached while executing command."
        if stderr:
            return stderr.decode()
        return stdout.decode() or "✅ Command executed successfully, but no output."
    except Exception as e:
        return f"⚠ Error running command:\n{e}"


async def evaluate_code(code: str) -> str:
    stdout = StringIO()
    code = code.strip("` ")
    try:
        with contextlib.redirect_stdout(stdout):
            exec(
                f"async def __aexec():\n"
                + "\n".join(f"    {line}" for line in code.split("\n"))
            )
            result = await locals()["__aexec"]()
    except Exception:
        result = traceback.format_exc()
    output = stdout.getvalue()
    return (output + str(result)) or "✅ Code executed successfully, but no output."


def initial_button(cmd_type: str, cmd_input: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload to URL", callback_data=f"upload:{cmd_type}|{cmd_input}")],
        [InlineKeyboardButton("🗑 Close", callback_data="close")]
    ])


def url_button(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Open URL", url=url)],
        [InlineKeyboardButton("🗑 Close", callback_data="close")]
    ])


async def send_output(message: Message, text: str, cmd_type: str, cmd_input: str):
    """Send output with upload button if long."""
    if len(text) > 2000:
        # Show "Upload to URL" button first
        await message.reply_text(
            f"**📤 {cmd_type.upper()} Output:**\nLong output detected.",
            reply_markup=initial_button(cmd_type, cmd_input)
        )
    else:
        await message.reply_text(
            f"**📤 {cmd_type.upper()} Output:**\n\n`{text}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Close", callback_data="close")]])
        )


# ---------------- COMMAND HANDLERS ----------------
@Client.on_message(filters.command("eval") & CustomFilters.owner)
async def eval_handler(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("❗Usage: `/eval <code>`", quote=True)
    code = message.text.split(None, 1)[1]
    output = await evaluate_code(code)
    # Store the result in the message data for later upload
    message._output_data = output
    await send_output(message, output, "eval", code)


@Client.on_message(filters.command("sh") & CustomFilters.owner)
async def shell_handler(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("❗Usage: `/sh <command>`", quote=True)
    cmd = message.text.split(None, 1)[1]
    output = await run_shell(cmd)
    message._output_data = output
    await send_output(message, output, "sh", cmd)


# ---------------- CALLBACKS ----------------
@Client.on_callback_query(filters.regex("^upload:(.+)$") & filters.user(OWNER_ID))
async def upload_callback(_, query):
    data = query.data.split("upload:", 1)[1]
    cmd_type, cmd_input = data.split("|", 1)
    await query.answer("⏳ Uploading...")

    # Access stored output from the message
    output = getattr(query.message, "_output_data", None)
    if not output:
        return await query.answer("❌ Output not found.", show_alert=True)

    # Upload to yaso first, fallback to spaceb
    paste_url = await paste_to_yaso(output)
    if paste_url.startswith("Error"):
        paste_url = await paste_to_spacebin(output)

    # Edit message with URL button
    await query.message.edit_text(
        f"**📤 {cmd_type.upper()} Output Uploaded!**",
        reply_markup=url_button(paste_url)
    )


@Client.on_callback_query(filters.regex("^close$") & filters.user(OWNER_ID))
async def close_callback(_, query):
    await query.answer("🗑 Closed")
    await query.message.delete()

                                  
