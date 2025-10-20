import asyncio
import io
import os
import sys
import traceback
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

    cmd = None

    try:
        # ✅ Use replied message first
        if message.reply_to_message:
            reply = message.reply_to_message
            if reply.text:
                cmd = reply.text.strip()
                LOGGER.debug("Using replied message text as shell command.")
            elif reply.caption:
                cmd = reply.caption.strip()
                LOGGER.debug("Using replied caption as shell command.")
            elif (
                reply.document
                and reply.document.file_name.endswith(('.sh', '.txt'))
            ):
                path = await reply.download()
                with open(path, "r") as f:
                    cmd = f.read().strip()
                os.remove(path)
                LOGGER.debug(f"Loaded command from attached file: {path}")

        # ✅ Fallback to text after command
        if not cmd:
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                await status_message.edit("❗Usage: `/sh <command>`", parse_mode=None)
                return
            cmd = parts[1]
            LOGGER.debug("Using inline command argument.")

        LOGGER.info(f"Executing shell command: {cmd}")

        # ✅ Execute command
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        o = stdout.decode() or "No Output"
        e = stderr.decode() or "No Error"

        output = (
            f"<b>💻 Shell Executed</b>\n\n"
            f"<b>🧾 Command:</b> <code>{cmd}</code>\n"
            f"<b>📌 PID:</b> <code>{process.pid}</code>\n\n"
            f"<b>⚠️ STDERR:</b>\n<code>{e}</code>\n\n"
            f"<b>✅ STDOUT:</b>\n<code>{o}</code>"
        )

        if len(output) > 4096:
            LOGGER.debug("Output too long — sending as document.")
            with BytesIO(output.encode()) as out_file:
                out_file.name = "shell_output.txt"
                await message.reply_document(
                    document=out_file,
                    caption=f"💻 Command: {cmd}",
                    disable_notification=True
                )
        else:
            await message.reply_text(output, parse_mode=ParseMode.MARKDOWN)

        LOGGER.info("Shell command executed successfully.")

    except Exception as err:
        LOGGER.error(f"Error during shell execution: {err}", exc_info=True)
        await message.reply_text(f"⚠️ Error: <code>{err}</code>", parse_mode=ParseMode.MARKDOWN)

    finally:
        await status_message.delete()


# ------------------ EVAL COMMAND HANDLER ------------------
@Client.on_message(filters.command(["eval"]) & CustomFilters.owner)
@Client.on_edited_message(filters.command(["eval"]) & CustomFilters.owner)
async def eval_handler(client, message):
    status_message = await message.reply_text("Processing ...")
    LOGGER.info(f"Eval command invoked by {message.from_user.id}")

    cmd = None

    try:
        # ✅ Replied message logic
        if message.reply_to_message:
            reply = message.reply_to_message
            if reply.text:
                cmd = reply.text.strip()
                LOGGER.debug("Using replied message text as eval code.")
            elif reply.caption:
                cmd = reply.caption.strip()
                LOGGER.debug("Using replied caption as eval code.")
            elif (
                reply.document
                and reply.document.file_name.endswith(('.py', '.txt'))
            ):
                path = await reply.download()
                with open(path, "r") as f:
                    cmd = f.read()
                os.remove(path)
                LOGGER.debug(f"Loaded eval code from file: {path}")

        # ✅ Inline fallback
        if not cmd:
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                await status_message.edit("❗Usage: `/eval <code>`", parse_mode=None)
                return
            cmd = parts[1]
            LOGGER.debug("Using inline eval argument.")

        LOGGER.info(f"Executing eval code: {cmd[:80]}...")

        # ✅ Capture stdout/stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        exc = None

        try:
            await aexec(cmd, client, message)
        except Exception:
            exc = traceback.format_exc()
            LOGGER.error("Exception during eval execution", exc_info=True)

        stdout = sys.stdout.getvalue()
        stderr = sys.stderr.getvalue()
        sys.stdout, sys.stderr = old_stdout, old_stderr

        if exc:
            evaluation = exc
        elif stderr:
            evaluation = stderr
        elif stdout:
            evaluation = stdout
        else:
            evaluation = "✅ Success"

        final_output = (
            f"<b>🧠 EVAL</b>\n\n"
            f"<b>📜 Code:</b>\n<code>{cmd.strip()}</code>\n\n"
            f"<b>🖨 Output:</b>\n<code>{evaluation.strip()}</code>"
        )

        if len(final_output) > 4096:
            LOGGER.debug("Eval output too long — sending as document.")
            with BytesIO(final_output.encode()) as out_file:
                out_file.name = "eval_output.txt"
                await message.reply_document(
                    document=out_file,
                    caption="🧠 Eval Result",
                    disable_notification=True
                )
        else:
            await message.reply_text(final_output, parse_mode=ParseMode.MARKDOWN)

        LOGGER.info("Eval executed successfully.")

    except Exception as err:
        LOGGER.error(f"Error during eval handling: {err}", exc_info=True)
        await message.reply_text(f"⚠️ Error: <code>{err}</code>", parse_mode=ParseMode.MARKDOWN)

    finally:
        await status_message.delete()


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
