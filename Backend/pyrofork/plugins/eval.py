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
                and reply.document.file_name
                and reply.document.file_name.endswith(('.sh', '.txt'))
            ):
                path = await reply.download()
                try:
                    with open(path, "r", encoding='utf-8') as f:
                        cmd = f.read().strip()
                finally:
                    if os.path.exists(path):
                        os.remove(path)

        # Fallback to inline command
        if not cmd:
            if not message.text:
                try:
                    await status_message.edit(
                        "❗**Usage:** `/sh <command>`\n\nExample: `/sh ls -la`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    await message.reply_text(
                        "❗**Usage:** `/sh <command>`\n\nExample: `/sh ls -la`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                return
            
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                try:
                    await status_message.edit(
                        "❗**Usage:** `/sh <command>`\n\nExample: `/sh ls -la`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    await message.reply_text(
                        "❗**Usage:** `/sh <command>`\n\nExample: `/sh ls -la`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                return
            
            cmd = parts[1].strip()

        if not cmd:
            try:
                await status_message.edit(
                    "❗**Usage:** `/sh <command>`\n\nExample: `/sh ls -la`",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                await message.reply_text(
                    "❗**Usage:** `/sh <command>`\n\nExample: `/sh ls -la`",
                    parse_mode=ParseMode.MARKDOWN
                )
            return

        LOGGER.info(f"Executing shell command: {cmd[:100]}...")

        # Execute command with timeout
        start_time = time.time()
        try:
            process = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=1024 * 1024  # 1MB buffer limit
                ),
                timeout=2.0
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=300.0  # 5 minute timeout
            )
        except asyncio.TimeoutError:
            await message.reply_text(
                "⚠️ Command execution timed out",
                parse_mode=ParseMode.HTML
            )
            return
        
        end_time = time.time()
        execution_time = round(end_time - start_time, 2)

        # Decode outputs with error handling
        try:
            o = stdout.decode('utf-8').strip() if stdout else ""
        except UnicodeDecodeError:
            o = stdout.decode('utf-8', errors='replace').strip() if stdout else ""
        
        try:
            e = stderr.decode('utf-8').strip() if stderr else ""
        except UnicodeDecodeError:
            e = stderr.decode('utf-8', errors='replace').strip() if stderr else ""

        o = o or "No Output"
        e = e or "No Error"

        # HTML escaped for Telegram
        cmd_html = html.escape(cmd[:500])  # Limit command display length
        o_html = html.escape(o[:2000])  # Limit output display length
        e_html = html.escape(e[:2000])  # Limit error display length

        output = (
            f"<b>💻 Shell Executed</b>\n\n"
            f"<b>🧾 Command:</b> <code>{cmd_html}</code>\n"
            f"<b>📌 Return Code:</b> <code>{process.returncode}</code>\n"
            f"<b>⏱️ Time:</b> <code>{execution_time}s</code>\n\n"
            f"<b>⚠️ STDERR:</b>\n<code>{e_html}</code>\n\n"
            f"<b>✅ STDOUT:</b>\n<code>{o_html}</code>"
        )

        # If too long — send file with RAW text
        if len(output) > 4096:
            raw_output = (
                f"Command:\n{cmd}\n\n"
                f"Return Code: {process.returncode}\n"
                f"Time: {execution_time}s\n\n"
                f"STDERR:\n{e}\n\n"
                f"STDOUT:\n{o}"
            )

            with BytesIO(raw_output.encode('utf-8')) as out_file:
                out_file.name = "shell_output.txt"
                await message.reply_document(
                    document=out_file,
                    caption=f"💻 Command: {html.escape(cmd[:100])}...",
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
                and reply.document.file_name
                and reply.document.file_name.endswith(('.py', '.txt'))
            ):
                path = await reply.download()
                try:
                    with open(path, "r", encoding='utf-8') as f:
                        cmd = f.read()
                finally:
                    if os.path.exists(path):
                        os.remove(path)

        # Inline fallback
        if not cmd:
            if not message.text:
                try:
                    await status_message.edit(
                        "❗**Usage:** `/eval <code>`\n\nExample: `/eval print('Hello')`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    await message.reply_text(
                        "❗**Usage:** `/eval <code>`\n\nExample: `/eval print('Hello')`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                return
            
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                try:
                    await status_message.edit(
                        "❗**Usage:** `/eval <code>`\n\nExample: `/eval print('Hello')`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    await message.reply_text(
                        "❗**Usage:** `/eval <code>`\n\nExample: `/eval print('Hello')`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                return
            
            cmd = parts[1].strip()

        if not cmd:
            try:
                await status_message.edit(
                    "❗**Usage:** `/eval <code>`\n\nExample: `/eval print('Hello')`",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                await message.reply_text(
                    "❗**Usage:** `/eval <code>`\n\nExample: `/eval print('Hello')`",
                    parse_mode=ParseMode.MARKDOWN
                )
            return

        LOGGER.info(f"Executing eval code: {cmd[:80]}...")

        # Capture stdout/stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        redirected_stdout = io.StringIO()
        redirected_stderr = io.StringIO()
        sys.stdout = redirected_stdout
        sys.stderr = redirected_stderr
        
        exc = None
        start_time = time.time()
        
        try:
            # Execute with timeout
            await asyncio.wait_for(
                aexec(cmd, client, message),
                timeout=60.0  # 1 minute timeout
            )
        except asyncio.TimeoutError:
            exc = "⚠️ Execution timed out (60s limit)"
        except Exception:
            exc = traceback.format_exc()
        finally:
            end_time = time.time()
            execution_time = round(end_time - start_time, 2)
            
            # Restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        stdout = redirected_stdout.getvalue().strip()
        stderr = redirected_stderr.getvalue().strip()

        if exc:
            evaluation = exc
        elif stderr:
            evaluation = stderr
        elif stdout:
            evaluation = stdout
        else:
            evaluation = "✅ Success"

        # Escape for Telegram (HTML mode)
        cmd_html = html.escape(cmd[:500])  # Limit code display
        evaluation_html = html.escape(evaluation[:3000])  # Limit output display

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

            with BytesIO(raw_output.encode('utf-8')) as out_file:
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
    # Create execution environment with available objects
    exec_globals = {
        "__builtins__": __builtins__,
        "client": client,
        "message": message,
        "asyncio": asyncio,
        "os": os,
        "sys": sys,
        "time": time,
    }
    
    # Properly indent the code
    indented_code = "\n".join(f"    {line}" for line in code.split("\n"))
    
    # Wrap code in async function
    wrapped_code = f"async def __aexec(client, message):\n{indented_code}"
    
    # Execute the function definition
    exec(wrapped_code, exec_globals)
    
    # Call and await the function
    return await exec_globals["__aexec"](client, message)
