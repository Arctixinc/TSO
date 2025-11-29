from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import psutil
import time
import platform
from datetime import timedelta

def get_readable_time(seconds):
    return str(timedelta(seconds=int(seconds)))

def get_size(bytes, suffix="B"):
    factor = 1024
    for unit in ["", "K", "M", "G", "T", "P"]:
        if bytes < factor:
            return f"{bytes:.2f}{unit}{suffix}"
        bytes /= factor

@Client.on_message(filters.command(['stats', 'sysinfo']) & filters.private & CustomFilters.owner)
async def stats_command(client: Client, message: Message):
    """
    Shows advanced system and database statistics.
    """
    try:
        status_msg = await message.reply_text("📊 Gathering system intel...", parse_mode=ParseMode.MARKDOWN)

        # Database Stats
        total_movies = 0
        total_tv_shows = 0
        total_storage_dbs = len(db.dbs) - 1

        db_details = ""
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            movies = await db.dbs[db_key]["movie"].count_documents({})
            tv = await db.dbs[db_key]["tv"].count_documents({})
            total_movies += movies
            total_tv_shows += tv
            db_details += f"  • **DB {db_index}:** {movies} Movies, {tv} Shows\n"

        # System Stats
        cpu_freq = psutil.cpu_freq()
        cpu_usage = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage('/')
        boot_time = psutil.boot_time()
        uptime = get_readable_time(time.time() - boot_time)

        # Network
        net_io = psutil.net_io_counters()

        stats_text = (
            f"**💻 System Information**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **OS:** `{platform.system()} {platform.release()}`\n"
            f"⏱ **Uptime:** `{uptime}`\n"
            f"🧠 **CPU:** `{cpu_usage}%` ({cpu_freq.current:.0f}MHz)\n"
            f"💾 **RAM:** `{get_size(mem.used)} / {get_size(mem.total)}` ({mem.percent}%)\n"
            f"💿 **Disk:** `{get_size(disk.used)} / {get_size(disk.total)}` ({disk.percent}%)\n"
            f"🌐 **Upload:** `{get_size(net_io.bytes_sent)}` | **Download:** `{get_size(net_io.bytes_recv)}`\n\n"

            f"**📊 Database Statistics**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎬 **Total Movies:** `{total_movies}`\n"
            f"📺 **Total TV Shows:** `{total_tv_shows}`\n"
            f"{db_details}"
        )

        await status_msg.edit_text(stats_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in stats command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
