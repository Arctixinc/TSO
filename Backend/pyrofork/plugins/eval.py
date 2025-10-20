
import asyncio
from io import BytesIO
import io
import logging
import os
import sys
import traceback
from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters


@Client.on_message(filters.command(['shell','sh']) & CustomFilters.owner)
async def execution(bot, message):
    status_message = await message.reply_text("Processing ...")
    try:
        cmd = message.text.split(" ", maxsplit=1)[1]
    except:
        await status_message.edit(text="input not found")
        return
    reply_to_ = message
    process = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    e = stderr.decode()
    if not e:
        e = "No Error"
    o = stdout.decode()
    if not o:
        o = "No Output"

    OUTPUT = ""
    OUTPUT += f"<b>QUERY:</b>\n<u>Command:</u>\n<code>{cmd}</code> \n"
    OUTPUT += f"<u>PID</u>: <code>{process.pid}</code>\n\n"
    OUTPUT += f"<b>stderr</b>: \n<code>{e}</code>\n\n"
    OUTPUT += f"<b>stdout</b>: \n<code>{o}</code>"

    if len(OUTPUT) > 4096:
        with BytesIO(str.encode(OUTPUT)) as out_file:
            out_file.name = "exec.text"
            await reply_to_.reply_document(
                document=out_file,
                caption=cmd,
                disable_notification=True
            )
    else:
        await reply_to_.reply_text(OUTPUT)

    await status_message.delete()
    
@Client.on_message(filters.command(['eval']), CustomFilters.owner)
async def eval(client, message):
    replied = message.reply_to_message
    status_message = await message.reply_text("Processing ...")
    if replied and replied.document and replied.document.file_name.endswith(
                    ('.py', '.txt')):
        path = await message.reply_to_message.download()
        with open(path, "r") as jv:
            cmd = jv.read()
        try:
            os.remove(path)
        except:
            pass
    else:
        try:
            cmd = message.text.split(" ", maxsplit=1)[1]
        except:
            await status_message.edit(text="input not found")
            return
    
    reply_to_ = message
    old_stderr = sys.stderr
    old_stdout = sys.stdout
    redirected_output = sys.stdout = io.StringIO()
    redirected_error = sys.stderr = io.StringIO()
    stdout, stderr, exc = None, None, None

    try:
        await aexec(cmd, client, message)
    except Exception:
        exc = traceback.format_exc()

    stdout = redirected_output.getvalue()
    stderr = redirected_error.getvalue()
    sys.stdout = old_stdout
    sys.stderr = old_stderr

    evaluation = ""
    if exc:
        evaluation = exc
    elif stderr:
        evaluation = stderr
    elif stdout:
        evaluation = stdout
    else:
        evaluation = "Success"

    final_output = "<b>EVAL</b>: "
    final_output += f"<code>{cmd}</code>\n\n"
    final_output += "<b>OUTPUT</b>:\n"
    final_output += f"<code>{evaluation.strip()}</code> \n"

    if len(final_output) > 4096:
        with io.BytesIO(str.encode(final_output)) as out_file:
            out_file.name = "eval.text"
            await reply_to_.reply_document(
                document=out_file,
                caption=cmd,
                disable_notification=True
            )
    else:
        await reply_to_.reply_text(final_output)
    await status_message.delete()


async def aexec(code, client, message):
    exec(
        'async def __aexec(client, message): ' +
        ''.join(f'\n {l_}' for l_ in code.split('\n'))
    )
    return await locals()['__aexec'](client, message)
