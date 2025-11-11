from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend.config import Telegram
import asyncio

@Client.on_message(filters.command('broadcast') & filters.private & CustomFilters.owner)
async def broadcast_command(client: Client, message: Message):
    """
    Broadcasts a message to all authorized channels.
    Usage: /broadcast <message>
           /broadcast confirm
    """
    args = message.text.split(maxsplit=1)

    if len(args) > 1:
        if args[1].lower() == 'confirm':
            if 'broadcast_message' in client.owner_storage:
                status_msg = await message.reply_text("- `Broadcasting message...`", parse_mode=ParseMode.MARKDOWN)
                count = 0
                for channel_id in Telegram.AUTH_CHANNEL:
                    try:
                        await client.send_message(int(channel_id), client.owner_storage['broadcast_message'])
                        count += 1
                        await asyncio.sleep(1)
                    except Exception as e:
                        LOGGER.error(f"Error broadcasting to {channel_id}: {e}")

                await status_msg.edit_text(f"✅ Broadcasted message to {count} channel(s).", parse_mode=ParseMode.MARKDOWN)
                del client.owner_storage['broadcast_message']
            else:
                await message.reply_text("🤷 No message to broadcast. Run `/broadcast <message>` first.", parse_mode=ParseMode.MARKDOWN)
        else:
            client.owner_storage['broadcast_message'] = args[1]
            await message.reply_text(f"**- Confirm Broadcast**\n\n- Message:\n`{args[1]}`\n\nTo confirm, run `/broadcast confirm`.", parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply_text("❗**Usage:** `/broadcast <message>`", parse_mode=ParseMode.MARKDOWN)

# Add a simple in-memory storage for the owner
Client.owner_storage = {}
