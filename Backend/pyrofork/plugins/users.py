from pyrogram import Client, filters
from pyrogram.types import Message
from Backend.config import Telegram
from Backend import db
from Backend.fastapi.security.credentials import hash_password
from Backend.helper.custom_filter import CustomFilters

@Client.on_message(filters.command("adduser") & CustomFilters.owner)
async def add_user_cmd(client: Client, message: Message):
    if len(message.command) != 3:
        return await message.reply_text("Usage: /adduser <username> <password>")

    username = message.command[1]
    password = message.command[2]

    # Hash password
    hashed_pw = hash_password(password)

    success = await db.add_user(username, hashed_pw, role="user")
    if success:
        await message.reply_text(f"User '{username}' added successfully!")
    else:
        await message.reply_text(f"User '{username}' already exists!")

@Client.on_message(filters.command("deluser") & CustomFilters.owner)
async def del_user_cmd(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text("Usage: /deluser <username>")

    username = message.command[1]

    if username == Telegram.ADMIN_USERNAME:
        return await message.reply_text("Cannot delete the root admin user!")

    success = await db.delete_user(username)
    if success:
        await message.reply_text(f"User '{username}' deleted successfully!")
    else:
        await message.reply_text(f"User '{username}' not found!")

@Client.on_message(filters.command("alluser") & CustomFilters.owner)
async def all_user_cmd(client: Client, message: Message):
    users = await db.list_users()
    if not users:
        return await message.reply_text("No database users found.")

    response = "**Registered Users:**\n\n"
    for u in users:
        response += f"• `{u['username']}` ({u['role']})\n"

    await message.reply_text(response)

@Client.on_message(filters.command("upuser") & CustomFilters.owner)
async def up_user_cmd(client: Client, message: Message):
    if len(message.command) != 3:
        return await message.reply_text("Usage: /upuser <username> <new_password>")

    username = message.command[1]
    new_password = message.command[2]

    hashed_pw = hash_password(new_password)

    success = await db.update_user_password(username, hashed_pw)
    if success:
        await message.reply_text(f"Password for '{username}' updated successfully!")
    else:
        await message.reply_text(f"User '{username}' not found!")
