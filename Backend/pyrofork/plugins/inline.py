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
import re

@Client.on_inline_query()
async def search_inline(client, query):
    q = query.query.strip()

    # Handle explicit command prefix "!search" or general queries
    if q.startswith("!search"):
        q = q[7:].strip()

    if not q:
        return

    try:
        # Search DB (fetch up to 50 results)
        results_payload = await db.search_documents(q, 1, 50)
        items = results_payload.get("results", [])

        if not items:
            return

        answers = []
        for item in items:
            title = item.get("title")
            year = item.get("release_year")
            media_type = item.get("media_type")
            tmdb_id = item.get("tmdb_id")
            db_index = item.get("db_index")
            poster = item.get("poster")
            description = item.get("description", "")

            # Construct Web UI Link (Deep Link to Media View)
            # This handles both Movies and TV Shows (Seasons/Episodes) nicely
            web_link = f"{Telegram.BASE_URL}/media/view?tmdb_id={tmdb_id}&db_index={db_index}&media_type={media_type}"

            # Fallback thumb
            thumb_url = poster if poster and poster.startswith("http") else "https://via.placeholder.com/150"

            # Type Emoji
            type_emoji = "🎬" if media_type == "movie" else "📺"

            # Description text for the result list
            desc_text = f"{type_emoji} {year if year else ''} | ⭐ {item.get('rating', 'N/A')}"

            # Message content sent when clicked
            # Using HTML parse mode
            message_text = (
                f"<b>{type_emoji} {title}</b> ({year})\n\n"
                f"<i>{description[:200]}...</i>\n\n"
                f"⭐ <b>Rating:</b> {item.get('rating', 'N/A')}\n"
                f"🎭 <b>Genres:</b> {', '.join(item.get('genres', []))}"
            )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Stream / Download 📥", url=web_link)]
            ])

            answers.append(
                InlineQueryResultArticle(
                    title=f"{title} ({year})",
                    description=desc_text,
                    thumb_url=thumb_url,
                    input_message_content=InputTextMessageContent(
                        message_text,
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=False
                    ),
                    reply_markup=keyboard
                )
            )

        await query.answer(answers, cache_time=0)

    except Exception as e:
        LOGGER.error(f"Inline search error: {e}")
