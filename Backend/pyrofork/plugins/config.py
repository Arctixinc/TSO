from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums.parse_mode import ParseMode
import asyncio

from Backend.config import Telegram
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db

# Internal/Private vars to exclude
EXCLUDE_CONFIGS = ["API_ID", "API_HASH", "DATABASE"]

def get_editable_configs():
    configs = []
    for key in dir(Telegram):
        if not key.startswith("_") and key not in EXCLUDE_CONFIGS:
            val = getattr(Telegram, key)
            if not callable(val):
                configs.append(key)
    return sorted(configs)

# Pagination helper
def build_config_markup(page=0, page_size=6):
    configs = get_editable_configs()
    total_configs = len(configs)
    total_pages = (total_configs + page_size - 1) // page_size

    start = page * page_size
    end = start + page_size
    current_batch = configs[start:end]

    buttons = []
    for key in current_batch:
        buttons.append([InlineKeyboardButton(f"⚙️ {key}", callback_data=f"conf_edit_{key}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"conf_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"conf_page_{page+1}"))

    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([InlineKeyboardButton("❌ Close", callback_data="conf_close")])
    buttons.append([InlineKeyboardButton("⚠️ Restart Bot", callback_data="conf_restart")])

    return InlineKeyboardMarkup(buttons)

@Client.on_message(filters.command('config') & filters.private & CustomFilters.owner)
async def config_handler(client: Client, message: Message):
    """
    Manage Environment Variables dynamically.
    """
    await message.reply_text(
        "🛠 **Configuration Manager**\n\nSelect a variable to edit:",
        reply_markup=build_config_markup(0),
        parse_mode=ParseMode.MARKDOWN
    )

@Client.on_callback_query(filters.regex(r"^conf_"))
async def config_callback(client: Client, query: CallbackQuery):
    await query.answer() # Acknowledge immediately to prevent timeout
    data = query.data

    if data == "conf_close":
        await query.message.delete()
        return

    if data == "conf_restart":
        await query.message.edit_text("🔄 Restarting bot...")
        # Trigger restart (assuming external restart script or Docker)
        # We can use os.execl or similar if supported, or just sys.exit()
        import sys
        import os
        os.execl(sys.executable, sys.executable, "-m", "Backend")
        return

    if data.startswith("conf_page_"):
        page = int(data.split("_")[-1])
        await query.message.edit_text(
            "🛠 **Configuration Manager**\n\nSelect a variable to edit:",
            reply_markup=build_config_markup(page),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data.startswith("conf_edit_"):
        key = data.replace("conf_edit_", "")
        current_val = getattr(Telegram, key, "N/A")

        text = (
            f"✏️ **Edit Configuration**\n\n"
            f"**Variable:** `{key}`\n"
            f"**Current Value:** `{current_val}`\n\n"
            f"👇 Send the new value as a text message."
        )

        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="conf_page_0")]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )

        # Listen for next message using a temporary handler (Robust manual listener)
        try:
            chat_id = query.message.chat.id
            user_id = query.from_user.id

            # Create a future to store the result
            future = asyncio.get_event_loop().create_future()

            async def next_message_handler(client, message):
                if message.chat.id == chat_id and message.from_user.id == user_id and message.text:
                    if not future.done():
                        future.set_result(message)
                    # Stop propagation to prevent other handlers from catching this config input
                    message.stop_propagation()

            # Register temporary handler
            group = -999 # High priority group
            handler_obj = client.add_handler(
                filters.create(lambda _, __, m: m.chat.id == chat_id and m.from_user.id == user_id),
                group
            )

            # Wait for response (60s timeout)
            try:
                # We need to manually inject the handler function logic via the filter or a proper handler
                # add_handler requires a Handler object.
                # Let's use MessageHandler.
                from pyrogram.handlers import MessageHandler

                temp_handler = MessageHandler(next_message_handler, filters.user(user_id) & filters.chat(chat_id) & filters.text)
                client.add_handler(temp_handler, group)

                response = await asyncio.wait_for(future, timeout=60)

                # Cleanup
                client.remove_handler(temp_handler, group)

                if response and response.text:
                    new_value = response.text

                    # Basic Type Inference
                    if new_value.lower() == "true": new_value = True
                    elif new_value.lower() == "false": new_value = False
                    elif new_value.isdigit(): new_value = int(new_value)

                    # Save to DB
                    await db.set_config(key, new_value)

                    # Update Memory
                    setattr(Telegram, key, new_value)

                    await query.message.edit_text(
                        f"✅ **Updated** `{key}`\n\n"
                        f"New Value: `{new_value}`\n\n"
                        f"⚠️ Restart bot to fully apply changes.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Back", callback_data="conf_page_0")]
                        ]),
                        parse_mode=ParseMode.MARKDOWN
                    )
            except asyncio.TimeoutError:
                client.remove_handler(temp_handler, group)
                await query.message.edit_text("❌ Timeout. Input cancelled.", reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="conf_page_0")]
                    ]))

        except Exception as e:
            LOGGER.error(f"Config Edit Error: {e}")
            await query.message.edit_text("❌ Error during input handling. Try again.")
