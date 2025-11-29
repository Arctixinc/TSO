from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from Backend.helper.custom_filter import CustomFilters

# Common Movie Genres (TMDB IDs mapped for future use if needed, but names are fine for text search)
GENRES = [
    "Action", "Adventure", "Animation", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "History",
    "Horror", "Music", "Mystery", "Romance", "Science Fiction",
    "Thriller", "War", "Western"
]

@Client.on_message(filters.command(['genres']) & filters.private & CustomFilters.owner)
async def genres_command(client: Client, message: Message):
    """
    Shows a menu of genres.
    """
    buttons = []
    row = []
    for genre in GENRES:
        row.append(InlineKeyboardButton(genre, callback_data=f"genre_search_{genre}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🚫 Close", callback_data="close_genre")])

    markup = InlineKeyboardMarkup(buttons)
    await message.reply_text("🎭 **Select a Genre to Search:**", reply_markup=markup)

@Client.on_callback_query(filters.regex(r"^genre_search_"))
async def genre_callback(client: Client, query: CallbackQuery):
    genre = query.data.split("_")[-1]

    # We provide a search command copy-paste for now,
    # as full genre filtering in DB might require a dedicated /filter command.
    # But wait, our /search supports keyword.
    # Our DB `sort_movies` supports `genre_filter`.
    # Let's guide them to the Web UI or give a search command tip.

    text = f"📂 **Genre:** {genre}\n\nTo find movies in this genre, you can use the Web UI filters or try searching for related keywords."

    # Actually, let's just trigger a search suggestion
    await query.message.reply_text(f"👇 To search for {genre} movies:\n\n`/search {genre}`", quote=True)
    await query.answer()

@Client.on_callback_query(filters.regex("^close_genre$"))
async def close_genre(client: Client, query: CallbackQuery):
    await query.message.delete()
