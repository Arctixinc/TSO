import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from Backend.config import Telegram
from Backend.helper.custom_filter import CustomFilters
from Backend import db
from Backend.helper.metadata import metadata
from Backend.helper.pyro import clean_filename, get_readable_file_size
from Backend.helper.defaulter_helper import DefaulterManager
from Backend.logger import LOGGER

# Lock to prevent multiple simultaneous scans
SCAN_LOCK = asyncio.Lock()

@Client.on_message(filters.command("scanall") & CustomFilters.owner)
async def scan_all_files(client, message):
    if SCAN_LOCK.locked():
        return await message.reply("⚠️ A scan is already in progress.")

    args = message.command
    # Default values
    target_channel = None
    if Telegram.AUTH_CHANNEL and len(Telegram.AUTH_CHANNEL) >= 2:
        try:
            target_channel = int(Telegram.AUTH_CHANNEL[1])
        except ValueError:
             target_channel = None

    start_id = 1
    end_id = 0 # 0 means auto-detect / infinite

    # Help Message / Validation
    help_text = (
        "ℹ️ **ScanAll Help**\n\n"
        "Usage: `/scanall [channel_id] [start_id] [end_id]`\n\n"
        "**Examples:**\n"
        "1. Scan configured Auth Channel from beginning:\n"
        "`/scanall`\n\n"
        "2. Scan specific channel from ID 100:\n"
        "`/scanall -100123456789 100`\n\n"
        "3. Scan specific channel range (100-500):\n"
        "`/scanall -100123456789 100 500`\n\n"
        "**Note:** Ensure the bot is added to the target channel."
    )

    # Parse Arguments: /scanall [channel_id] [start_id] [end_id]
    if len(args) > 1:
        try:
            target_channel = int(args[1])
        except ValueError:
             return await message.reply(f"❌ Invalid Channel ID format.\n\n{help_text}")

    if len(args) > 2:
        try:
            start_id = int(args[2])
        except ValueError:
            pass

    if len(args) > 3:
        try:
            end_id = int(args[3])
        except ValueError:
            pass

    if not target_channel:
        return await message.reply(f"❌ Target Channel not configured or provided.\n\n{help_text}")

    status_msg = await message.reply(f"🚀 Initializing scan for `{target_channel}`...")

    async with SCAN_LOCK:
        try:
            # Resolve chat title for logging
            try:
                chat = await client.get_chat(target_channel)
                chat_title = chat.title or str(target_channel)
            except Exception:
                chat_title = str(target_channel)

            await status_msg.edit(
                f"🚀 **Scanning Channel:** `{chat_title}` (`{target_channel}`)\n"
                f"🔢 **Start ID:** `{start_id}`\n"
                f"🏁 **End ID:** `{'Auto' if end_id == 0 else end_id}`\n\n"
                "Starting batch processing..."
            )

            processed = 0
            added = 0
            failed = 0
            current_id = start_id

            while True:
                # Stop condition if end_id is set
                if end_id > 0 and current_id > end_id:
                    break

                # Determine batch range
                batch_limit = min(current_id + 200, end_id + 1) if end_id > 0 else current_id + 200
                batch_ids = list(range(current_id, batch_limit))

                if not batch_ids:
                    break

                messages = []
                try:
                    msgs = await client.get_messages(target_channel, batch_ids)
                    # Filter valid messages (Pyrogram returns None or empty for missing IDs)
                    messages = [m for m in msgs if m and not m.empty]
                except FloodWait as e:
                    LOGGER.warning(f"[SCAN] FloodWait {e.value}s. Sleeping...")
                    await asyncio.sleep(e.value + 5)
                    continue # Retry same batch
                except Exception as e:
                    LOGGER.warning(f"[SCAN] Error fetching batch {batch_ids[0]}-{batch_ids[-1]}: {e}")
                    # If we fail to fetch a batch (e.g. channel not found, access denied), abort
                    if "CHANNEL_INVALID" in str(e) or "PEER_ID_INVALID" in str(e):
                        await status_msg.edit(f"❌ Error: Cannot access channel `{target_channel}`.")
                        return
                    # Otherwise skip batch
                    current_id += 200
                    continue

                # Auto-stop condition:
                # If we are in auto-mode (end_id=0) and we get a completely empty batch,
                # we assume we reached the end of the channel.
                # Note: This assumes no gaps > 200 messages.
                if end_id == 0 and not messages:
                    # Double check: try one more batch ahead to be sure?
                    # For now, let's assume empty batch = done.
                    # Or maybe checking current_id vs some sanity limit?
                    LOGGER.info(f"[SCAN] Empty batch at {current_id}. Stopping auto-scan.")
                    break

                for msg in messages:
                    if not (msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"))):
                        continue

                    processed += 1
                    file = msg.video or msg.document
                    file_name = file.file_name or msg.caption or "Unknown"
                    cleaned_name = clean_filename(file_name)

                    # Fix: Convert size to readable string as expected by Pydantic model
                    file_size_str = get_readable_file_size(getattr(file, "file_size", 0))

                    # Try to get metadata
                    meta = await metadata(cleaned_name, target_channel, msg.id)

                    if meta:
                        # Insert into DB
                        await db.insert_media(
                            metadata_info=meta,
                            channel=target_channel,
                            msg_id=msg.id,
                            size=file_size_str,
                            name=file_name
                        )
                        added += 1
                    else:
                        # Add to Defaulters
                        await DefaulterManager.add_defaulter(
                            message_id=msg.id,
                            file_name=file_name,
                            file_unique_id=file.file_unique_id,
                            chat_id=target_channel
                        )
                        failed += 1

                        # Notify User
                        try:
                            text = (
                                f"⚠️ **Unmatched File Detected**\n\n"
                                f"📂 **File:** `{file_name}`\n"
                                f"🔗 **Link:** [Click Here]({msg.link})"
                            )
                            btn = InlineKeyboardMarkup([[
                                InlineKeyboardButton("🔧 Manual Match", callback_data=f"def_man|{file.file_unique_id}")
                            ]])
                            await message.reply(text, reply_markup=btn, quote=False, disable_web_page_preview=True)
                        except Exception as e:
                            LOGGER.error(f"Failed to send notification: {e}")

                # Update Status every batch
                if processed % 50 == 0: # Reduce edits
                    try:
                        await status_msg.edit(
                            f"🔄 **Scanning...**\n\n"
                            f"🔢 **Current ID:** `{batch_ids[-1]}`\n"
                            f"📂 **Processed:** `{processed}`\n"
                            f"✅ **Added:** `{added}`\n"
                            f"⚠️ **Unmatched:** `{failed}`"
                        )
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except Exception:
                        pass

                current_id += 200
                await asyncio.sleep(2) # Rate limit protection

        except Exception as e:
            LOGGER.error(f"Scan failed: {e}")
            await message.reply(f"❌ Scan interrupted: {e}")

        await status_msg.edit(
            f"✅ **Scan Completed**\n\n"
            f"🔢 **Final ID:** `{current_id}`\n"
            f"📂 **Total Processed:** `{processed}`\n"
            f"✅ **Added/Updated:** `{added}`\n"
            f"⚠️ **Unmatched:** `{failed}`\n\n"
            f"Use /defaulter to handle unmatched files."
        )
