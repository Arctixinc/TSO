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
    # Initial status message
    status_msg = await message.reply_text("🔄 **Processing Hot Reload...**")

    reloader: PluginReloader = getattr(client, "reloader", None)
    if not reloader:
        await status_msg.edit("❌ **Error:** Reloader not initialized.")
        return

    # --- Step 1: Git Pull ---
    # We do not edit the message here to avoid flickering, just gather the result.
    git_success, git_output = await client.loop.run_in_executor(None, reloader.git_pull)

    # Truncate git output if it's too long/noisy
    clean_git_output = git_output.strip()
    if len(clean_git_output) > 1000:
        clean_git_output = clean_git_output[:1000] + "... (truncated)"
    if not clean_git_output:
        clean_git_output = "No output."

    if not git_success:
        # If git fails, we must report it immediately and stop.
        await status_msg.edit(
            f"❌ **Git Pull Failed**\n\n"
            f"```\n{clean_git_output}\n```"
        )
        return

    # --- Step 2: Reload Modules ---
    try:
        reloaded, errors = await client.loop.run_in_executor(None, reloader.reload_changed_modules)
    except Exception as e:
        await status_msg.edit(f"❌ **Critical Error during reload:**\n`{e}`")
        return

    # --- Step 3: Refresh Bot Commands ---
    cmd_status = "✅ Updated"
    try:
        await setup_bot_commands(client)
    except Exception as e:
        cmd_status = f"⚠️ Failed: {e}"
        errors.append(f"Command update failed: {e}")

    # --- Step 4: Final Report (The "Best UI Message") ---
    # Construct a clean, unified summary

    header = "✅ **Reload Complete**" if not errors else "⚠️ **Reload Completed with Errors**"

    # Git Section
    git_section = f"**Git Status:**\n`{clean_git_output}`"

    # Modules Section
    if reloaded:
        modules_list = "\n".join([f"• `{m}`" for m in reloaded])
        module_section = f"**Reloaded Modules:**\n{modules_list}"
    else:
        module_section = "**Reloaded Modules:**\n• None (No changes detected)"

    # Errors Section
    error_section = ""
    if errors:
        error_list = "\n".join([f"• `{e}`" for e in errors])
        error_section = f"\n\n**⚠️ Errors:**\n{error_list}"

    final_text = (
        f"{header}\n\n"
        f"{git_section}\n\n"
        f"{module_section}\n\n"
        f"**Commands:** {cmd_status}"
        f"{error_section}"
    )

    await status_msg.edit(final_text)
