import asyncio
import io
import os
import sys
import time
import traceback
import html
from io import BytesIO
from pyrogram import Client, filters
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER


# ------------------ SHELL COMMAND HANDLER ------------------
@Client.on_message(filters.command(["shell", "sh"]) & CustomFilters.owner)
@Client.on_edited_message(filters.command(["shell", "sh"]) & CustomFilters.owner)
async def shell_handler(client, message):
    status_message = await message.reply_text("Processing ...")
    LOGGER.info(f"Shell command invoked by {message.from_user.id}")

    try:
        cmd = None

        # Use replied message first
        if message.reply_to_message:
            reply = message.reply_to_message
            if reply.text:
                cmd = reply.text.strip()
            elif reply.caption:
                cmd = reply.caption.strip()
            elif (
                reply.document
                and reply.document.file_name.endswith(('.sh', '.txt'))
            ):
                path = await reply.download()
                with open(path, "r") as f:
                    cmd = f.read().strip()
                os.remove(path)

        # Fallback to inline command
        if not cmd:
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                await status_message.edit(
                    "❗**Usage:** `/sh <command>`",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            cmd = parts[1]

        LOGGER.info(f"Executing shell command: {cmd}")

        # Execute command
        start_time = time.time()
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        end_time = time.time()
        execution_time = round(end_time - start_time, 2)

        # Raw outputs
        o = stdout.decode().strip() or "No Output"
        e = stderr.decode().strip() or "No Error"

        # HTML escaped for Telegram
        cmd_html = html.escape(cmd)
        o_html = html.escape(o)
        e_html = html.escape(e)

        output = (
            f"<b>💻 Shell Executed</b>\n\n"
            f"<b>🧾 Command:</b> <code>{cmd_html}</code>\n"
            f"<b>📌 PID:</b> <code>{process.pid}</code>\n"
            f"<b>⏱️ Time:</b> <code>{execution_time}s</code>\n\n"
            f"<b>⚠️ STDERR:</b>\n<code>{e_html}</code>\n\n"
            f"<b>✅ STDOUT:</b>\n<code>{o_html}</code>"
        )

        # If too long — send file with RAW text (no HTML escape!)
        if len(output) > 4096:
            raw_output = (
                f"Command:\n{cmd}\n\n"
                f"PID: {process.pid}\n"
                f"Time: {execution_time}s\n\n"
                f"STDERR:\n{e}\n\n"
                f"STDOUT:\n{o}"
            )

            with BytesIO(raw_output.encode()) as out_file:
                out_file.name = "shell_output.txt"
                await message.reply_document(
                    document=out_file,
                    caption=f"💻 Command: {cmd}",
                    disable_notification=True
                )
        else:
            await message.reply_text(output, parse_mode=ParseMode.HTML)

    except Exception as err:
        LOGGER.error(f"Error during shell execution: {err}", exc_info=True)
        await message.reply_text(
            f"⚠️ Error: <code>{html.escape(str(err))}</code>",
            parse_mode=ParseMode.HTML
        )

    finally:
        try:
            await status_message.delete()
        except Exception:
            pass


# ------------------ EVAL COMMAND HANDLER ------------------
@Client.on_message(filters.command(["eval"]) & CustomFilters.owner)
@Client.on_edited_message(filters.command(["eval"]) & CustomFilters.owner)
async def eval_handler(client, message):
    status_message = await message.reply_text("Processing ...")
    LOGGER.info(f"Eval command invoked by {message.from_user.id}")

    cmd = None

    try:
        # Replied message logic
        if message.reply_to_message:
            reply = message.reply_to_message
            if reply.text:
                cmd = reply.text.strip()
            elif reply.caption:
                cmd = reply.caption.strip()
            elif (
                reply.document
                and reply.document.file_name.endswith(('.py', '.txt'))
            ):
                path = await reply.download()
                with open(path, "r") as f:
                    cmd = f.read()
                os.remove(path)

        # Inline fallback
        if not cmd:
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                await status_message.edit(
                    "❗**Usage:** `/eval <code>`",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            cmd = parts[1]

        LOGGER.info(f"Executing eval code: {cmd[:80]}...")

        # Capture stdout/stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        exc = None

        start_time = time.time()
        try:
            await aexec(cmd, client, message)
        except Exception:
            exc = traceback.format_exc()
        end_time = time.time()
        execution_time = round(end_time - start_time, 2)

        stdout = sys.stdout.getvalue().strip()
        stderr = sys.stderr.getvalue().strip()
        sys.stdout, sys.stderr = old_stdout, old_stderr

        if exc:
            evaluation = exc
        elif stderr:
            evaluation = stderr
        elif stdout:
            evaluation = stdout
        else:
            evaluation = "✅ Success"

        # Escape for Telegram (HTML mode)
        cmd_html = html.escape(cmd)
        evaluation_html = html.escape(evaluation)

        final_output = (
            f"<b>🧠 EVAL</b>\n\n"
            f"<b>📜 Code:</b>\n<code>{cmd_html}</code>\n"
            f"<b>⏱️ Time:</b> <code>{execution_time}s</code>\n\n"
            f"<b>🖨 Output:</b>\n<code>{evaluation_html}</code>"
        )

        # Too long — send RAW output in file
        if len(final_output) > 4096:
            raw_output = (
                f"Code:\n{cmd}\n\n"
                f"Time: {execution_time}s\n\n"
                f"Output:\n{evaluation}"
            )

            with BytesIO(raw_output.encode()) as out_file:
                out_file.name = "eval_output.txt"
                await message.reply_document(
                    document=out_file,
                    caption="🧠 Eval Result",
                    disable_notification=True
                )
        else:
            await message.reply_text(final_output, parse_mode=ParseMode.HTML)

    except Exception as err:
        LOGGER.error(f"Error during eval handling: {err}", exc_info=True)
        await message.reply_text(
            f"⚠️ Error: <code>{html.escape(str(err))}</code>",
            parse_mode=ParseMode.HTML
        )

    finally:
        try:
            await status_message.delete()
        except Exception:
            pass


# ------------------ ASYNC EXECUTOR ------------------
async def aexec(code, client, message):
    """Execute async code dynamically in eval context"""
    env = {"client": client, "message": message}
    exec(
        "async def __aexec(client, message):\n"
        + "\n".join(f"    {line}" for line in code.split("\n")),
        env
    )
    return await env["__aexec"](client, message)
