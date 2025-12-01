from asyncio import create_task, sleep as asleep, Queue, Lock
import Backend
from Backend.helper.task_manager import edit_message
from Backend.logger import LOGGER
from Backend import db
from Backend.config import Telegram
from Backend.helper.pyro import clean_filename, get_readable_file_size, remove_urls
from Backend.helper.metadata import metadata
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode


file_queue = Queue()
db_lock = Lock()

async def process_file():
    while True:
        metadata_info, channel, msg_id, size, title = await file_queue.get()
        async with db_lock:
            updated_id = await db.insert_media(metadata_info, channel=channel, msg_id=msg_id, size=size, name=title)
            if updated_id:
                LOGGER.info(f"{metadata_info['media_type']} updated with ID: {updated_id}")
            else:
                LOGGER.info("Update failed due to validation errors.")
        file_queue.task_done()

for _ in range(1):
    create_task(process_file())

@Client.on_edited_message(filters.channel & (filters.document | filters.video))
@Client.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(client: Client, message: Message):
    if str(message.chat.id) in Telegram.AUTH_CHANNEL:
        try:
            if message.video or (message.document and message.document.mime_type.startswith("video/")):
                file = message.video or message.document
                title = message.caption or file.file_name
                msg_id = message.id
                size = get_readable_file_size(file.file_size)
                channel = str(message.chat.id).replace("-100", "")
                
                # Check if this media already exists in DB before processing
                # Also checks if we have already processed this message ID
                existing = await db.get_media(channel=int(channel), msg_id=msg_id)
                if existing:
                    LOGGER.info(f"Skipping edit — already processed: {title} ({msg_id})")
                    return

                # Prevent loop: If the caption already contains the USE_DEFAULT_ID, don't re-edit or re-process heavily
                if Backend.USE_DEFAULT_ID and message.caption and Backend.USE_DEFAULT_ID in message.caption:
                    LOGGER.info(f"Skipping file processing - DEFAULT_ID already in caption: {msg_id}")
                    # However, if it's not in DB yet, we should process it.
                    # But the 'existing' check above covers DB presence.
                    # This check prevents the bot from reacting to its own edit.
                    # We continue to metadata parsing only if we haven't processed it yet.
                    pass

                metadata_info = await metadata(clean_filename(title), int(channel), msg_id)
                if metadata_info is None:
                    LOGGER.warning(f"Metadata failed for file: {title} (ID: {msg_id})")
                    return

                if metadata_info.get("combined_note"):
                    LOGGER.info(f"Detected combined file: {title} ({metadata_info['combined_note']})")


                title = remove_urls(title)
                if not title.endswith(('.mkv', '.mp4')):
                    title += '.mkv'

                if Backend.USE_DEFAULT_ID:
                    # Check again to be safe before editing
                    if not message.caption or Backend.USE_DEFAULT_ID not in message.caption:
                        new_caption = (message.caption + "\n\n" + Backend.USE_DEFAULT_ID) if message.caption else Backend.USE_DEFAULT_ID
                        create_task(edit_message(
                            chat_id=message.chat.id,
                            msg_id=message.id,
                            new_caption=new_caption
                        ))

                await file_queue.put((metadata_info, int(channel), msg_id, size, title))
            else:
                await message.reply_text("> Not supported")
        except FloodWait as e:
            LOGGER.info(f"Sleeping for {str(e.value)}s")
            await asleep(e.value)
            await message.reply_text(
                text=f"Got Floodwait of {str(e.value)}s",
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        await message.reply_text("> Channel is not in AUTH_CHANNEL")
        
        
