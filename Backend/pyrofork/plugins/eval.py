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


# ==========================================================
#                     SHELL COMMAND
# ==========================================================
@Client.on_message(filters.command(["shell", "sh"]) & CustomFilters.owner)
@Client.on_edited_message(filters.command(["shell", "sh"]) & CustomFilters.owner)
async def shell_handler(client, message):

    cmd = None

    # ---------- extract command ----------
    if message.reply_to_message:
        r = message.reply_to_message
        if r.text:
            cmd = r.text.strip()
        elif r.caption:
            cmd = r.caption.strip()
        elif r.document and r.document.file_name and r.document.file_name.endswith((".sh", ".txt")):
            path = await r.download()
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    cmd = f.read().strip()
            finally:
                if os.path.exists(path):
                    os.remove(path)

    if not cmd:
        if not message.text or len(message.text.split(maxsplit=1)) < 2:
            await message.reply_text(
                "❗ **Usage:** `/sh <command>`\n\nExample: `/sh ls -la`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        cmd = message.text.split(maxsplit=1)[1].strip()

    if not cmd:
        await message.reply_text(
            "❗ **Usage:** `/sh <command>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ---------- now send processing ----------
    status_message = await message.reply_text("⏳ Processing ...")
    LOGGER.info(f"Executing shell command: {cmd[:100]}")

    try:
        start = time.time()

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
        except asyncio.TimeoutError:
            await message.reply_text("⚠️ Command timed out")
            return

        exec_time = round(time.time() - start, 2)

        out = stdout.decode(errors="ignore").strip() or "No Output"
        err = stderr.decode(errors="ignore").strip() or "No Error"

        result = (
            f"<b>💻 Shell Executed</b>\n\n"
            f"<b>🧾 Command:</b> <code>{html.escape(cmd[:500])}</code>\n"
            f"<b>📌 Return Code:</b> <code>{process.returncode}</code>\n"
            f"<b>⏱️ Time:</b> <code>{exec_time}s</code>\n\n"
            f"<b>⚠️ STDERR:</b>\n<code>{html.escape(err[:2000])}</code>\n\n"
            f"<b>✅ STDOUT:</b>\n<code>{html.escape(out[:2000])}</code>"
        )

        if len(result) > 4096:
            with BytesIO(
                f"Command:\n{cmd}\n\nSTDERR:\n{err}\n\nSTDOUT:\n{out}".encode()
            ) as f:
                f.name = "shell_output.txt"
                await message.reply_document(f, caption="💻 Shell Output")
        else:
            await message.reply_text(result, parse_mode=ParseMode.HTML)

    except Exception as e:
        LOGGER.error("Shell error", exc_info=True)
        await message.reply_text(
            f"⚠️ Error:\n<code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML
        )

    finally:
        try:
            await status_message.delete()
        except Exception:
            pass


# ==========================================================
#                       EVAL COMMAND
# ==========================================================
@Client.on_message(filters.command(["eval"]) & CustomFilters.owner)
@Client.on_edited_message(filters.command(["eval"]) & CustomFilters.owner)
async def eval_handler(client, message):

    code = None

    # ---------- extract code ----------
    if message.reply_to_message:
        r = message.reply_to_message
        if r.text:
            code = r.text
        elif r.caption:
            code = r.caption
        elif r.document and r.document.file_name and r.document.file_name.endswith((".py", ".txt")):
            path = await r.download()
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    code = f.read()
            finally:
                if os.path.exists(path):
                    os.remove(path)

    if not code:
        if not message.text or len(message.text.split(maxsplit=1)) < 2:
            await message.reply_text(
                "❗ **Usage:** `/eval <code>`\n\nExample: `/eval print('Hello')`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        code = message.text.split(maxsplit=1)[1].strip()

    if not code:
        await message.reply_text(
            "❗ **Usage:** `/eval <code>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ---------- now send processing ----------
    status_message = await message.reply_text("⏳ Processing ...")
    LOGGER.info("Executing eval")

    try:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()

        start = time.time()
        error = None

        try:
            await asyncio.wait_for(aexec(code, client, message), timeout=60)
        except asyncio.TimeoutError:
            error = "⚠️ Execution timed out (60s)"
        except Exception:
            error = traceback.format_exc()

        exec_time = round(time.time() - start, 2)

        stdout = sys.stdout.getvalue().strip()
        stderr = sys.stderr.getvalue().strip()

        sys.stdout, sys.stderr = old_stdout, old_stderr

        output = error or stderr or stdout or "✅ Success"

        result = (
            f"<b>🧠 EVAL</b>\n\n"
            f"<b>📜 Code:</b>\n<code>{html.escape(code[:500])}</code>\n\n"
            f"<b>⏱️ Time:</b> <code>{exec_time}s</code>\n\n"
            f"<b>🖨 Output:</b>\n<code>{html.escape(output[:3000])}</code>"
        )

        if len(result) > 4096:
            with BytesIO(output.encode()) as f:
                f.name = "eval_output.txt"
                await message.reply_document(f, caption="🧠 Eval Output")
        else:
            await message.reply_text(result, parse_mode=ParseMode.HTML)

    except Exception as e:
        LOGGER.error("Eval error", exc_info=True)
        await message.reply_text(
            f"⚠️ Error:\n<code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML
        )

    finally:
        try:
            await status_message.delete()
        except Exception:
            pass


# ==========================================================
#                   ASYNC EXECUTOR
# ==========================================================
async def aexec(code, client, message):
    exec(
        'async def __aexec(client, message): ' +
        ''.join(f'\n {l_}' for l_ in code.split('\n'))
    )
    return await locals()['__aexec'](client, message)
