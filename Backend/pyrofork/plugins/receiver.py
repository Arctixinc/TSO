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

async def worker(worker_id):
    LOGGER.info(f"Worker {worker_id} started")
    while True:
        try:
            metadata_info, channel, msg_id, size, title = await file_queue.get()

            async with db_lock:
                updated_id = await db.insert_media(metadata_info, channel=channel, msg_id=msg_id, size=size, name=title)

            if updated_id:
                LOGGER.info(f"Worker {worker_id}: {metadata_info['media_type']} updated with ID: {updated_id}")
            else:
                LOGGER.info(f"Worker {worker_id}: Update failed for {title} due to validation errors.")

            file_queue.task_done()
        except Exception as e:
            LOGGER.error(f"Worker {worker_id}: An error occurred: {e}", exc_info=True)
            # Optional: Add a small delay before continuing to prevent rapid-fire errors
            await asleep(1)

def start_workers():
    for i in range(Telegram.MAX_WORKERS):
        create_task(worker(i + 1))

start_workers()

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
                
                # ✅ Check if this media already exists in DB before processing
                existing = await db.get_media(channel=int(channel), msg_id=msg_id)
                if existing:
                    LOGGER.info(f"Skipping edit — already processed: {title} ({msg_id})")
                    return

                metadata_info = await metadata(clean_filename(title), int(channel), msg_id)
                if metadata_info is None:
                    LOGGER.warning(f"Metadata failed for file: {title} (ID: {msg_id})")
                    return

                # ✅ If combined — just log info, not skip
                if metadata_info.get("combined_note"):
                    LOGGER.info(f"Detected combined file: {title} ({metadata_info['combined_note']})")


                title = remove_urls(title)
                if not title.endswith(('.mkv', '.mp4')):
                    title += '.mkv'

                if Backend.USE_DEFAULT_ID:
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
        
        
