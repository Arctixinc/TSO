from pyrogram import filters, Client
from pyrogram.types import Message, BotCommand
from Backend.reloader import PluginReloader
from Backend.loader import Loader
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.pyro import setup_bot_commands
from Backend import db
import sys

# Define Commands for dynamic registration
COMMANDS = [
    BotCommand("reload", "🔄 Hot reload plugins")
]

@Client.on_message(filters.command("reload") & CustomFilters.owner)
async def reload_bot(client: Client, message: Message):
    status_msg = await message.reply_text("🔄 **Checking for updates...**")

    reloader: PluginReloader = getattr(client, "reloader", None)
    if not reloader:
        await status_msg.edit("❌ Reloader not initialized.")
        return

    # 1. Git Pull
    success, output = await client.loop.run_in_executor(None, reloader.git_pull)

    log_text = f"**Git Pull:**\n`{output[:3000]}`" # Truncate if too long

    if not success:
        await status_msg.edit(f"❌ **Git Pull Failed!**\n\n{log_text}")
        return

    await status_msg.edit(f"🔄 **Updates found.**\n{log_text}\n\n♻️ **Reloading modules...**")

    # 2. Reload Modules
    try:
        reloaded, errors = await client.loop.run_in_executor(None, reloader.reload_changed_modules)
    except Exception as e:
        await status_msg.edit(f"❌ **Critical Error during reload:**\n`{e}`")
        return

    # 3. Refresh Bot Commands (Dynamic Update)
    try:
        await setup_bot_commands(client)
    except Exception as e:
        errors.append(f"Command update failed: {e}")

    # 4. Report Results
    result_text = f"✅ **Reload Complete**\n\n"

    if reloaded:
        result_text += f"📦 **Reloaded Modules:**\n" + "\n".join([f"`{m}`" for m in reloaded]) + "\n\n"
    else:
        result_text += "📦 No modules changed.\n\n"

    if errors:
        result_text += f"⚠️ **Errors:**\n" + "\n".join([f"`{e}`" for e in errors])

    await status_msg.edit(result_text)
