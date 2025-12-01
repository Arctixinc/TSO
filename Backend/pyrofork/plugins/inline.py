from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent
)
from Backend.config import Telegram
from Backend import db
from Backend.logger import LOGGER
from Backend.helper.encrypt import encode_string
import re

@Client.on_inline_query()
async def search_inline(client, query):
    q = query.query.strip()

    if q.startswith("!search"):
        q = q[7:].strip()

    # If query is empty, show random/recent items
    if not q:
        try:
            # Fetch latest 10 items from DB as "random/recent" output
            # Using sort_movies for simplicity, fetching movies
            latest_movies = await db.sort_movies([("updated_on", "desc")], 1, 5)
            # And some TV shows
            latest_tv = await db.sort_tv_shows([("updated_on", "desc")], 1, 5)

            items = latest_movies.get("movies", []) + latest_tv.get("tv_shows", [])
        except Exception:
            return
    else:
        try:
            # Reduced limit to 10 for speed
            results_payload = await db.search_documents(q, 1, 10)
            items = results_payload.get("results", [])
        except Exception:
            return

    if not items:
        return

    try:
        answers = []
        bot_username = client.me.username

        for item in items:
            title = item.get("title")
            year = item.get("release_year")
            media_type = item.get("media_type")
            tmdb_id = item.get("tmdb_id")
            db_index = item.get("db_index")
            poster = item.get("poster")
            description = item.get("description", "")

            # Web UI Link (Player & Download)
            web_link = f"{Telegram.BASE_URL}/media/view?tmdb_id={tmdb_id}&db_index={db_index}&media_type={media_type}"

            # Encode data for "Get File" button
            # Data: type:tmdb_id:db_index
            data_to_encode = f"{media_type}:{tmdb_id}:{db_index}"
            encoded_start_param = await encode_string(data_to_encode)

            thumb_url = poster if poster and poster.startswith("http") else "https://via.placeholder.com/150"
            type_emoji = "🎬" if media_type == "movie" else "📺"

            desc_text = f"{type_emoji} {year if year else ''} | ⭐ {item.get('rating', 'N/A')}"

            message_text = (
                f"<b>{type_emoji} {title}</b> ({year})\n\n"
                f"<i>{description[:200]}...</i>\n\n"
                f"⭐ <b>Rating:</b> {item.get('rating', 'N/A')}\n"
                f"🎭 <b>Genres:</b> {', '.join(item.get('genres', []))}\n\n"
                f"🔗 <b>Player Link:</b> <a href='{web_link}'>Click Here</a>"
            )

            buttons = []

            # "Get Files" button logic
            # This will trigger the Season selector in start.py for TV shows, and file delivery for movies
            buttons.append([InlineKeyboardButton("📂 Get Files (Telegram)", url=f"https://t.me/{bot_username}?start=get_{encoded_start_param}")])

            # Watch/Download button linking to Web UI
            buttons.append([InlineKeyboardButton("▶️ Watch / Download", url=web_link)])

            keyboard = InlineKeyboardMarkup(buttons)

            answers.append(
                InlineQueryResultArticle(
                    title=f"{title} ({year})",
                    description=desc_text,
                    thumb_url=thumb_url,
                    input_message_content=InputTextMessageContent(
                        message_text,
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True
                    ),
                    reply_markup=keyboard
                )
            )

        await query.answer(answers, cache_time=0)

    except Exception as e:
        LOGGER.error(f"Inline search error: {e}")
