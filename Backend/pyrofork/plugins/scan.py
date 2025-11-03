from asyncio import sleep as asleep
import time
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode

import Backend
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

        # --- Argument Parsing ---
        if len(args) == 1:
            start_id, end_id = 1, 100
        elif len(args) == 2:
            start_id, end_id = 1, int(args[1])
        elif len(args) == 3:
            if args[1].startswith('-'):
                specific_channel, start_id, end_id = args[1], 1, int(args[2])
            else:
                start_id, end_id = int(args[1]), int(args[2])
        elif len(args) >= 4 and args[1].startswith('-'):
            specific_channel, start_id, end_id = args[1], int(args[2]), int(args[3])
        else:
            await message.reply_text(
                "⚠️ Invalid usage!\n\n"
                "**Usage:**\n"
                "`/scan` - Scan messages 1-100 in all channels\n"
                "`/scan 500` - Scan messages 1-500 in all channels\n"
                "`/scan 100 500` - Scan messages 100-500 in all channels\n"
                "`/scan -1003261695898 1 3500` - Scan specific channel only",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # --- Range Validation ---
        if start_id < 1:
            return await message.reply_text("⚠️ Start ID must be at least 1")
        if end_id < start_id:
            return await message.reply_text("⚠️ End ID must be ≥ Start ID")
        if end_id - start_id > 10000:
            return await message.reply_text("⚠️ Range too large! Max 10,000 messages per scan.")

        # --- Channel Validation ---
        if specific_channel and specific_channel not in Telegram.AUTH_CHANNEL:
            await message.reply_text(
                f"⚠️ Channel `{specific_channel}` is not in AUTH_CHANNEL!\n\n"
                f"**Available channels:**\n" + "\n".join([f"`{ch}`" for ch in Telegram.AUTH_CHANNEL]),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        total_range = end_id - start_id + 1
        channels_to_scan = [specific_channel] if specific_channel else Telegram.AUTH_CHANNEL
        channel_info = f"specific channel `{specific_channel}`" if specific_channel else f"**{len(channels_to_scan)} channel(s)**"

        status_msg = await message.reply_text(
            f"🔍 Starting scan...\n📺 Channels: {channel_info}\n📊 Range: {start_id}-{end_id} ({total_range} messages)",
            parse_mode=ParseMode.MARKDOWN
        )

        global_start_time = time.time()
        total_processed = total_added = total_skipped = total_errors = 0

        # --- Channel Loop ---
        for channel_id in channels_to_scan:
            channel_processed = channel_added = channel_skipped = channel_errors = 0
            try:
                chat_id = int(channel_id)
                LOGGER.info(f"[SCAN] Starting channel: {chat_id}")

                msg_ids_to_scan = list(range(start_id, end_id + 1))
                messages_to_scan = []

                # Try to fetch messages safely
                try:
                    messages_to_scan = await client.get_messages(chat_id, msg_ids_to_scan)
                    if not isinstance(messages_to_scan, list):
                        messages_to_scan = [messages_to_scan]
                    messages_to_scan = [m for m in messages_to_scan if m is not None]
                except Exception as e:
                    LOGGER.warning(f"[SCAN] Skipping channel {chat_id}: {e}")
                    await status_msg.edit_text(
                        f"⚠️ Skipping channel `{chat_id}` — {e}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    continue

                last_update = time.time()
                progress_interval = 15  # seconds

                for msg in messages_to_scan:
                    try:
                        if not msg:
                            continue
                        if not (msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"))):
                            continue

                        file = msg.video or msg.document
                        title = msg.caption or file.file_name
                        msg_id = msg.id
                        size = get_readable_file_size(file.file_size)
                        channel = str(chat_id).replace("-100", "")

                        # Skip duplicates
                        existing = await check_existing_file(int(channel), msg_id)
                        if existing:
                            channel_skipped += 1
                            continue

                        metadata_info = await metadata(clean_filename(title), int(channel), msg_id)
                        if not metadata_info:
                            channel_errors += 1
                            continue

                        title = remove_urls(title)
                        if not title.endswith(('.mkv', '.mp4')):
                            title += '.mkv'

                        inserted = await db.insert_media(metadata_info, channel=int(channel), msg_id=msg_id, size=size, name=title)
                        if inserted:
                            channel_added += 1
                        else:
                            channel_errors += 1

                        channel_processed += 1

                        # --- Throttled progress update (3.A) ---
                        if time.time() - last_update > progress_interval:
                            last_update = time.time()
                            percent = int((channel_processed / total_range) * 100)
                            progress_bar = "█" * (percent // 10) + "░" * (10 - (percent // 10))
                            await status_msg.edit_text(
                                f"🔍 Scanning `{channel_id}`\n"
                                f"{progress_bar} {percent}%\n"
                                f"📊 Processed: {channel_processed}/{total_range}\n"
                                f"✅ Added: {channel_added} | ⏭️ Skipped: {channel_skipped} | ❌ Errors: {channel_errors}",
                                parse_mode=ParseMode.MARKDOWN
                            )

                        await asleep(0.3)

                    except FloodWait as e:
                        # --- Global FloodWait backoff (4.B) ---
                        delay = e.value + 5
                        LOGGER.warning(f"FloodWait {delay}s on channel {chat_id}")
                        await status_msg.edit_text(f"⏳ FloodWait {delay}s — Pausing...", parse_mode=ParseMode.MARKDOWN)
                        await asleep(delay)

                    except Exception as e:
                        LOGGER.error(f"Error processing msg {msg.id} in {chat_id}: {e}")
                        channel_errors += 1
                        continue

                # --- Per-channel summary (5.C) ---
                LOGGER.info(
                    f"[SCAN SUMMARY] Channel {chat_id}: "
                    f"Processed={channel_processed}, Added={channel_added}, Skipped={channel_skipped}, Errors={channel_errors}"
                )

                total_processed += channel_processed
                total_added += channel_added
                total_skipped += channel_skipped
                total_errors += channel_errors

            except Exception as e:
                LOGGER.error(f"Channel-level error {channel_id}: {e}")
                continue

        # --- Final summary ---
        duration = round(time.time() - global_start_time, 1)
        await status_msg.edit_text(
            f"✅ **Scan Complete!**\n\n"
            f"📺 Channels Scanned: {len(channels_to_scan)}\n"
            f"🕒 Time: {duration}s\n\n"
            f"📊 Total Processed: {total_processed}\n"
            f"✅ Added: {total_added}\n"
            f"⏭️ Skipped: {total_skipped}\n"
            f"❌ Errors: {total_errors}",
            parse_mode=ParseMode.MARKDOWN
        )

        LOGGER.info(
            f"[SCAN DONE] All channels complete — "
            f"Processed={total_processed}, Added={total_added}, Skipped={total_skipped}, Errors={total_errors}"
        )

    except ValueError:
        await message.reply_text("⚠️ Invalid limit. Example: `/scan 100`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        LOGGER.error(f"Scan command error: {e}")
        await message.reply_text(f"❌ Error: {e}", parse_mode=ParseMode.MARKDOWN)


async def check_existing_file(channel: int, msg_id: int) -> bool:
    try:
        from Backend.helper.encrypt import encode_string
        data = {"chat_id": channel, "msg_id": msg_id}
        encoded_id = await encode_string(data)

        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            movie = await db.dbs[db_key]["movie"].find_one({"telegram.id": encoded_id})
            if movie:
                return True
            tv = await db.dbs[db_key]["tv"].find_one({"seasons.episodes.telegram.id": encoded_id})
            if tv:
                return True
        return False
    except Exception as e:
        LOGGER.error(f"Error checking existing file: {e}")
        return False
