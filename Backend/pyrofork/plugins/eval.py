import asyncio
import io
import sys
import os
import traceback
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from io import BytesIO
from Backend.helper.custom_filter import CustomFilters

# ---------------- HELPERS ----------------
async def generate_random_string(length=32):
    import random, string
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
        return (stdout.decode() or "") + (stderr.decode() or "")
    except Exception as e:
        return f"⚠ Error running command:\n{e}"

async def aexec(code, client, message):
    env = {}
    exec(
        "async def __aexec(client, message):\n"
        + "\n".join(f"    {line}" for line in code.split("\n")),
        env
    )
    return await env["__aexec"](client, message)

async def evaluate_code(client, message, code: str) -> str:
    old_stdout, old_stderr = sys.stdout, sys.stderr
    redirected_output = sys.stdout = io.StringIO()
    redirected_error = sys.stderr = io.StringIO()
    exc = None

    try:
        await aexec(code, client, message)
    except Exception:
        exc = traceback.format_exc()

    stdout = redirected_output.getvalue()
    stderr = redirected_error.getvalue()
    sys.stdout, sys.stderr = old_stdout, old_stderr

    if exc:
        return exc
    elif stderr:
        return stderr
    elif stdout:
        return stdout
    else:
        return "✅ Success"

# ---------------- BUTTONS ----------------
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
    """Send output with upload button if too long"""
    if len(text) > 2000:
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
async def eval_handler(client, message: Message):
    status_msg = await message.reply_text("Processing ...")
    # Get code from reply file or command
    if message.reply_to_message and message.reply_to_message.document and \
        message.reply_to_message.document.file_name.endswith(('.py', '.txt')):
        path = await message.reply_to_message.download()
        with open(path, "r") as f:
            code = f.read()
        try: os.remove(path)
        except: pass
    else:
        try:
            code = message.text.split(None, 1)[1]
        except:
            await status_msg.edit("❗Usage: `/eval code`")
            return
    output = await evaluate_code(client, message, code)
    message._output_data = output
    await send_output(message, output, "eval", code)
    await status_msg.delete()

@Client.on_message(filters.command(["sh", "shell"]) & CustomFilters.owner)
async def shell_handler(client, message: Message):
    status_msg = await message.reply_text("Processing ...")

    if (
        message.reply_to_message
        and message.reply_to_message.document
        and message.reply_to_message.document.file_name.endswith(('.sh', '.txt'))
    ):
        path = await message.reply_to_message.download()
        with open(path, "r") as f:
            cmd = f.read().strip()
        try:
            os.remove(path)
        except:
            pass
    else:
        parts = message.text.split(None, 1)
        if len(parts) < 2:
            await status_msg.edit(
                "❗Usage: `/sh <command>`",
                parse_mode=None,
                disable_web_page_preview=True
            )
            return
        cmd = parts[1]

    output = await run_shell(cmd)
    message._output_data = output
    await send_output(message, output, "sh", cmd)
    await status_msg.delete()


# ---------------- CALLBACKS ----------------
@Client.on_callback_query(filters.regex("^upload:(.+)$") & CustomFilters.owner)
async def upload_callback(_, query):
    data = query.data.split("upload:", 1)[1]
    cmd_type, cmd_input = data.split("|", 1)
    await query.answer("⏳ Uploading...")
    output = getattr(query.message, "_output_data", None)
    if not output:
        return await query.answer("❌ Output not found.", show_alert=True)

    paste_url = await paste_to_yaso(output)
    if paste_url.startswith("Error"):
        paste_url = await paste_to_spacebin(output)

    await query.message.edit_text(
        f"**📤 {cmd_type.upper()} Output Uploaded!**",
        reply_markup=url_button(paste_url)
    )

@Client.on_callback_query(filters.regex("^close$") & CustomFilters.owner)
async def close_callback(_, query):
    await query.answer("🗑 Closed")
    await query.message.delete()
