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

            # Download Link Logic (Distinguish from Web Link for Movies)
            download_link_text = web_link

            # For Movies: Try to get a direct file link if available
            if media_type == "movie" and item.get("telegram"):
                try:
                    # Get first file ID to show a direct-ish link example
                    # Using proper route /dl/ID/video.mkv
                    # We try to find the best quality or just the first one
                    first_file = item["telegram"][0]
                    file_id = first_file.get("id")
                    file_name = first_file.get("name", "video.mkv")
                    if file_id:
                        # User requested format: hosturl/dl/encodedid/filename
                        # We need to URL encode the filename to be safe, but typically browsers handle it.
                        # Ideally use a proper url join, but f-string is what was asked.
                        download_link_text = f"{Telegram.BASE_URL}/dl/{file_id}/{file_name}"
                except Exception:
                    pass

            # For TV Shows: User requested NO Download link in text, only Player Link.
            # So if media_type == "tv", download_link_text remains web_link or we hide it.
            # "Download link only possible for movies not tv show"
            # However, the message_text format below has a line "📥 Download Link: ...".
            # If TV show, we should probably hide that line or point it to WebUI.
            # The user said "Only send the download link for movies".
            # So for TV shows, we will NOT show the download link line in the message text logic below.

            # Encode data for "Get File" button
            # Data: type:tmdb_id:db_index
            data_to_encode = f"{media_type}:{tmdb_id}:{db_index}"
            encoded_start_param = await encode_string(data_to_encode)

            thumb_url = poster if poster and poster.startswith("http") else "https://via.placeholder.com/150"
            type_emoji = "🎬" if media_type == "movie" else "📺"

            desc_text = f"{type_emoji} {year if year else ''} | ⭐ {item.get('rating', 'N/A')}"

            if media_type == "movie":
                message_text = (
                    f"<b>{type_emoji} {title}</b> ({year})\n\n"
                    f"<i>{description[:200]}...</i>\n\n"
                    f"⭐ <b>Rating:</b> {item.get('rating', 'N/A')}\n"
                    f"🎭 <b>Genres:</b> {', '.join(item.get('genres', []))}\n\n"
                    f"🔗 <b>Player Link:</b> <a href='{web_link}'>Click Here</a>\n"
                    f"📥 <b>Download Link:</b> <a href='{download_link_text}'>Click Here</a>"
                )
            else:
                # TV Show: No download link in text
                message_text = (
                    f"<b>{type_emoji} {title}</b> ({year})\n\n"
                    f"<i>{description[:200]}...</i>\n\n"
                    f"⭐ <b>Rating:</b> {item.get('rating', 'N/A')}\n"
                    f"🎭 <b>Genres:</b> {', '.join(item.get('genres', []))}\n\n"
                    f"🔗 <b>Player Link:</b> <a href='{web_link}'>Click Here</a>"
                )

            buttons = []

            # "Get Files" button logic
            # User said: "And the get files has to be button based... send that perticular file when click"
            # And: "Still it dont gave get files for the tv shows fix that"
            # So BOTH Movie and TV get "Get Files".
            # For TV, this will trigger the Season selector in start.py
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
