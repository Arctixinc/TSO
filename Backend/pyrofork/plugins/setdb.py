from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from Backend import db
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.db_manager import load_db_list, add_db_to_list, remove_db_from_list
import asyncio

@Client.on_message(filters.command("setdb") & CustomFilters.owner)
async def set_db_command(client, message):
    await show_setdb_menu(client, message.chat.id)

async def show_setdb_menu(client, chat_id, message_id=None):
    current_db = db.db_name
    stats = await db.get_database_stats()

    # Calculate stats for current DB (approximate, since stats are per storage shard)
    # Actually, stats list contains all storage shards for the *current* connection.
    total_movies = sum(s.get("movie_count", 0) for s in stats)
    total_tv = sum(s.get("tv_count", 0) for s in stats)
    total_size = sum(s.get("storageSize", 0) for s in stats) / (1024*1024) # MB

    text = (
        f"🗄 **Database Manager**\n\n"
        f"📌 **Current DB:** `{current_db}`\n"
        f"🎬 **Movies:** `{total_movies}`\n"
        f"📺 **TV Shows:** `{total_tv}`\n"
        f"💾 **Size:** `{total_size:.2f} MB`"
    )

    buttons = [
        [InlineKeyboardButton("🔄 Switch DB", callback_data="db_switch_menu")],
        [InlineKeyboardButton("➕ Add DB", callback_data="db_add"), InlineKeyboardButton("➖ Remove DB", callback_data="db_remove_menu")],
        [InlineKeyboardButton("❌ Close", callback_data="db_close")]
    ]

    if message_id:
        await client.edit_message_text(chat_id, message_id, text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await client.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r"^db_"))
async def db_callback(client, query: CallbackQuery):
    data = query.data
    chat_id = query.message.chat.id

    if data == "db_close":
        await query.message.delete()

    elif data == "db_menu":
        await show_setdb_menu(client, chat_id, query.message.id)

    elif data == "db_switch_menu":
        dbs = load_db_list()
        buttons = []
        for d in dbs:
            prefix = "✅ " if d == db.db_name else ""
            buttons.append([InlineKeyboardButton(f"{prefix}{d}", callback_data=f"db_switch|{d}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="db_menu")])

        await query.message.edit_text("🔄 **Select Database to Switch:**", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("db_switch|"):
        target_db = data.split("|")[1]
        if target_db == db.db_name:
            return await query.answer("⚠️ Already on this DB.", show_alert=True)

        await query.answer("🔄 Switching...", show_alert=False)
        try:
            # Persist
            with open("dbname.txt", "w") as f:
                f.write(target_db)

            await db.switch_db(target_db)
            await show_setdb_menu(client, chat_id, query.message.id)
            await client.send_message(chat_id, f"✅ Switched to `{target_db}`")
        except Exception as e:
            await query.message.reply(f"❌ Error: {e}")

    elif data == "db_add":
        await query.answer()
        prompt = await client.ask(
            chat_id=chat_id,
            text="✍️ **Send the new Database Name:**\n(No spaces, e.g., `my_new_db`)",
            filters=filters.text,
            timeout=60
        )
        new_name = prompt.text.strip()
        await prompt.delete()

        if " " in new_name:
            return await query.message.reply("❌ Invalid name. No spaces allowed.")

        if add_db_to_list(new_name):
            await query.message.reply(f"✅ Added `{new_name}` to list.")
            await show_setdb_menu(client, chat_id, query.message.id)
        else:
            await query.message.reply("⚠️ Database already in list.")

    elif data == "db_remove_menu":
        dbs = load_db_list()
        buttons = []
        for d in dbs:
            if d == db.db_name:
                continue # Cannot remove active
            buttons.append([InlineKeyboardButton(f"🗑 {d}", callback_data=f"db_remove|{d}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="db_menu")])

        await query.message.edit_text("➖ **Select Database to Remove from List:**", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("db_remove|"):
        target_db = data.split("|")[1]
        if remove_db_from_list(target_db):
            await query.answer(f"Removed {target_db}")
            # Refresh list
            dbs = load_db_list()
            buttons = []
            for d in dbs:
                if d == db.db_name:
                    continue
                buttons.append([InlineKeyboardButton(f"🗑 {d}", callback_data=f"db_remove|{d}")])
            buttons.append([InlineKeyboardButton("🔙 Back", callback_data="db_menu")])
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
        else:
            await query.answer("Failed to remove.", show_alert=True)
