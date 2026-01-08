from pyrogram.file_id import FileId
from typing import Optional
from Backend.logger import LOGGER
from Backend import __version__, now, timezone
from Backend.config import Telegram
from Backend.helper.exceptions import FIleNotFound
from aiofiles import open as aiopen
from aiofiles.os import path as aiopath, remove as aioremove
from pyrogram import Client
from Backend.pyrofork.bot import StreamBot
from datetime import datetime
from pyrogram.types import BotCommand
from pyrogram import enums
import subprocess
import re
import pytz


def is_media(message):
    return next((getattr(message, attr) for attr in ["document", "photo", "video", "audio", "voice", "video_note", "sticker", "animation"] if getattr(message, attr)), None)


async def get_file_ids(client: Client, chat_id: int, message_id: int) -> Optional[FileId]:
    try:
        message = await client.get_messages(chat_id, message_id)
        if message.empty:
            raise FIleNotFound("Message not found or empty")
        
        if media := is_media(message):
            file_id_obj = FileId.decode(media.file_id)
            file_unique_id = media.file_unique_id
            
            setattr(file_id_obj, 'file_name', getattr(media, 'file_name', ''))
            setattr(file_id_obj, 'file_size', getattr(media, 'file_size', 0))
            setattr(file_id_obj, 'mime_type', getattr(media, 'mime_type', ''))
            setattr(file_id_obj, 'unique_id', file_unique_id)
            
            return file_id_obj
        else:
            raise FIleNotFound("No supported media found in message")
    except Exception as e:
        LOGGER.error(f"Error getting file IDs: {e}")
        raise
        


def get_readable_file_size(size_in_bytes):
    size_in_bytes = int(size_in_bytes) if str(size_in_bytes).isdigit() else 0
    if not size_in_bytes:
        return '0B'
    
    index, SIZE_UNITS = 0, ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1
    
    return f'{size_in_bytes:.2f}{SIZE_UNITS[index]}' if index > 0 else f'{size_in_bytes:.0f}B'


def clean_filename(filename):
    if not filename:
        return "unknown_file"
    
    pattern = r'_@[A-Za-z]+_|@[A-Za-z]+_|[\[\]\s@]*@[^.\s\[\]]+[\]\[\s@]*'
    cleaned_filename = re.sub(pattern, '', filename)
    
    cleaned_filename = re.sub(
        r'(?<=\W)(org|AMZN|DDP|DD|NF|AAC|TVDL|5\.1|2\.1|2\.0|7\.0|7\.1|5\.0|~|\b\w+kbps\b)(?=\W)', 
        ' ', cleaned_filename, flags=re.IGNORECASE
    )
    
    cleaned_filename = re.sub(r'\s+', ' ', cleaned_filename).strip().replace(' .', '.')
    
    return cleaned_filename if cleaned_filename else "unknown_file"


def get_readable_time(seconds: int) -> str:
    count = 0
    readable_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", " days"]
    
    while count < 4:
        count += 1
        if count < 3:
            remainder, result = divmod(seconds, 60)
        else:
            remainder, result = divmod(seconds, 24)
        
        if seconds == 0 and remainder == 0:
            break
        
        time_list.append(int(result))
        seconds = int(remainder)
    
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    
    if len(time_list) == 4:
        readable_time += time_list.pop() + ", "
    
    time_list.reverse()
    readable_time += ": ".join(time_list)
    
    return readable_time


def extract_tmdb_id(url):
    # Match IMDb URLs
    imdb_match = re.search(r'/title/(tt\d+)', url)
    if imdb_match:
        return imdb_match.group(1)
    
    return None


def remove_urls(text):
    if not text:
        return ""
    
    url_pattern = r'\b(?:https?|ftp):\/\/[^\s/$.?#].[^\s]*'
    text_without_urls = re.sub(url_pattern, '', text)
    cleaned_text = re.sub(r'\s+', ' ', text_without_urls).strip()
    
    return cleaned_text





async def restart_notification():
    """
    Sends a styled restart confirmation message including
    last Git commit details and IST-formatted commit time.
    """

    chat_id = 0
    msg_id = 0

    try:
        if await aiopath.exists(".restartmsg"):
            async with aiopen(".restartmsg", "r") as f:
                data = await f.readlines()
                chat_id, msg_id = map(int, data)

            # ========== GET LATEST GIT COMMIT INFO ==========
            try:
                commit_hash = (
                    subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
                    .decode()
                    .strip()
                )

                commit_message = (
                    subprocess.check_output(
                        ["git", "log", "-1", "--pretty=%B"]
                    )
                    .decode()
                    .strip()
                )

                commit_time_raw = (
                    subprocess.check_output(
                        ["git", "log", "-1", "--pretty=%ct"]
                    )
                    .decode()
                    .strip()
                )
                commit_time_utc = datetime.utcfromtimestamp(int(commit_time_raw))

                # Convert commit time to IST
                ist_tz = pytz.timezone("Asia/Kolkata")
                commit_time_ist = commit_time_utc.replace(tzinfo=pytz.utc).astimezone(ist_tz)
                commit_time_str = commit_time_ist.strftime("%d/%m/%y • %I:%M:%S %p")

            except Exception as e:
                LOGGER.error(f"Git info error: {e}")
                commit_hash = "N/A"
                commit_message = "N/A"
                commit_time_str = "N/A"

            # Prepare GitHub repo link
            repo_parts = Telegram.UPSTREAM_REPO.split("/")
            upstream_repo = f"https://github.com/{repo_parts[-2]}/{repo_parts[-1]}"

            # ========== BUILD STYLED MESSAGE ==========
            message_text = (
                "<b>♻️ Restart Successful!</b>\n\n"
                f"📅 <b>Date:</b> {now.strftime('%d/%m/%y')}\n"
                f"⏰ <b>Time:</b> {now.strftime('%I:%M:%S %p')}\n"
                f"🌍 <b>Time Zone:</b> {timezone.zone}\n\n"

                "📌 <b>Last Commit Details</b>\n"
                f"🔹 <b>Commit ID:</b> <code>{commit_hash}</code>\n"
                f"🔹 <b>Message:</b> {commit_message}\n"
                f"🔹 <b>Commit Time (IST):</b> {commit_time_str}\n\n"

                f"📂 <b>Repo:</b> {upstream_repo}\n"
                f"🌿 <b>Branch:</b> {Telegram.UPSTREAM_BRANCH}\n"
                f"🧩 <b>Version:</b> {__version__}"
            )

            # Send the styled message
            try:
                await StreamBot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=message_text,
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception as e:
                LOGGER.error(f"Failed to edit restart message: {e}")

            await aioremove(".restartmsg")

    except Exception as e:
        LOGGER.error(f"Error in restart_notification: {e}")


# Bot commands
commands = [
    BotCommand("start",       "🚀 Start the bot"),
    BotCommand("health",      "❤️ Check bot health"),
    BotCommand("mode",        "⚙️ Manage configuration"),
    BotCommand("set",         "🎬 Add IMDb metadata"),
    BotCommand("log",         "📄 Get log file"),
    BotCommand("info",        "ℹ️ Get media info"),
    BotCommand("find",        "🔎 Search media in database"),
    BotCommand("latest",      "🆕 Show latest media"),
    BotCommand("random",      "🎲 Get random media"),
    BotCommand("smartclean",  "🧹 Smart scanning ultra speed"),
    BotCommand("scan",        "🔍 Scan channels for videos"),
    BotCommand("stats",       "📊 View database stats"),
    BotCommand("cleanup",     "🧹 Remove broken links"),
    BotCommand("dbcleanup",   "🗑️ Delete duplicate entries"),
    BotCommand("fixmetadata", "🔧 Fix metadata"),
    BotCommand("users",       "👥 Manage users"),
    BotCommand("eval",        "🧠 Run Python code"),
    BotCommand("shell",       "💻 Run shell commands"),
    BotCommand("scrapper",    "👾 Scrape Media"),
    BotCommand("restart",     "♻️ Restart the bot"),
]

async def setup_bot_commands(bot: Client):
    try:
        current_commands = await bot.get_bot_commands()
        if current_commands:
            LOGGER.info(f"Found {len(current_commands)} existing commands. Deleting them...")
            await bot.set_bot_commands([])
        
        await bot.set_bot_commands(commands)
        LOGGER.info("Bot commands updated successfully.")
    except Exception as e:
        LOGGER.error(f"Error setting up bot commands: {e}")

