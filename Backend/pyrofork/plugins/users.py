from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ForceReply
from Backend.config import Telegram
from Backend import db
from Backend.fastapi.security.credentials import hash_password
from Backend.helper.custom_filter import CustomFilters

# --- Keyboards ---

def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 List Users", callback_data="users_list"),
         InlineKeyboardButton("➕ Add User", callback_data="users_add")],
        [InlineKeyboardButton("❌ Close", callback_data="users_close")]
    ])

def get_users_list_keyboard(users):
    buttons = []
    for user in users:
        username = user.get("username", "Unknown")
        buttons.append([InlineKeyboardButton(f"👤 {username}", callback_data=f"user_detail_{username}")])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="users_main")])
    return InlineKeyboardMarkup(buttons)

def get_user_detail_keyboard(username):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Change Password", callback_data=f"user_pass_{username}")],
        [InlineKeyboardButton("🗑️ Delete User", callback_data=f"user_del_{username}")],
        [InlineKeyboardButton("🔙 Back", callback_data="users_list")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="users_list")]])

# --- Commands ---

@Client.on_message(filters.command("users") & CustomFilters.owner)
async def users_command(client: Client, message: Message):
    await message.reply_text(
        "**👥 User Management Panel**\nSelect an action below:",
        reply_markup=get_main_menu_keyboard()
    )

# --- Callbacks ---

@Client.on_callback_query(filters.regex("^users_main$"))
async def users_main_cb(client: Client, callback: CallbackQuery):
    await callback.edit_message_text(
        "**👥 User Management Panel**\nSelect an action below:",
        reply_markup=get_main_menu_keyboard()
    )

@Client.on_callback_query(filters.regex("^users_close$"))
async def users_close_cb(client: Client, callback: CallbackQuery):
    await callback.message.delete()

@Client.on_callback_query(filters.regex("^users_list$"))
async def users_list_cb(client: Client, callback: CallbackQuery):
    users = await db.list_users()
    if not users:
        await callback.answer("No users found.", show_alert=True)
        # Even if empty, show back button
        await callback.edit_message_text(
            "**No users found.**",
            reply_markup=get_back_keyboard()
        )
        return

    await callback.edit_message_text(
        "**👥 Registered Users**\nSelect a user to manage:",
        reply_markup=get_users_list_keyboard(users)
    )

@Client.on_callback_query(filters.regex("^users_add$"))
async def users_add_cb(client: Client, callback: CallbackQuery):
    await callback.message.delete()
    # Force reply to prompt user
    await client.send_message(
        callback.message.chat.id,
        "**➕ Add New User**\n\nReply with: `username password`\nExample: `john 123456`",
        reply_markup=ForceReply(selective=True)
    )

@Client.on_callback_query(filters.regex("^user_detail_"))
async def user_detail_cb(client: Client, callback: CallbackQuery):
    username = callback.data.split("_")[2]
    user = await db.get_user(username)

    if not user:
        await callback.answer("User not found!", show_alert=True)
        await users_list_cb(client, callback)
        return

    info = (
        f"**👤 User Details**\n\n"
        f"**Username:** `{user['username']}`\n"
        f"**Role:** `{user.get('role', 'user')}`\n"
        f"**Created:** `{user.get('created_at', 'N/A')}`"
    )

    await callback.edit_message_text(
        info,
        reply_markup=get_user_detail_keyboard(username)
    )

@Client.on_callback_query(filters.regex("^user_del_"))
async def user_del_cb(client: Client, callback: CallbackQuery):
    username = callback.data.split("_")[2]

    if username == Telegram.ADMIN_USERNAME:
        await callback.answer("Cannot delete root admin!", show_alert=True)
        return

    # Confirmation step could be added here, but for now direct delete
    await db.delete_user(username)
    await callback.answer(f"User {username} deleted!", show_alert=True)
    await users_list_cb(client, callback)

@Client.on_callback_query(filters.regex("^user_pass_"))
async def user_pass_cb(client: Client, callback: CallbackQuery):
    username = callback.data.split("_")[2]
    await callback.message.delete()

    await client.send_message(
        callback.message.chat.id,
        f"**🔐 Change Password for {username}**\n\nReply with the new password.",
        reply_markup=ForceReply(selective=True)
    )

# --- Message Handlers for Inputs ---

@Client.on_message(filters.reply & CustomFilters.owner)
async def input_handler(client: Client, message: Message):
    if not message.reply_to_message or not message.reply_to_message.text:
        return

    original_text = message.reply_to_message.text

    # Handle Add User
    if "Add New User" in original_text:
        try:
            args = message.text.split(maxsplit=1)
            if len(args) != 2:
                await message.reply_text("❌ Invalid format. Please reply with `username password`.")
                return

            username, password = args
            hashed_pw = hash_password(password)

            if await db.add_user(username, hashed_pw):
                await message.reply_text(
                    f"✅ User `{username}` added successfully!",
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                await message.reply_text(
                    f"❌ User `{username}` already exists!",
                    reply_markup=get_main_menu_keyboard()
                )
        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")

    # Handle Change Password
    elif "Change Password for" in original_text:
        try:
            # Extract username from prompt: "**🔐 Change Password for {username}**..."
            # Text might be bolded in markdown, but `message.text` usually has raw text if parsed?
            # `message.reply_to_message.text` returns the plain text content (parsed).
            # "🔐 Change Password for username\n\nReply with..."
            lines = original_text.split('\n')
            first_line = lines[0] # "🔐 Change Password for username"
            username = first_line.split("for ")[1].strip()

            new_password = message.text.strip()
            if not new_password:
                await message.reply_text("❌ Password cannot be empty.")
                return

            hashed_pw = hash_password(new_password)

            if await db.update_user_password(username, hashed_pw):
                await message.reply_text(
                    f"✅ Password for `{username}` updated!",
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                await message.reply_text(
                    f"❌ User `{username}` not found!",
                    reply_markup=get_main_menu_keyboard()
                )
        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")
