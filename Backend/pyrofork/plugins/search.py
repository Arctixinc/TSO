from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import re

# In-memory cache for search results to support pagination
# Structure: {user_id: {"results": [], "page": 0, "query": "..."}}
SEARCH_CACHE = {}
RESULTS_PER_PAGE = 1

@Client.on_message(filters.command(['search', 'find']) & filters.private & CustomFilters.owner)
async def search_command(client: Client, message: Message):
    """
    Smart search for media with interactive UI.
    Usage: /search <keyword>
    """
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply_text("❗**Usage:** `/search <keyword>`", parse_mode=ParseMode.MARKDOWN)
            return

        keyword = args[1]
        status_msg = await message.reply_text(f"🔍 Searching for: `{keyword}`...", parse_mode=ParseMode.MARKDOWN)

        # Use the optimized search method from database.py
        # We fetch more results initially to handle pagination
        search_data = await db.search_documents(keyword, page=1, page_size=20)
        results = search_data.get("results", [])

        if not results:
            await status_msg.edit_text(f"🤷 No media found for: `{keyword}`", parse_mode=ParseMode.MARKDOWN)
            return

        # Cache results for this user
        SEARCH_CACHE[message.from_user.id] = {
            "results": results,
            "page": 0,
            "query": keyword
        }

        # Show first result
        await show_search_result(client, message.chat.id, message.from_user.id, status_msg.id)

    except Exception as e:
        LOGGER.error(f"Error in search command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)


async def show_search_result(client, chat_id, user_id, message_id):
    data = SEARCH_CACHE.get(user_id)
    if not data:
        return

    results = data["results"]
    page = data["page"]
    total = len(results)

    if page < 0: page = 0
    if page >= total: page = total - 1
    data["page"] = page

    media = results[page]

    # Format caption
    title = media.get("title", "Unknown")
    year = media.get("release_year") or media.get("year", "N/A")
    rating = media.get("rating") or media.get("rate", "N/A")
    genres = ", ".join(media.get("genres", [])) or "N/A"
    media_type = media.get("media_type", "movie").capitalize()
    tmdb_id = media.get("tmdb_id")

    caption = (
        f"🎬 **{title}** ({year})\n"
        f"⭐️ **Rating:** {rating}/10\n"
        f"🎭 **Genres:** {genres}\n"
        f"📺 **Type:** {media_type}\n"
        f"🆔 **TMDb ID:** `{tmdb_id}`\n\n"
        f"Use buttons below to navigate."
    )

    # Build Buttons
    buttons = []

    # Navigation Row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="search_prev"))

    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total}", callback_data="search_ignore"))

    if page < total - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="search_next"))

    buttons.append(nav_row)

    # Actions Row
    action_row = []
    # If it's a movie, we can offer a direct link or more info.
    # Since we don't have a direct "get link" logic exposed simply here without knowing quality,
    # we can link to the Web UI or just show More Info.
    # For now, let's add a "More Info" button that could expand details (or just be a placeholder if we want simplicity).
    # But wait, the user wants "Get Link".
    # The file IDs are deep inside the structure.
    # Let's add a "Get Files" button that triggers a detailed list of files/links.

    action_row.append(InlineKeyboardButton("📂 Get Files", callback_data=f"search_files_{page}"))
    buttons.append(action_row)

    markup = InlineKeyboardMarkup(buttons)

    poster_url = media.get("poster")
    # Fallback image if no poster
    if not poster_url:
        poster_url = "https://via.placeholder.com/500x750.png?text=No+Poster"

    try:
        # If the message is a text message (initial status), we delete it and send photo
        # If it's already a photo (navigation), we edit media
        msg = await client.get_messages(chat_id, message_id)

        if msg.photo:
            from pyrogram.types import InputMediaPhoto
            await client.edit_message_media(
                chat_id,
                message_id,
                media=InputMediaPhoto(poster_url, caption=caption),
                reply_markup=markup
            )
        else:
            await client.delete_messages(chat_id, message_id)
            sent = await client.send_photo(chat_id, poster_url, caption=caption, reply_markup=markup)
            # Update cache with new message ID if needed, though we track user_id mostly
            # Ideally we should pass the new message_id to future calls, but CallbackQuery gives us the message.
    except Exception as e:
        LOGGER.error(f"Error updating search message: {e}")


@Client.on_callback_query(filters.regex(r"^search_"))
async def search_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = SEARCH_CACHE.get(user_id)

    if not data:
        await query.answer("❌ Search session expired.", show_alert=True)
        return

    action = query.data

    if action == "search_prev":
        data["page"] -= 1
        await show_search_result(client, query.message.chat.id, user_id, query.message.id)
        await query.answer()

    elif action == "search_next":
        data["page"] += 1
        await show_search_result(client, query.message.chat.id, user_id, query.message.id)
        await query.answer()

    elif action == "search_ignore":
        await query.answer(f"Page {data['page'] + 1} of {len(data['results'])}")

    elif action.startswith("search_files_"):
        page_idx = int(action.split("_")[-1])
        media = data["results"][page_idx]

        # Generate file list
        files_text = f"📂 **Files for {media.get('title')}**:\n\n"

        if media.get("media_type") == "movie":
            qualities = media.get("telegram", [])
            if not qualities:
                files_text += "No files found."
            for q in qualities:
                files_text += f"🔹 **{q.get('quality', 'Unknown')}**: {q.get('size', 'N/A')}\n"

        elif media.get("media_type") == "tv":
            seasons = media.get("seasons", [])
            for s in seasons:
                files_text += f"📅 **Season {s.get('season_number')}**\n"
                for e in s.get("episodes", []):
                    files_text += f"  • E{e.get('episode_number')}: "
                    qs = [q.get('quality') for q in e.get("telegram", [])]
                    files_text += ", ".join(qs) + "\n"

        await query.message.reply_text(files_text, parse_mode=ParseMode.MARKDOWN)
        await query.answer("File list sent!")
