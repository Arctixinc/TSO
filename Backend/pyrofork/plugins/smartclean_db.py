import io
import asyncio
from asyncio import Semaphore, sleep as asleep
from time import time
from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.errors import FloodWait

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db

# ================= CONFIG =================
INITIAL_CONCURRENCY = 10
MAX_CONCURRENCY = 20
MIN_CONCURRENCY = 2
STATUS_UPDATE_INTERVAL = 5
BATCH_SIZE = 20

CANCEL_FLAGS = {}

# ================= UI BUILDERS =================
def build_status_ui(
    mode,
    checked,
    broken,
    deleted,
    movies_done,
    total_movies,
    tv_done,
    total_tv,
    concurrency,
    elapsed
):
    return (
        f"🧹 **SMART CLEANER**\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"🟢 **Mode**       : {mode}\n"
        f"⚙️ **Concurrency**: `{concurrency}`\n\n"
        f"📊 **Progress**\n"
        f"• Checked  : `{checked}`\n"
        f"• Broken   : `{broken}`\n"
        f"• Deleted  : `{deleted}`\n\n"
        f"📦 **Content**\n"
        f"• Movies   : `{movies_done} / {total_movies}`\n"
        f"• TV Shows : `{tv_done} / {total_tv}`\n\n"
        f"⏱️ **Time** : `{elapsed}`\n"
        f"━━━━━━━━━━━━━━"
    )


def build_final_report(
    mode,
    checked,
    broken,
    deleted,
    movies_done,
    total_movies,
    tv_done,
    total_tv,
    concurrency,
    elapsed
):
    return (
        f"🏁 **SMART CLEAN REPORT**\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"🟢 **Mode** : {mode}\n\n"
        f"📊 **Summary**\n"
        f"• Checked      : `{checked}`\n"
        f"• Broken Links : `{broken}`\n"
        f"• Deleted      : `{deleted}`\n\n"
        f"📦 **Content**\n"
        f"• Movies   : `{movies_done} / {total_movies}`\n"
        f"• TV Shows : `{tv_done} / {total_tv}`\n\n"
        f"⚙️ **Final Concurrency** : `{concurrency}`\n"
        f"⏱️ **Time Taken**        : `{elapsed}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ **Completed Successfully**"
    )

# ================= MAIN COMMAND =================
@Client.on_message(filters.command("smartclean") & filters.private & CustomFilters.owner, group=10)
async def smartclean(client: Client, message: Message):
    cancel_id = f"{message.chat.id}_{message.id}"
    CANCEL_FLAGS[cancel_id] = False

    try:
        overall_start = time()

        def format_elapsed():
            t = time() - overall_start
            return f"{int(t // 60)}m {int(t % 60)}s"

        args = message.text.split()
        delete_mode = len(args) > 1 and args[1].lower() == "delete"
        mode_text = "Cleanup (Delete)" if delete_mode else "Scan Only"

        # Counters
        checked = total_deleted = 0
        total_movies = total_tv = 0
        movies_done = tv_done = 0
        broken_entries = []

        concurrency = INITIAL_CONCURRENCY
        semaphore = Semaphore(concurrency)
        adaptive_lock = asyncio.Lock()
        last_update = 0

        status_msg = await message.reply_text(
            build_status_ui(
                mode_text, 0, 0, 0,
                0, 0, 0, 0,
                concurrency, "0m 0s"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data=f"smartclean_cancel:{cancel_id}")]]
            )
        )

        from Backend.helper.encrypt import decode_string

        async def adjust_concurrency(success=True, flood_wait=None):
            nonlocal concurrency, semaphore
            async with adaptive_lock:
                if flood_wait:
                    concurrency = max(MIN_CONCURRENCY, concurrency // 2)
                elif success and concurrency < MAX_CONCURRENCY:
                    concurrency += 1
                semaphore = Semaphore(concurrency)

        async def safe_get_message(chat_id, msg_id):
            async with semaphore:
                if CANCEL_FLAGS.get(cancel_id):
                    return None
                try:
                    start = time()
                    msg = await client.get_messages(chat_id, msg_id)
                    if time() - start < 0.2:
                        await adjust_concurrency(True)
                    return msg if msg and (msg.video or msg.document) else None
                except FloodWait as e:
                    await adjust_concurrency(False, e.value)
                    await asleep(e.value + 1)
                except Exception:
                    return None

        async def validate_quality(entry, tmdb_id, db_index, ctype, meta):
            nonlocal checked
            try:
                decoded = await decode_string(entry["id"])
                chat_id = int(f"-100{decoded['chat_id']}")
                msg_id = int(decoded["msg_id"])
                msg = await safe_get_message(chat_id, msg_id)
                checked += 1
                if msg:
                    return entry
                raise Exception("Message not found")
            except Exception as e:
                broken_entries.append({
                    "type": ctype,
                    "title": meta.get("title", "Unknown"),
                    "quality": entry.get("quality"),
                    "db": db_index,
                    "error": str(e)
                })
                return None

        async def update_ui():
            await status_msg.edit_text(
                build_status_ui(
                    mode_text,
                    checked,
                    len(broken_entries),
                    total_deleted,
                    movies_done,
                    total_movies,
                    tv_done,
                    total_tv,
                    concurrency,
                    format_elapsed()
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data=f"smartclean_cancel:{cancel_id}")]]
                )
            )

        total_storage_dbs = len(db.dbs) - 1

        for idx in range(1, total_storage_dbs + 1):
            if CANCEL_FLAGS.get(cancel_id):
                break

            db_key = f"storage_{idx}"

            # ================= MOVIES =================
            movies = await db.dbs[db_key]["movie"].find({}, {"_id": 0}).to_list(None)
            total_movies += len(movies)

            for movie in movies:
                tasks = [
                    validate_quality(q, movie["tmdb_id"], idx, "movie", movie)
                    for q in movie.get("telegram", [])
                ]
                results = await asyncio.gather(*tasks)
                valid = [r for r in results if r]

                if delete_mode:
                    total_deleted += len(movie["telegram"]) - len(valid)
                    if valid:
                        await db.dbs[db_key]["movie"].update_one(
                            {"tmdb_id": movie["tmdb_id"]},
                            {"$set": {"telegram": valid}}
                        )
                    else:
                        await db.dbs[db_key]["movie"].delete_one({"tmdb_id": movie["tmdb_id"]})

                movies_done += 1

                if time() - last_update > STATUS_UPDATE_INTERVAL:
                    await update_ui()
                    last_update = time()

            # ================= TV =================
            shows = await db.dbs[db_key]["tv"].find({}, {"_id": 0}).to_list(None)
            total_tv += len(shows)

            for show in shows:
                for season in show.get("seasons", []):
                    for ep in season.get("episodes", []):
                        tasks = [
                            validate_quality(q, show["tmdb_id"], idx, "tv", show)
                            for q in ep.get("telegram", [])
                        ]
                        results = await asyncio.gather(*tasks)
                        valid = [r for r in results if r]

                        if delete_mode:
                            total_deleted += len(ep.get("telegram", [])) - len(valid)
                            ep["telegram"] = valid

                if delete_mode:
                    await db.dbs[db_key]["tv"].update_one(
                        {"tmdb_id": show["tmdb_id"]},
                        {"$set": {"seasons": show["seasons"]}}
                    )

                tv_done += 1

                if time() - last_update > STATUS_UPDATE_INTERVAL:
                    await update_ui()
                    last_update = time()

        await status_msg.edit_text(
            build_final_report(
                mode_text,
                checked,
                len(broken_entries),
                total_deleted,
                movies_done,
                total_movies,
                tv_done,
                total_tv,
                concurrency,
                format_elapsed()
            ),
            parse_mode=ParseMode.MARKDOWN
        )

        # ================= REPORT FILE =================
        if broken_entries:
            buffer = io.StringIO()
            buffer.write("SMART CLEAN REPORT\n" + "=" * 50 + "\n\n")
            for i, e in enumerate(broken_entries, 1):
                buffer.write(
                    f"[{i}] {e['type'].upper()}\n"
                    f"Title   : {e['title']}\n"
                    f"Quality : {e.get('quality','N/A')}\n"
                    f"DB      : {e['db']}\n"
                    f"Error   : {e['error']}\n"
                    f"{'-'*40}\n"
                )
            buffer.seek(0)

            await client.send_document(
                message.chat.id,
                io.BytesIO(buffer.getvalue().encode()),
                file_name="smartclean_report.txt",
                caption="🧾 Smart Clean Detailed Report"
            )

    except Exception as e:
        LOGGER.error(e)
        await message.reply_text(f"❌ Error: {e}")

    finally:
        CANCEL_FLAGS.pop(cancel_id, None)

# ================= CANCEL HANDLER =================
@Client.on_callback_query(filters.regex(r"smartclean_cancel:(.+)"))
async def cancel_smartclean(_, query: CallbackQuery):
    CANCEL_FLAGS[query.data.split(":")[1]] = True
    await query.answer("❌ Cancellation requested")
