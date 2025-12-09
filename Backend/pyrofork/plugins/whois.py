from pyrogram import Client, filters
from pyrogram.types import Message, User
from pyrogram.errors import PeerIdInvalid, UsernameInvalid, UserNotParticipant
from Backend.helper.custom_filter import CustomFilters
from datetime import datetime

@Client.on_message(filters.command("whois") & CustomFilters.owner)
async def whois_command(client: Client, message: Message):
    cmd = message.command
    get_user = None

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
    except (PeerIdInvalid, UsernameInvalid, Exception) as e:
        # If user is not found, print what we know (the ID or input)
        await message.reply_text(f"❌ **User not found.**\n\nInput: `{get_user}`\n\nThe bot has likely not interacted with this user yet, or the ID is invalid.")
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
