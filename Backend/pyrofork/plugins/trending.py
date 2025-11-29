from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from themoviedb import aioTMDb
from Backend.config import Telegram

tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")

# Cache: {user_id: {"results": [], "page": 0, "type": "movie"}}
TRENDING_CACHE = {}

@Client.on_message(filters.command(['trending']) & filters.private & CustomFilters.owner)
async def trending_command(client: Client, message: Message):
    """
    Shows trending movies or shows.
    Usage: /trending
    """
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Movies", callback_data="trend_init_movie"),
         InlineKeyboardButton("📺 TV Shows", callback_data="trend_init_tv")]
    ])
    await message.reply_text("🔥 **What's trending today?**", reply_markup=buttons, parse_mode=ParseMode.MARKDOWN)


async def fetch_trending(media_type="movie"):
    try:
        if media_type == "movie":
            return await tmdb.trending().movie(time_window="week")
        else:
            return await tmdb.trending().tv(time_window="week")
    except Exception as e:
        LOGGER.error(f"Error fetching trending: {e}")
        return []

async def show_trending_result(client, chat_id, user_id, message_id):
    data = TRENDING_CACHE.get(user_id)
    if not data: return

    results = data["results"]
    page = data["page"]
    total = len(results)

    if page < 0: page = 0
    if page >= total: page = total - 1
    data["page"] = page

    item = results[page]

    # Extract data (TMDB object attributes)
    title = getattr(item, "title", getattr(item, "name", "Unknown"))
    overview = getattr(item, "overview", "No description.")[:200] + "..."
    rating = getattr(item, "vote_average", 0)
    poster_path = getattr(item, "poster_path", "")
    poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "https://via.placeholder.com/500x750?text=No+Poster"

    caption = (
        f"🔥 **Trending #{page + 1}**\n\n"
        f"🎬 **{title}**\n"
        f"⭐️ **Rating:** {rating:.1f}/10\n\n"
        f"📝 {overview}"
    )

    buttons = []
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data="trend_prev"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total}", callback_data="trend_ignore"))
    if page < total - 1: nav.append(InlineKeyboardButton("Next ➡️", callback_data="trend_next"))
    buttons.append(nav)

    # Helper to search this title in our local DB
    buttons.append([InlineKeyboardButton("🔍 Search in Library", callback_data=f"trend_search_{page}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="trend_back")])

    markup = InlineKeyboardMarkup(buttons)

    try:
        msg = await client.get_messages(chat_id, message_id)
        if msg.photo:
            from pyrogram.types import InputMediaPhoto
            await client.edit_message_media(
                chat_id, message_id,
                media=InputMediaPhoto(poster_url, caption=caption),
                reply_markup=markup
            )
        else:
            await client.delete_messages(chat_id, message_id)
            await client.send_photo(chat_id, poster_url, caption=caption, reply_markup=markup)
    except Exception as e:
        LOGGER.error(f"Error showing trending: {e}")


@Client.on_callback_query(filters.regex(r"^trend_"))
async def trending_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    action = query.data

    if action == "trend_back":
        # Reset to selection menu
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Movies", callback_data="trend_init_movie"),
             InlineKeyboardButton("📺 TV Shows", callback_data="trend_init_tv")]
        ])
        await query.message.delete()
        await query.message.reply_text("🔥 **What's trending today?**", reply_markup=buttons)
        return

    if action.startswith("trend_init_"):
        m_type = action.split("_")[-1]
        await query.answer(f"Fetching trending {m_type}s...")
        results = await fetch_trending(m_type)
        if not results:
            await query.answer("❌ Failed to fetch trending data.", show_alert=True)
            return

        TRENDING_CACHE[user_id] = {"results": results, "page": 0, "type": m_type}
        await show_trending_result(client, query.message.chat.id, user_id, query.message.id)
        return

    # Navigation
    data = TRENDING_CACHE.get(user_id)
    if not data:
        await query.answer("❌ Session expired.", show_alert=True)
        return

    if action == "trend_prev":
        data["page"] -= 1
        await show_trending_result(client, query.message.chat.id, user_id, query.message.id)
    elif action == "trend_next":
        data["page"] += 1
        await show_trending_result(client, query.message.chat.id, user_id, query.message.id)
    elif action == "trend_ignore":
        await query.answer(f"Page {data['page'] + 1}")

    elif action.startswith("trend_search_"):
        page_idx = int(action.split("_")[-1])
        item = data["results"][page_idx]
        title = getattr(item, "title", getattr(item, "name", ""))

        if title:
            # We trigger the search logic
            # Since we can't easily invoke another command function directly with correct message context,
            # we can tell the user to click a command or just run the search logic internally.
            # Let's redirect to /search logic by creating a fake message or just calling the logic.
            # Simpler: Just Reply with the command suggestion.
            await query.message.reply_text(f"👇 Click to search:\n\n`/search {title}`", parse_mode=ParseMode.MARKDOWN)
            await query.answer("Search command generated!")
