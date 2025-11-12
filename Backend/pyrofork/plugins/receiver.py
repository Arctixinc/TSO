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

CONCURRENT_TASKS = 4
file_queue = Queue()

async def process_file_worker():
    while True:
        message = await file_queue.get()
        try:
            await handle_file(message)
        except Exception as e:
            LOGGER.error(f"Error processing message {message.id}: {e}")
        finally:
            file_queue.task_done()

async def handle_file(message: Message):
    if not (message.video or (message.document and message.document.mime_type.startswith("video/"))):
        return

    file = message.video or message.document
    title = message.caption or file.file_name
    channel = str(message.chat.id).replace("-100", "")

    if await db.get_media(channel=int(channel), msg_id=message.id):
        LOGGER.info(f"Skipping edit — already processed: {title} ({message.id})")
        return

    metadata_info = await metadata(clean_filename(title), int(channel), message.id)
    if not metadata_info:
        LOGGER.warning(f"Metadata failed for file: {title} (ID: {message.id})")
        return

    size = get_readable_file_size(file.file_size)
    clean_title = remove_urls(title)
    if not clean_title.endswith(('.mkv', '.mp4')):
        clean_title += '.mkv'

    if await db.insert_media(metadata_info, channel=int(channel), msg_id=message.id, size=size, name=clean_title):
        LOGGER.info(f"{metadata_info['media_type']} added: {clean_title}")
    else:
        LOGGER.warning(f"Failed to add media: {clean_title}")

@Client.on_edited_message(filters.channel & (filters.document | filters.video))
@Client.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(client: Client, message: Message):
    if str(message.chat.id) in Telegram.AUTH_CHANNEL:
        try:
            await file_queue.put(message)
        except FloodWait as e:
            LOGGER.info(f"Sleeping for {e.value}s due to FloodWait.")
            await asleep(e.value)
            await message.reply_text(f"Got Floodwait of {e.value}s")
    else:
        LOGGER.warning(f"Received message from unauthorized channel: {message.chat.id}")

for _ in range(CONCURRENT_TASKS):
    create_task(process_file_worker())
