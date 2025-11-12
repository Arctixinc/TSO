from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
import psutil
import platform
import sys

@Client.on_message(filters.command('sysinfo') & filters.private & CustomFilters.owner)
async def sysinfo_command(client: Client, message: Message):
    """
    Displays detailed system information.
    """
    try:
        status_msg = await message.reply_text("- `Gathering system information...`", parse_mode=ParseMode.MARKDOWN)

        cpu_percent = psutil.cpu_percent(interval=1)
        ram_percent = psutil.virtual_memory().percent
        disk_usage = psutil.disk_usage('/')

        sys_info = (
            f"**- System Information**\n\n"
            f"- **CPU Usage:** `{cpu_percent}%`\n"
            f"- **RAM Usage:** `{ram_percent}%`\n"
            f"- **Disk Usage:** `{disk_usage.percent}%`\n"
            f"- **Total Disk:** `{disk_usage.total // (1024**3)} GB`\n"
            f"- **Used Disk:** `{disk_usage.used // (1024**3)} GB`\n"
            f"- **Free Disk:** `{disk_usage.free // (1024**3)} GB`\n\n"
            f"**- Software Information**\n\n"
            f"- **Python Version:** `{sys.version}`\n"
            f"- **Operating System:** `{platform.system()} {platform.release()}`\n"
            f"- **Architecture:** `{platform.machine()}`"
        )

        await status_msg.edit_text(sys_info, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in sysinfo command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
