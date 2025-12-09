from pyrogram import Client, filters
from pyrogram.types import Message, User
from Backend.helper.custom_filter import CustomFilters
from datetime import datetime

@Client.on_message(filters.command("whois") & CustomFilters.owner)
async def whois_command(client: Client, message: Message):
    cmd = message.command
    if not message.reply_to_message and len(cmd) == 1:
        get_user = message.from_user.id
    elif len(cmd) == 1:
        get_user = message.reply_to_message.from_user.id
    elif len(cmd) > 1:
        get_user = cmd[1]
        try:
            get_user = int(cmd[1])
        except ValueError:
            pass

    try:
        user = await client.get_users(get_user)
    except Exception as e:
        await message.reply_text(f"❌ User not found: {e}")
        return

    text = (
        f"👤 **User Info:**\n\n"
        f"🆔 **ID:** `{user.id}`\n"
        f"🗣 **First Name:** `{user.first_name}`\n"
    )

    if user.last_name:
        text += f"🗣 **Last Name:** `{user.last_name}`\n"

    if user.username:
        text += f"🔗 **Username:** @{user.username}\n"

    text += f"🔗 **Permalink:** [Link](tg://user?id={user.id})\n"

    if user.dc_id:
        text += f"🌍 **DC ID:** `{user.dc_id}`\n"

    if user.is_bot:
        text += f"🤖 **Is Bot:** `True`\n"

    if user.is_scam:
        text += f"⚠️ **Scam:** `True`\n"

    if user.is_fake:
        text += f"🚫 **Fake:** `True`\n"

    await message.reply_text(text)
