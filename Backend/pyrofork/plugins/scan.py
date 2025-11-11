from asyncio import sleep as asleep
import time
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, ChannelInvalid
from pyrogram.enums.parse_mode import ParseMode

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend.config import Telegram
from Backend.helper.pyro import clean_filename, get_readable_file_size, remove_urls
from Backend.helper.metadata import metadata
from Backend import db

@Client.on_message(filters.command('scan') & filters.private & CustomFilters.owner, group=10)
async def scan_channel(client: Client, message: Message):
    """
    Scans AUTH_CHANNEL for existing video files and adds them to the database.
    Usage:
        /scan [limit]
        /scan <start> <end>
        /scan <channel_id> <start> <end>
    """
    try:
        args = message.text.split()
        specific_channel = None

        if len(args) == 1:
            start_id, end_id = 1, 100
        elif len(args) == 2:
            start_id, end_id = 1, int(args[1])
        elif len(args) == 3:
            if args[1].startswith('-'):
                specific_channel, start_id, end_id = int(args[1]), 1, int(args[2])
            else:
                start_id, end_id = int(args[1]), int(args[2])
        elif len(args) >= 4 and args[1].startswith('-'):
            specific_channel, start_id, end_id = int(args[1]), int(args[2]), int(args[3])
        else:
            await message.reply_text("See /help for usage.", parse_mode=ParseMode.MARKDOWN)
            return

        if start_id < 1:
            return await message.reply_text("⚠️ Start ID must be at least 1")
        if end_id < start_id:
            return await message.reply_text("⚠️ End ID must be ≥ Start ID")
        if end_id - start_id > 10000:
            return await message.reply_text("⚠️ Range too large! Max 10,000 messages per scan.")

        channels_to_scan = [specific_channel] if specific_channel else [int(ch) for ch in Telegram.AUTH_CHANNEL]

        status_msg = await message.reply_text(
            f"🔍 Starting scan on {len(channels_to_scan)} channel(s) from message ID {start_id} to {end_id}...",
            parse_mode=ParseMode.MARKDOWN
        )

        global_start_time = time.time()
        total_added = total_skipped = total_errors = 0

        for channel_id in channels_to_scan:
            channel_added = channel_skipped = channel_errors = 0
            try:
                chat = await client.get_chat(channel_id)
                LOGGER.info(f"[SCAN] Starting channel: {chat.title} ({channel_id})")

                messages_to_process = []
                for i in range(start_id, end_id + 1, 200):
                    batch_ids = list(range(i, min(i + 200, end_id + 1)))
                    try:
                        messages = await client.get_messages(channel_id, batch_ids)
                        messages_to_process.extend([m for m in messages if m])
                    except FloodWait as e:
                        await asleep(e.value + 5)
                    except Exception as e:
                        LOGGER.warning(f"[SCAN] Error fetching batch for {channel_id}: {e}")

                for i, msg in enumerate(messages_to_process):
                    if not (msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"))):
                        continue

                    file = msg.video or msg.document
                    title = msg.caption or file.file_name

                    if await db.get_media(int(str(channel_id).replace("-100", "")), msg.id):
                        channel_skipped += 1
                        continue

                    metadata_info = await metadata(clean_filename(title), int(str(channel_id).replace("-100", "")), msg.id)
                    if not metadata_info:
                        channel_errors += 1
                        continue

                    inserted = await db.insert_media(metadata_info, channel=int(str(channel_id).replace("-100", "")), msg_id=msg.id, size=get_readable_file_size(file.file_size), name=remove_urls(title))
                    if inserted:
                        channel_added += 1
                    else:
                        channel_skipped += 1

                    if i % 20 == 0:
                        await status_msg.edit_text(
                            f"🔍 Scanning {chat.title}...\n"
                            f"📊 Progress: {i+1}/{len(messages_to_process)}\n"
                            f"✅ Added: {channel_added} | ⏭️ Skipped: {channel_skipped} | ❌ Errors: {channel_errors}",
                            parse_mode=ParseMode.MARKDOWN
                        )

            except (ChannelInvalid, ValueError):
                LOGGER.error(f"Channel {channel_id} is not valid.")
                total_errors += 1
                continue
            except Exception as e:
                LOGGER.error(f"Channel-level error for {channel_id}: {e}")
                total_errors += 1
                continue

            total_added += channel_added
            total_skipped += channel_skipped
            total_errors += channel_errors

        duration = round(time.time() - global_start_time, 1)
        await status_msg.edit_text(
            f"✅ **Scan Complete!**\n\n"
            f"🕒 Time: {duration}s\n"
            f"✅ Added: {total_added}\n"
            f"⏭️ Skipped: {total_skipped}\n"
            f"❌ Errors: {total_errors}",
            parse_mode=ParseMode.MARKDOWN
        )

    except ValueError:
        await message.reply_text("⚠️ Invalid number. Example: `/scan 100`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        LOGGER.error(f"Scan command error: {e}")
        await message.reply_text(f"❌ Error: {e}", parse_mode=ParseMode.MARKDOWN)
