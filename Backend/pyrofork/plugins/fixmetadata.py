import asyncio
import time
import argparse
import difflib
import re
import uuid
from collections import deque
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.database import Database
from Backend.helper.metadata import fetch_movie_metadata, fetch_tv_metadata, safe_tmdb_search
from Backend.logger import LOGGER

# Global state
fix_task = None
fixer_instance = None

# Concurrency Limit
SEMAPHORE = asyncio.Semaphore(30)

class ArgumentParserError(Exception): 
    pass

class ThrowingArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentParserError(message)

class MetadataFixer:
    def __init__(self, client: Client, message: Message, args):
        self.client = client
        self.status_msg = message
        self.args = args
        self.should_cancel = False
        self.db = Database()
        
        # Stats
        self.stats = {
            "processed": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "manual_pending": 0,
            "auto_fixed": 0,
            "current_name": "Initializing..."
        }
        self.total_items = 0
        self.start_time = time.time()
        self.pending_approvals = {}  # Map callback_id -> asyncio.Future

    async def start(self):
        await self.db.connect()
        total_dbs = self.db.current_db_index
        
        # Start status loop
        status_task = asyncio.create_task(self.update_status_loop())
        
        try:
            tasks = []
            targets = await self.get_targets()
            self.total_items = len(targets)
            
            for item_data in targets:
                if self.should_cancel: break
                
                # item_data = (db_idx, item_dict, media_type)
                task = asyncio.create_task(self.process_item(*item_data))
                tasks.append(task)
                
                # Clean up finished tasks to manage memory
                if len(tasks) > 50:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    tasks = list(pending)

            if tasks:
                await asyncio.gather(*tasks)
                
        except Exception as e:
            LOGGER.error(f"Fix process error: {e}")
            self.stats["errors"] += 1
        finally:
            status_task.cancel()
            global fix_task
            fix_task = None
            
            # Final Status Update
            await self.status_msg.edit_text(
                f"✅ **Metadata Fix Completed!**\n\n"
                f"📊 **Total Scanned:** `{self.total_items}`\n"
                f"✅ **Updated:** `{self.stats['updated']}`\n"
                f"⚠️ **Skipped:** `{self.stats['skipped']}`\n"
                f"❌ **Errors:** `{self.stats['errors']}`\n"
                f"🤖 **Auto Fixed:** `{self.stats['auto_fixed']}`\n"
                f"⏱ **Total Time:** `{time.time() - self.start_time:.1f}s`"
            )

    async def get_targets(self):
        targets = []
        total_dbs = self.db.current_db_index
        
        # Determine filter
        query = {}
        if self.args.latest:
            # Simple check for missing critical metadata
            query = {"$or": [{"overview": {"$exists": False}}, {"cast": {"$exists": False}}]}
        
        sort_order = [("updated_on", -1)] if self.args.last else [("_id", 1)]
        limit = self.args.last if self.args.last else 0
        
        media_types = []
        if self.args.type in ["movies", "all"]: media_types.append("movie")
        if self.args.type in ["tv", "all"]: media_types.append("tv")
        
        for m_type in media_types:
            count = 0
            for i in range(1, total_dbs + 1):
                if limit and count >= limit: break
                
                db_key = f"storage_{i}"
                database = self.db.dbs[db_key]
                collection = database[m_type]
                
                cursor = collection.find(query).sort(sort_order)
                if limit:
                    cursor = cursor.limit(limit - count)
                
                items = await cursor.to_list(length=None)
                for item in items:
                    targets.append((i, item, m_type))
                count += len(items)
                
        return targets

    def clean_filename(self, name):
        # Remove extension
        name = re.sub(r'\.\w+$', '', name)
        # Remove brackets first
        name = re.sub(r'\[.*?\]', '', name)
        name = re.sub(r'\(.*?\)', '', name)
        # Remove common release tags
        name = re.sub(r'(1080p|720p|2160p|480p|BluRay|WEB-DL|HDR|H\.265|x264|x265|HEVC|AAC|DDP|ATMOS).*', '', name, flags=re.IGNORECASE)
        # Remove dots/underscores
        name = name.replace('.', ' ').replace('_', ' ')
        return name.strip()

    async def process_item(self, db_idx, item, media_type):
        if self.should_cancel: return
        
        async with SEMAPHORE:
            try:
                title = item.get("title", "Unknown")
                tmdb_id = item.get("tmdb_id")
                self.stats["current_name"] = f"{title}"
                
                should_update = False
                new_meta = {}
                confidence = 100
                fix_reason = "Refresh"

                # 1. Rename / Mismatch Detection Logic
                if self.args.rename:
                    filename = ""
                    if media_type == "movie":
                         if item.get("telegram"):
                             filename = item["telegram"][0].get("name", "")
                    else:
                        # For TV, check first episode of first season
                        if item.get("seasons") and item["seasons"][0].get("episodes"):
                            ep = item["seasons"][0]["episodes"][0]
                            if ep.get("telegram"):
                                filename = ep["telegram"][0].get("name", "")

                    if filename:
                        clean_name = self.clean_filename(filename)
                        # Similarity check
                        ratio = difflib.SequenceMatcher(None, clean_name.lower(), title.lower()).ratio()
                        
                        if ratio < 0.6: # Mismatch detected
                            # Search TMDB
                            results = await safe_tmdb_search(clean_name, media_type)
                            if results:
                                candidate = results # safe_tmdb_search returns best match object
                                candidate_title = candidate.title if media_type == "movie" else candidate.name
                                candidate_year = getattr(candidate, "release_date" if media_type == "movie" else "first_air_date", None)
                                if hasattr(candidate_year, "year"):
                                    candidate_year = candidate_year.year
                                elif isinstance(candidate_year, str):
                                    candidate_year = int(candidate_year[:4]) if candidate_year else 0
                                else:
                                    candidate_year = 0
                                
                                new_ratio = difflib.SequenceMatcher(None, clean_name.lower(), candidate_title.lower()).ratio()
                                
                                if new_ratio > 0.8: # Found a good candidate
                                    confidence = int(new_ratio * 100)
                                    fix_reason = "Renamed (Mismatch Detected)"
                                    
                                    # Fetch full metadata for candidate
                                    if media_type == "movie":
                                        fetched = await fetch_movie_metadata(candidate_title, "dummy", candidate_year, None)
                                    else:
                                        fetched = await fetch_tv_metadata(candidate_title, 1, 1, "dummy", candidate_year)
                                    
                                    if fetched:
                                        new_meta = fetched
                                        should_update = True
                
                # 2. Normal Refresh Logic (if not renaming or no rename candidate found)
                if not should_update:
                    if self.args.rename: 
                        # If rename mode but no better candidate found, skip
                        self.stats["processed"] += 1
                        self.stats["skipped"] += 1
                        return

                    year = item.get("release_year")
                    # Fetch fresh using ID if possible, else Title
                    if media_type == "movie":
                        new_meta = await fetch_movie_metadata(title, "dummy", year, None, default_id=f"tmdb:{tmdb_id}" if tmdb_id else None)
                    else:
                        # For TV, we just need show level data mostly
                        new_meta = await fetch_tv_metadata(title, 1, 1, "dummy", year, default_id=f"tmdb:{tmdb_id}" if tmdb_id else None)
                    
                    if new_meta:
                        confidence = 100
                        should_update = True

                if should_update and new_meta:
                    # Filter only necessary fields
                    update_fields = {
                        "cast": new_meta.get("cast"),
                        "runtime": new_meta.get("runtime"),
                        "overview": new_meta.get("description"),
                        "released": new_meta.get("year"),
                        "title": new_meta.get("title"),
                        "tmdb_id": new_meta.get("tmdb_id"),
                        "imdb_id": new_meta.get("imdb_id"),
                        "genres": new_meta.get("genres"),
                    }
                    if media_type == "tv":
                         update_fields.pop("seasons", None) # Don't overwrite structure, just fields

                    # Clean None
                    update_fields = {k: v for k, v in update_fields.items() if v is not None}
                    
                    # Logic 3: Decision
                    action = "skip"
                    
                    if self.args.force or confidence >= 85:
                        action = "auto"
                    elif 70 <= confidence < 85:
                        action = "ask"
                    else:
                        action = "ignore"

                    if action == "auto":
                        update_fields["auto_fixed"] = True
                        update_fields["fix_reason"] = fix_reason
                        await self.apply_fix(db_idx, tmdb_id, media_type, update_fields)
                        self.stats["updated"] += 1
                        self.stats["auto_fixed"] += 1
                        
                    elif action == "ask":
                        self.stats["manual_pending"] += 1
                        approved = await self.ask_approval(item, update_fields, confidence)
                        self.stats["manual_pending"] -= 1
                        
                        if approved:
                             await self.apply_fix(db_idx, tmdb_id, media_type, update_fields)
                             self.stats["updated"] += 1
                        else:
                             self.stats["skipped"] += 1
                    else:
                        self.stats["skipped"] += 1

            except Exception as e:
                LOGGER.error(f"Error processing {item.get('title')}: {e}")
                self.stats["errors"] += 1
            finally:
                self.stats["processed"] += 1

    async def apply_fix(self, db_idx, tmdb_id, media_type, fields, season=None, episode=None):
        await self.db.update_metadata_fields(tmdb_id, media_type, db_idx, fields, season, episode)

    async def ask_approval(self, item, new_meta, confidence):
        uid = str(uuid.uuid4())[:8]
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_approvals[uid] = future
        
        current_title = item.get("title", "Unknown")
        new_title = new_meta.get("title", "Unknown")
        
        text = (
            f"⚠️ **Metadata Fix Approval Needed**\n\n"
            f"**Current:** `{current_title}`\n"
            f"**Proposed:** `{new_title}`\n"
            f"**Confidence:** `{confidence}%`\n\n"
            f"**Reason:** Found via filename scan."
        )
        
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"fix_appr_yes_{uid}"),
                InlineKeyboardButton("❌ Skip", callback_data=f"fix_appr_no_{uid}")
            ]
        ])
        
        msg = await self.client.send_message(self.status_msg.chat.id, text, reply_markup=buttons)
        
        try:
            result = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            result = False
        finally:
            self.pending_approvals.pop(uid, None)
            try:
                await msg.delete()
            except Exception:
                pass
                
        return result

    async def update_status_loop(self):
        while not self.should_cancel and fix_task:
            await asyncio.sleep(3)
            
            elapsed = time.time() - self.start_time
            if self.stats["processed"] > 0:
                rate = self.stats["processed"] / elapsed
                remaining = self.total_items - self.stats["processed"]
                eta = remaining / rate if rate > 0 else 0
            else:
                eta = 0
            
            eta_str = time.strftime("%H:%M:%S", time.gmtime(eta))
            elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))
            
            percent = (self.stats["processed"] / self.total_items * 100) if self.total_items > 0 else 0
            bar_len = 10
            filled = int(bar_len * percent / 100)
            bar = "▰" * filled + "▱" * (bar_len - filled)
            
            text = (
                f"**🔄 Metadata Fix in Progress**\n\n"
                f"**Progress:** `{bar}` **{percent:.1f}%**\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"✅ **Updated:** `{self.stats['updated']}`\n"
                f"⚠️ **Skipped:** `{self.stats['skipped']}`\n"
                f"❌ **Errors:** `{self.stats['errors']}`\n"
                f"🤖 **Auto Fixed:** `{self.stats['auto_fixed']}`\n"
                f"⏳ **Pending Approval:** `{self.stats['manual_pending']}`\n"
                f"📥 **Processed:** `{self.stats['processed']}/{self.total_items}`\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"⏱ **Elapsed:** `{elapsed_str}`\n"
                f"⏳ **ETA:** `{eta_str}`\n\n"
                f"**Currently Processing:**\n`{self.stats['current_name']}`"
            )
            
            cancel_btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel Operation", callback_data="cancel_fix")]
            ])
            
            try:
                await self.status_msg.edit_text(text, reply_markup=cancel_btn)
            except Exception:
                pass

@Client.on_message(filters.command("fixmetadata") & CustomFilters.owner)
async def fix_metadata_command(client: Client, message: Message):
    global fix_task, fixer_instance

    if fix_task and not fix_task.done():
        await message.reply_text("⚠️ **Metadata fix is already running.**")
        return

    # Argument Parsing
    parser = ThrowingArgumentParser(description="Metadata Fixer", add_help=False)
    parser.add_argument("type", nargs="?", choices=["movies", "tv", "all"], default="all", help="Media type")
    parser.add_argument("--last", type=int, help="Fix only last N items")
    parser.add_argument("--latest", action="store_true", help="Fix only items with missing metadata")
    parser.add_argument("--force", action="store_true", help="Overwrite existing metadata")
    parser.add_argument("--rename", action="store_true", help="Analyze filename to detect wrong matches")

    try:
        # message.command is ['fixmetadata', 'movies', '--last', '10']
        args = parser.parse_args(message.command[1:])
    except ArgumentParserError as e:
        await message.reply_text(f"❌ **Invalid Arguments:**\n`{e}`\n\n"
                                 "**Usage:**\n"
                                 "`/fixmetadata [movies|tv|all] [--last N] [--latest] [--force] [--rename]`")
        return

    status_msg = await message.reply_text(
        f"**🔄 Initializing Metadata Fix...**\n"
        f"Type: `{args.type}`\n"
        f"Mode: `{'Rename/Deep Scan' if args.rename else 'Refresh'}`\n"
        f"Target: `{'Last ' + str(args.last) if args.last else 'All'}`"
    )

    fixer_instance = MetadataFixer(client, status_msg, args)
    fix_task = asyncio.create_task(fixer_instance.start())

@Client.on_callback_query(filters.regex("^cancel_fix$") & CustomFilters.owner)
async def cancel_fix_callback(client: Client, callback_query: CallbackQuery):
    global fixer_instance
    if fixer_instance:
        fixer_instance.should_cancel = True
        await callback_query.answer("🛑 Cancelling...", show_alert=True)
    else:
        await callback_query.answer("⚠️ No active process.", show_alert=True)

@Client.on_callback_query(filters.regex(r"^fix_appr_(.+)") & CustomFilters.owner)
async def approval_callback(client: Client, callback_query: CallbackQuery):
    global fixer_instance
    try:
        data_parts = callback_query.data.split("_")
        action = data_parts[2] # yes/no
        uid = data_parts[3]
        
        if fixer_instance and uid in fixer_instance.pending_approvals:
            future = fixer_instance.pending_approvals[uid]
            if not future.done():
                future.set_result(action == "yes")
                await callback_query.answer("Decision recorded.")
        else:
            await callback_query.answer("⚠️ Request expired or not found.", show_alert=True)
            try:
                await callback_query.message.delete()
            except:
                pass
    except Exception as e:
        LOGGER.error(f"Approval callback error: {e}")
