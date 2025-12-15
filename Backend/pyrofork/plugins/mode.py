from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from Backend.config import Telegram
from Backend.helper.custom_filter import CustomFilters
from Backend import db

@Client.on_message(filters.command("mode") & CustomFilters.owner)
async def mode_command(client, message):
    buttons = [
        [
            InlineKeyboardButton(
                f"Replace Mode: {'ON' if Telegram.REPLACE_MODE else 'OFF'}",
                callback_data="toggle_replace_mode"
            )
        ],
        [InlineKeyboardButton("Close", callback_data="close_mode")]
    ]
    await message.reply_text(
        "<b>⚙️ Command Mode (Configuration)</b>\nSelect a setting to toggle:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex("^toggle_replace_mode$") & CustomFilters.owner)
async def toggle_replace_mode(client, callback_query: CallbackQuery):
    Telegram.REPLACE_MODE = not Telegram.REPLACE_MODE
    await db.set_config("REPLACE_MODE", Telegram.REPLACE_MODE)

    buttons = [
        [
            InlineKeyboardButton(
                f"Replace Mode: {'ON' if Telegram.REPLACE_MODE else 'OFF'}",
                callback_data="toggle_replace_mode"
            )
        ],
        [InlineKeyboardButton("Close", callback_data="close_mode")]
    ]

    try:
        await callback_query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        pass

    await callback_query.answer(f"Replace Mode set to {Telegram.REPLACE_MODE}")

@Client.on_callback_query(filters.regex("^close_mode$") & CustomFilters.owner)
async def close_mode_menu(client, callback_query: CallbackQuery):
    await callback_query.message.delete()
