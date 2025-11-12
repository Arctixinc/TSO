from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER

MAINTENANCE_MODE = False

@Client.on_message(filters.command('maintenance') & filters.private & CustomFilters.owner)
async def maintenance_command(client: Client, message: Message):
    """
    Toggles maintenance mode.
    Usage: /maintenance <on|off>
    """
    global MAINTENANCE_MODE
    args = message.text.split(maxsplit=1)

    if len(args) > 1:
        if args[1].lower() == 'on':
            MAINTENANCE_MODE = True
            await message.reply_text("- `Maintenance mode enabled.`", parse_mode=ParseMode.MARKDOWN)
        elif args[1].lower() == 'off':
            MAINTENANCE_MODE = False
            await message.reply_text("- `Maintenance mode disabled.`", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("❗**Usage:** `/maintenance <on|off>`", parse_mode=ParseMode.MARKDOWN)
    else:
        status = "on" if MAINTENANCE_MODE else "off"
        await message.reply_text(f"- `Maintenance mode is currently {status}.`", parse_mode=ParseMode.MARKDOWN)

@Client.on_message(filters.command & filters.private & ~CustomFilters.owner)
async def maintenance_handler(client: Client, message: Message):
    if MAINTENANCE_MODE:
        await message.reply_text("- `The bot is currently under maintenance. Please try again later.`", parse_mode=ParseMode.MARKDOWN)
        message.stop_propagation()
