from asyncio import create_task
from bson import ObjectId
import motor.motor_asyncio
from datetime import datetime
from pydantic import ValidationError
from pymongo import ASCENDING, DESCENDING
from typing import Dict, List, Optional, Tuple, Any

from Backend.logger import LOGGER
from Backend.config import Telegram
import re
from Backend.helper.encrypt import decode_string, encode_string
from Backend.helper.modal import Episode, MovieSchema, QualityDetail, Season, TVShowSchema
from Backend.helper.task_manager import delete_message


def convert_objectid_to_str(document: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively converts ObjectId objects to strings in a dictionary.

    Args:
        document (Dict[str, Any]): The dictionary to convert.

    Returns:
        Dict[str, Any]: The converted dictionary.
    """
    for key, value in document.items():
        if isinstance(value, ObjectId):
            document[key] = str(value)
        elif isinstance(value, list):
            document[key] = [convert_objectid_to_str(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, dict):
            document[key] = convert_objectid_to_str(value)
    return document


class Database:
    """A class for interacting with the MongoDB database."""
    def __init__(self, db_name: str = "dbFyvio"):
        """Initializes the Database class.

        Args:
            db_name (str, optional): The name of the database. Defaults to "dbFyvio".

        Raises:
            ValueError: If less than 2 database URIs are provided.
        """
        self.db_uris = Telegram.DATABASE
        self.db_name = db_name

        if len(self.db_uris) < 2:
            raise ValueError("At least 2 database URIs are required (1 for tracking + 1 for storage).")

        self.clients: Dict[str, motor.motor_asyncio.AsyncIOMotorClient] = {}
        self.dbs: Dict[str, motor.motor_asyncio.AsyncIOMotorDatabase] = {}

        self.current_db_index = 1

    async def connect(self):
        """Connects to the databases."""
        try:
            for index, uri in enumerate(self.db_uris):
                client = motor.motor_asyncio.AsyncIOMotorClient(uri)
                db_key = "tracking" if index == 0 else f"storage_{index}"
                self.clients[db_key] = client
                self.dbs[db_key] = client[self.db_name]
                db_type = "Tracking" if index == 0 else f"Storage {index}"

                masked_uri = re.sub(r"://(.*?):.*?@", r"://\1:*****@", uri)
                masked_uri = masked_uri.split('?')[0]
                
                LOGGER.info(f"{db_type} Database connected successfully: {masked_uri}")

            state = await self.dbs["tracking"]["state"].find_one({"_id": "db_index"})
            if not state:
                await self.dbs["tracking"]["state"].insert_one({"_id": "db_index", "current_index": 1})
                self.current_db_index = 1
            else:
                self.current_db_index = state["current_index"]

            LOGGER.info(f"Active storage DB: storage_{self.current_db_index}")

        except Exception as e:
            LOGGER.error(f"Database connection error: {e}")

    async def disconnect(self):
        """Disconnects from the databases."""
        for client in self.clients.values():
            client.close()
        LOGGER.info("All database connections closed.")

    async def update_current_db_index(self):
        """Updates the current database index in the tracking database."""
        await self.dbs["tracking"]["state"].update_one(
            {"_id": "db_index"},
            {"$set": {"current_index": self.current_db_index}},
            upsert=True
        )


    # =====================================================
    # 🔹 Helper: Sort telegram qualities numerically
    # =====================================================
    def sort_telegram_list(self, telegram_list: list) -> list:
        """Sorts the telegram list by numeric quality value (highest to lowest).

        Args:
            telegram_list (list): The list of telegram qualities to sort.

        Returns:
            list: The sorted list.
        """
        try:
            return sorted(
                telegram_list,
                key=lambda q: int(''.join(filter(str.isdigit, str(q.get("quality", 0)))) or 0),
                reverse=True
            )
        except Exception:
            return telegram_list
            
    # -------------------------------
    # Helper Methods for Repeated Logic
    # -------------------------------
    def _get_sort_dict(self, sort_params: List[Tuple[str, str]]) -> Dict[str, int]:
        """Gets the sort dictionary for a given set of sort parameters.

        Args:
            sort_params (List[Tuple[str, str]]): The sort parameters.

        Returns:
            Dict[str, int]: The sort dictionary.
        """
        if sort_params:
            sort_field, sort_direction = sort_params[0]
            return {sort_field: DESCENDING if sort_direction.lower() == "desc" else ASCENDING}
        return {"updated_on": DESCENDING}

    async def _paginate_collection(
        self,
        collection_name: str,
        sort_dict: Dict[str, int],
        page: int,
        page_size: int,
        filter_dict: Optional[dict] = None
    ):
        """Paginates a collection and returns the results.

        Args:
            collection_name (str): The name of the collection to paginate.
            sort_dict (Dict[str, int]): The sort dictionary.
            page (int): The page number to retrieve.
            page_size (int): The number of items per page.
            filter_dict (Optional[dict], optional): A filter to apply to the query. Defaults to None.

        Returns:
            Tuple[List[dict], List[int], int]: A tuple containing the results, the databases checked, and the total count.
        """
        filter_dict = filter_dict or {}
        skip = (page - 1) * page_size
        results = []
        dbs_checked = []
        total_count = 0

        db_counts = []
        for i in range(1, self.current_db_index + 1):
            db_key = f"storage_{i}"
            db = self.dbs[db_key]
            count = await db[collection_name].count_documents(filter_dict)
            db_counts.append((i, count))
            total_count += count

        start_db_index = None
        for db_index, count in reversed(db_counts):
            if skip < count:
                start_db_index = db_index
                break
            skip -= count

        if not start_db_index:
            return [], [], total_count

        for db_index, count in reversed(db_counts):
            if db_index < start_db_index:
                continue

            db_key = f"storage_{db_index}"
            db = self.dbs[db_key]
            dbs_checked.append(db_index)

            cursor = (
                db[collection_name]
                .find(filter_dict)
                .sort(sort_dict)
                .skip(skip if db_index == start_db_index else 0)
                .limit(page_size - len(results))
            )

            docs = await cursor.to_list(None)
            results.extend(docs)

            if len(results) >= page_size:
                break
        return results, dbs_checked, total_count

    async def _move_document(
        self, collection_name: str, document: dict, old_db_index: int
    ) -> bool:
        """Moves a document from one database to another.

        Args:
            collection_name (str): The name of the collection.
            document (dict): The document to move.
            old_db_index (int): The index of the old database.

        Returns:
            bool: True if the document was moved successfully, False otherwise.
        """
        current_db_key = f"storage_{self.current_db_index}"
        old_db_key = f"storage_{old_db_index}"
        document["db_index"] = self.current_db_index
        try:
            await self.dbs[current_db_key][collection_name].insert_one(document)
            await self.dbs[old_db_key][collection_name].delete_one({"_id": document["_id"]})
            LOGGER.info(f"✅ Moved document {document.get('tmdb_id')} from {old_db_key} to {current_db_key}")
            return True
        except Exception as e:
            LOGGER.error(f"Error moving document to {current_db_key}: {e}")
            return False

    async def _handle_storage_error(self, func, *args, total_storage_dbs: int) -> Optional[Any]:
        """Handles storage errors by switching to the next available database.

        Args:
            func: The function that caused the error.
            *args: The arguments to the function.
            total_storage_dbs (int): The total number of storage databases.

        Returns:
            Optional[Any]: The result of the function call on the new database, or None if all databases are full.
        """
        next_db_index = (self.current_db_index % total_storage_dbs) + 1
        if next_db_index == 1:
            LOGGER.warning("⚠️ All storage databases are full! Add more.")
            return None
        self.current_db_index = next_db_index
        await self.update_current_db_index()
        LOGGER.info(f"Switched to storage_{self.current_db_index}")
        return await func(*args)


    # -------------------------------
    # Multi Database Method for insert/update/delete/list
    # -------------------------------

    async def insert_media(
        self, metadata_info: dict,
        channel: int, msg_id: int, size: str, name: str
    ) -> Optional[ObjectId]:
        """Inserts or updates a media item in the database.

        This method determines whether the media is a movie or a TV show and
        calls the appropriate update method.

        Args:
            metadata_info (dict): A dictionary containing the media's metadata.
            channel (int): The Telegram channel ID.
            msg_id (int): The Telegram message ID.
            size (str): The size of the media file.
            name (str): The name of the media file.

        Returns:
            Optional[ObjectId]: The ObjectId of the inserted or updated media item, or None on failure.
        """
        LOGGER.info(f"\n🧩 [DEBUG] Metadata Info for {name}:\n{metadata_info}\n")
        if metadata_info['media_type'] == "movie":
            media = MovieSchema(
                tmdb_id=metadata_info['tmdb_id'],
                imdb_id=metadata_info['imdb_id'],
                db_index=self.current_db_index,
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                logo=metadata_info['logo'],
                media_type=metadata_info['media_type'],
                telegram=[QualityDetail(
                    quality=metadata_info['quality'],
                    id=metadata_info['encoded_string'],
                    name=name,
                    size=size
                )]
            )
            return await self.update_movie(media)
        else:
            tv_show = TVShowSchema(
                tmdb_id=metadata_info['tmdb_id'],
                imdb_id=metadata_info['imdb_id'],
                db_index=self.current_db_index,
                title=metadata_info['title'],
                genres=metadata_info['genres'],
                description=metadata_info['description'],
                rating=metadata_info['rate'],
                release_year=metadata_info['year'],
                poster=metadata_info['poster'],
                backdrop=metadata_info['backdrop'],
                logo=metadata_info['logo'],
                media_type=metadata_info['media_type'],
                seasons=[Season(
                    season_number=metadata_info['season_number'],
                    episodes=[Episode(
                        episode_number=metadata_info['episode_number'],
                        title=metadata_info['episode_title'],
                        episode_backdrop=metadata_info['episode_backdrop'],
                        telegram=[QualityDetail(
                            quality=metadata_info['quality'],
                            id=metadata_info['encoded_string'],
                            name=name,
                            size=size
                        )]
                    )]
                )]
            )
            return await self.update_tv_show(tv_show)

    
    async def update_movie(self, movie_data: MovieSchema) -> Optional[ObjectId]:
        """Updates a movie in the database.

        If the movie already exists, it updates the existing entry. Otherwise, it inserts a new one.

        Args:
            movie_data (MovieSchema): The movie data to update.

        Returns:
            Optional[ObjectId]: The ObjectId of the updated movie, or None on failure.
        """
        try:
            movie_dict = movie_data.dict()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None
    
        tmdb_id = movie_dict["tmdb_id"]
        title = movie_dict["title"]
        release_year = movie_dict["release_year"]
        quality_to_update = movie_dict["telegram"][0]
        target_quality = quality_to_update["quality"]
        current_db_key = f"storage_{self.current_db_index}"
    
        total_storage_dbs = len(self.dbs) - 1  
        existing_db_key = None
        existing_db_index = None
        existing_movie = None
    
        # 🔍 Check existing movies across all DBs
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            movie = await self.dbs[db_key]["movie"].find_one(
                    {"title": title, "release_year": release_year}
            )
            if movie:
                existing_db_key = db_key
                existing_db_index = db_index
                existing_movie = movie
                break
    
        # 🆕 Insert new movie if not found
        if not existing_movie:
            try:
                movie_dict["db_index"] = self.current_db_index
                result = await self.dbs[current_db_key]["movie"].insert_one(movie_dict)
                return result.inserted_id
            except Exception as e:
                LOGGER.error(f"Insertion failed in {current_db_key}: {e}")
                if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                    return await self._handle_storage_error(self.update_movie, movie_data, total_storage_dbs=total_storage_dbs)
                return None
    
        movie_id = existing_movie["_id"]
        existing_qualities = existing_movie.get("telegram", [])
        matching_quality = next((q for q in existing_qualities if q["quality"] == target_quality), None)
    
        # 🎯 If same quality exists, replace and delete old file
        if matching_quality:
            try:
                old_id = matching_quality.get("id")
                if old_id:
                    decoded_data = await decode_string(old_id)
                    chat_id = int(f"-100{decoded_data['chat_id']}")
                    msg_id = int(decoded_data['msg_id'])
                    create_task(delete_message(chat_id, msg_id))
            except Exception as e:
                LOGGER.error(f"Failed to queue old quality file for deletion: {e}")
            matching_quality.update(quality_to_update)
        else:
            existing_qualities.append(quality_to_update)
    
        # 🔹 Sort qualities before saving
        existing_movie["telegram"] = self.sort_telegram_list(existing_qualities)
        existing_movie["updated_on"] = datetime.utcnow()
    
        # 🔁 Move movie if DB changed
        if existing_db_index != self.current_db_index:
            try:
                if await self._move_document("movie", existing_movie, existing_db_index):
                    return movie_id
            except Exception as e:
                LOGGER.error(f"Error moving movie to {current_db_key}: {e}")
                if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                    return await self._handle_storage_error(self.update_movie, movie_data, total_storage_dbs=total_storage_dbs)
    
        try:
            await self.dbs[existing_db_key]["movie"].replace_one({"_id": movie_id}, existing_movie)
            return movie_id
        except Exception as e:
            LOGGER.error(f"Failed to update movie {tmdb_id} in {existing_db_key}: {e}")
            if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                return await self._handle_storage_error(self.update_movie, movie_data, total_storage_dbs=total_storage_dbs)
            
    async def update_tv_show(self, tv_show_data: TVShowSchema) -> Optional[ObjectId]:
        """Updates a TV show in the database.

        If the TV show already exists, it updates the existing entry. Otherwise, it inserts a new one.

        Args:
            tv_show_data (TVShowSchema): The TV show data to update.

        Returns:
            Optional[ObjectId]: The ObjectId of the updated TV show, or None on failure.
        """
        try:
            tv_show_dict = tv_show_data.dict()
        except ValidationError as e:
            LOGGER.error(f"Validation error: {e}")
            return None
    
        tmdb_id = tv_show_dict["tmdb_id"]
        title = tv_show_dict["title"]
        release_year = tv_show_dict["release_year"]
        current_db_key = f"storage_{self.current_db_index}"
        total_storage_dbs = len(self.dbs) - 1
    
        existing_db_key = None
        existing_db_index = None
        existing_tv = None
    
        # 🔍 Find existing TV show
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            tv = await self.dbs[db_key]["tv"].find_one(
                    {"title": title, "release_year": release_year}
            )
            if tv:
                existing_db_key = db_key
                existing_db_index = db_index
                existing_tv = tv
                break
    
        # 🆕 Insert new TV show
        if not existing_tv:
            try:
                tv_show_dict["db_index"] = self.current_db_index
                result = await self.dbs[current_db_key]["tv"].insert_one(tv_show_dict)
                return result.inserted_id
            except Exception as e:
                LOGGER.error(f"Insertion failed in {current_db_key}: {e}")
                if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                    return await self._handle_storage_error(self.update_tv_show, tv_show_data, total_storage_dbs=total_storage_dbs)
                return None
    
        tv_id = existing_tv["_id"]
    
        # 🔄 Merge seasons & episodes
        for season in tv_show_dict["seasons"]:
            existing_season = next(
                (s for s in existing_tv["seasons"] if s["season_number"] == season["season_number"]), None
            )
            if existing_season:
                for episode in season["episodes"]:
                    existing_episode = next(
                        (e for e in existing_season["episodes"] if e["episode_number"] == episode["episode_number"]), None
                    )
                    if existing_episode:
                        existing_episode.setdefault("telegram", [])
                        for quality in episode["telegram"]:
                            existing_quality = next(
                                (q for q in existing_episode["telegram"]
                                 if q.get("quality") == quality.get("quality")),
                                None
                            )
                            if existing_quality:
                                try:
                                    old_id = existing_quality.get("id")
                                    if old_id:
                                        decoded_data = await decode_string(old_id)
                                        chat_id = int(f"-100{decoded_data['chat_id']}")
                                        msg_id = int(decoded_data['msg_id'])
                                        create_task(delete_message(chat_id, msg_id))
                                except Exception as e:
                                    LOGGER.error(f"Failed to queue old quality file for deletion: {e}")
                                existing_quality.update(quality)
                            else:
                                existing_episode["telegram"].append(quality)
                        # 🔹 Sort telegram qualities for this episode
                        existing_episode["telegram"] = self.sort_telegram_list(existing_episode["telegram"])
                    else:
                        existing_season["episodes"].append(episode)
            else:
                existing_tv["seasons"].append(season)
    
        existing_tv["updated_on"] = datetime.utcnow()
    
        # 🔁 Move if DB index changed
        if existing_db_index != self.current_db_index:
            try:
                if await self._move_document("tv", existing_tv, existing_db_index):
                    return tv_id
            except Exception as e:
                LOGGER.error(f"Error moving TV show to {current_db_key}: {e}")
                if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                    return await self._handle_storage_error(self.update_tv_show, tv_show_data, total_storage_dbs=total_storage_dbs)
            return tv_id
    
        try:
            await self.dbs[existing_db_key]["tv"].replace_one({"_id": tv_id}, existing_tv)
            return tv_id
        except Exception as e:
            LOGGER.error(f"Failed to update TV show {tmdb_id} in {existing_db_key}: {e}")
            if any(keyword in str(e).lower() for keyword in ["storage", "quota"]):
                return await self._handle_storage_error(self.update_tv_show, tv_show_data, total_storage_dbs=total_storage_dbs)
            
    async def sort_movies(self, sort_params, page, page_size, genre_filter=None):
        """Sorts and paginates the movies in the database.

        Args:
            sort_params: The parameters to sort by.
            page (int): The page number to retrieve.
            page_size (int): The number of items per page.
            genre_filter (str, optional): A genre to filter by. Defaults to None.

        Returns:
            dict: A dictionary containing the sorted and paginated movies.
        """
        sort_dict = self._get_sort_dict(sort_params)
        filter_dict = {"genres": {"$in": [genre_filter]}} if genre_filter else {}
        results, dbs_checked, total_count = await self._paginate_collection(
            "movie", sort_dict, page, page_size, filter_dict=filter_dict
        )
        total_pages = (total_count + page_size - 1) // page_size
        return {
            "total_count": total_count,
            "total_pages": total_pages,
            "databases_checked": dbs_checked,
            "current_page": page,
            "movies": [convert_objectid_to_str(result) for result in results],
        }

    async def sort_tv_shows(self, sort_params, page, page_size, genre_filter=None):
        """Sorts and paginates the TV shows in the database.

        Args:
            sort_params: The parameters to sort by.
            page (int): The page number to retrieve.
            page_size (int): The number of items per page.
            genre_filter (str, optional): A genre to filter by. Defaults to None.

        Returns:
            dict: A dictionary containing the sorted and paginated TV shows.
        """
        sort_dict = self._get_sort_dict(sort_params)
        filter_dict = {"genres": {"$in": [genre_filter]}} if genre_filter else {}
        results, dbs_checked, total_count = await self._paginate_collection(
            "tv", sort_dict, page, page_size, filter_dict=filter_dict
        )
        total_pages = (total_count + page_size - 1) // page_size
        return {
            "total_count": total_count,
            "total_pages": total_pages,
            "databases_checked": dbs_checked,
            "current_page": page,
            "tv_shows": [convert_objectid_to_str(result) for result in results],
        }



    async def search_documents(
            self, 
            query: str, 
            page: int, 
            page_size: int
        ) -> dict:
        """Searches for documents in the database.

        Args:
            query (str): The search query.
            page (int): The page number to retrieve.
            page_size (int): The number of items per page.

        Returns:
            dict: A dictionary containing the search results.
        """

            skip = (page - 1) * page_size
            
            words = query.split()
            regex_query = {
                '$regex': '.*' + '.*'.join(words) + '.*', 
                '$options': 'i'
            }
            
            tv_pipeline = [
                {"$match": {"$or": [
                    {"title": regex_query},
                    {"seasons.episodes.telegram.name": regex_query}
                ]}},
                {"$project": {
                    "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1, "imdb_id": 1,
                    "release_year": 1, "poster": 1, "backdrop": 1, "description": 1, "logo": 1,
                    "media_type": 1, "db_index": 1
                }}
            ]
            
            movie_pipeline = [
                {"$match": {"$or": [
                    {"title": regex_query},
                    {"telegram.name": regex_query}
                ]}},
                {"$project": {
                    "_id": 1, "tmdb_id": 1, "title": 1, "genres": 1, "rating": 1,
                    "release_year": 1, "poster": 1, "backdrop": 1, "description": 1,
                    "media_type": 1, "db_index": 1, "imdb_id": 1, "logo": 1
                }}
            ]
            
            results = []
            dbs_checked = []
            
            active_db_key = f"storage_{self.current_db_index}"
            active_db = self.dbs[active_db_key]
            dbs_checked.append(self.current_db_index)
            
            tv_results = await active_db["tv"].aggregate(tv_pipeline).to_list(None)
            movie_results = await active_db["movie"].aggregate(movie_pipeline).to_list(None)
            combined = tv_results + movie_results
            results.extend(combined)
            
            if len(results) < page_size:
                previous_db_index = self.current_db_index - 1
                while previous_db_index > 0 and len(results) < page_size:
                    prev_db_key = f"storage_{previous_db_index}"
                    prev_db = self.dbs[prev_db_key]
                    tv_results_prev = await prev_db["tv"].aggregate(tv_pipeline).to_list(None)
                    movie_results_prev = await prev_db["movie"].aggregate(movie_pipeline).to_list(None)
                    combined_prev = tv_results_prev + movie_results_prev
                    results.extend(combined_prev)
                    dbs_checked.append(previous_db_index)
                    previous_db_index -= 1

            total_count = 0
            for db_index in dbs_checked:
                key = f"storage_{db_index}"
                db = self.dbs[key]
                tv_count = await db["tv"].count_documents({
                    "$or": [
                        {"title": regex_query},
                        {"seasons.episodes.telegram.name": regex_query}
                    ]
                })
                movie_count = await db["movie"].count_documents({
                    "$or": [
                        {"title": regex_query},
                        {"telegram.name": regex_query}
                    ]
                })
                total_count += (tv_count + movie_count)
            
            paged_results = results[skip:skip + page_size]

            return {
                "total_count": total_count,
                "results": [convert_objectid_to_str(doc) for doc in paged_results]
            }


    async def get_media_details(
        self, tmdb_id: int, db_index: int,
        season_number: Optional[int] = None, episode_number: Optional[int] = None
    ) -> Optional[dict]:
        """Gets the details of a media item.

        Args:
            tmdb_id (int): The TMDB ID of the media.
            db_index (int): The database index of the media.
            season_number (Optional[int], optional): The season number (for TV shows). Defaults to None.
            episode_number (Optional[int], optional): The episode number (for TV shows). Defaults to None.

        Returns:
            Optional[dict]: The details of the media item, or None if not found.
        """
        db_key = f"storage_{db_index}"
        if episode_number is not None and season_number is not None:
            tv_show = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
            if not tv_show:
                return None
            for season in tv_show.get("seasons", []):
                if season.get("season_number") == season_number:
                    for episode in season.get("episodes", []):
                        if episode.get("episode_number") == episode_number:
                            details = convert_objectid_to_str(episode)
                            details.update({
                                "tmdb_id": tmdb_id,
                                "type": "tv",
                                "season_number": season_number,
                                "episode_number": episode_number,
                                "backdrop": episode.get("episode_backdrop")
                            })
                            return details
            return None

        elif season_number is not None:
            tv_show = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
            if not tv_show:
                return None
            for season in tv_show.get("seasons", []):
                if season.get("season_number") == season_number:
                    details = convert_objectid_to_str(season)
                    details.update({
                        "tmdb_id": tmdb_id,
                        "type": "tv",
                        "season_number": season_number
                    })
                    return details
            return None

        else:
            tv_doc = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
            if tv_doc:
                tv_doc = convert_objectid_to_str(tv_doc)
                tv_doc["type"] = "tv"
                return tv_doc
            movie_doc = await self.dbs[db_key]["movie"].find_one({"tmdb_id": tmdb_id})
            if movie_doc:
                movie_doc = convert_objectid_to_str(movie_doc)
                movie_doc["type"] = "movie"
                return movie_doc
            return None


    # -------------------------------
    # DB Method for Edit Post
    # -------------------------------


    async def get_document(self, media_type: str, tmdb_id: int, db_index: int) -> Optional[Dict[str, Any]]:
        """Gets a document from the database.

        Args:
            media_type (str): The type of media.
            tmdb_id (int): The TMDB ID of the media.
            db_index (int): The database index of the media.

        Returns:
            Optional[Dict[str, Any]]: The document, or None if not found.
        """
        db_key = f"storage_{db_index}"
        if media_type.lower() in ["tv", "series"]:
            collection_name = "tv"
        else:
            collection_name = "movie"
        document = await self.dbs[db_key][collection_name].find_one({"tmdb_id": int(tmdb_id)})
        return convert_objectid_to_str(document) if document else None

    async def update_document(
        self, media_type: str, tmdb_id: int, db_index: int, update_data: Dict[str, Any]
    ):
        """Updates a document in the database.

        Args:
            media_type (str): The type of media.
            tmdb_id (int): The TMDB ID of the media.
            db_index (int): The database index of the media.
            update_data (Dict[str, Any]): The data to update.

        Returns:
            bool: True if the document was updated successfully, False otherwise.
        """
        update_data.pop('_id', None)
        db_key = f"storage_{db_index}"
        if media_type.lower() in ["tv", "series"]:
            collection_name = "tv"
        else:
            collection_name = "movie"
        collection = self.dbs[db_key][collection_name]

        try:
            result = await collection.update_one({"tmdb_id": int(tmdb_id)}, {"$set": update_data})

            return result.modified_count > 0

        except Exception as e:
            err_str = str(e).lower()
            LOGGER.error(f"Error updating document in {db_key}: {e}")
            if "storage" in err_str or "quota" in err_str:
                total_storage_dbs = len(self.dbs) - 1
                db_index_int = int(db_index)
                next_db_index = (db_index_int % total_storage_dbs) + 1
                if next_db_index == 1:
                    LOGGER.warning("⚠️ All storage databases are full! Add more.")
                    return False

                new_db_key = f"storage_{next_db_index}"
                LOGGER.info(f"Switching from {db_key} to {new_db_key} due to storage error.")

                try:
                    old_doc = await self.dbs[db_key][collection_name].find_one({"tmdb_id": int(tmdb_id)})
                    if not old_doc:
                        LOGGER.error(f"Document with tmdb_id {tmdb_id} not found in {db_key} during migration.")
                        return False

                    old_doc.update(update_data)
                    old_doc["db_index"] = next_db_index
                    old_doc.pop("_id", None)
                    insert_result = await self.dbs[new_db_key][collection_name].insert_one(old_doc)
                    LOGGER.info(f"Inserted document {insert_result.inserted_id} into {new_db_key}")
                    await self.dbs[db_key][collection_name].delete_one({"tmdb_id": int(tmdb_id)})
                    LOGGER.info(f"Deleted document tmdb_id {tmdb_id} from {db_key}")
                    self.current_db_index = next_db_index
                    await self.update_current_db_index()
                    LOGGER.info(f"Switched to {new_db_key} and document migrated successfully.")
                    return True

                except Exception as migrate_error:
                    LOGGER.error(f"Error migrating document tmdb_id {tmdb_id} to {new_db_key}: {migrate_error}")
                    return False
            raise

    # Delete a Movie or Tvshow completely
    async def delete_document(self, media_type: str, tmdb_id: int, db_index: int) -> bool:
        """Deletes a document from the database.

        Args:
            media_type (str): The type of media.
            tmdb_id (int): The TMDB ID of the media.
            db_index (int): The database index of the media.

        Returns:
            bool: True if the document was deleted successfully, False otherwise.
        """
        db_key = f"storage_{db_index}"

        if media_type == "Movie":
            doc = await self.dbs[db_key]["movie"].find_one({"tmdb_id": tmdb_id})
            if doc and "telegram" in doc:
                for quality in doc["telegram"]:
                    try:
                        old_id = quality.get("id")
                        if old_id:
                            decoded_data = await decode_string(old_id)
                            chat_id = int(f"-100{decoded_data['chat_id']}")
                            msg_id = int(decoded_data['msg_id'])
                            create_task(delete_message(chat_id, msg_id))
                    except Exception as e:
                        LOGGER.error(f"Failed to queue file for deletion: {e}")
            
            result = await self.dbs[db_key]["movie"].delete_one({"tmdb_id": tmdb_id})
        else:
            doc = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
            if doc and "seasons" in doc:
                for season in doc["seasons"]:
                    for episode in season.get("episodes", []):
                        for quality in episode.get("telegram", []):
                            try:
                                old_id = quality.get("id")
                                if old_id:
                                    decoded_data = await decode_string(old_id)
                                    chat_id = int(f"-100{decoded_data['chat_id']}")
                                    msg_id = int(decoded_data['msg_id'])
                                    create_task(delete_message(chat_id, msg_id))
                            except Exception as e:
                                LOGGER.error(f"Failed to queue file for deletion: {e}")
            
            result = await self.dbs[db_key]["tv"].delete_one({"tmdb_id": tmdb_id})
        
        if result.deleted_count > 0:
            LOGGER.info(f"{media_type} with tmdb_id {tmdb_id} deleted successfully.")
            return True
        LOGGER.info(f"No document found with tmdb_id {tmdb_id}.")
        return False


    # Delete a specific quality from movie
    async def delete_movie_quality(self, tmdb_id: int, db_index: int, quality: str) -> bool:
        """Deletes a specific quality from a movie.

        Args:
            tmdb_id (int): The TMDB ID of the movie.
            db_index (int): The database index of the movie.
            quality (str): The quality to delete.

        Returns:
            bool: True if the quality was deleted successfully, False otherwise.
        """
        db_key = f"storage_{db_index}"
        movie = await self.dbs[db_key]["movie"].find_one({"tmdb_id": tmdb_id})
        
        if not movie or "telegram" not in movie:
            return False

        for q in movie["telegram"]:
            if q.get("quality") == quality:
                try:
                    old_id = q.get("id")
                    if old_id:
                        decoded_data = await decode_string(old_id)
                        chat_id = int(f"-100{decoded_data['chat_id']}")
                        msg_id = int(decoded_data['msg_id'])
                        create_task(delete_message(chat_id, msg_id))
                except Exception as e:
                    LOGGER.error(f"Failed to queue file for deletion: {e}")
                break
        
        original_len = len(movie["telegram"])
        movie["telegram"] = [q for q in movie["telegram"] if q.get("quality") != quality]
        
        if len(movie["telegram"]) == original_len:
            return False
        
        movie['updated_on'] = datetime.utcnow()
        result = await self.dbs[db_key]["movie"].replace_one({"tmdb_id": tmdb_id}, movie)
        return result.modified_count > 0

    # Delete a specific episode from a TV show
    async def delete_tv_episode(self, tmdb_id: int, db_index: int, season_number: int, episode_number: int) -> bool:
        """Deletes a specific episode from a TV show.

        Args:
            tmdb_id (int): The TMDB ID of the TV show.
            db_index (int): The database index of the TV show.
            season_number (int): The season number of the episode.
            episode_number (int): The episode number to delete.

        Returns:
            bool: True if the episode was deleted successfully, False otherwise.
        """
        db_key = f"storage_{db_index}"
        tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
        
        if not tv or "seasons" not in tv:
            return False
        
        found = False
        for season in tv["seasons"]:
            if season.get("season_number") == season_number:
                for ep in season["episodes"]:
                    if ep.get("episode_number") == episode_number:
                        for quality in ep.get("telegram", []):
                            try:
                                old_id = quality.get("id")
                                if old_id:
                                    decoded_data = await decode_string(old_id)
                                    chat_id = int(f"-100{decoded_data['chat_id']}")
                                    msg_id = int(decoded_data['msg_id'])
                                    create_task(delete_message(chat_id, msg_id))
                            except Exception as e:
                                LOGGER.error(f"Failed to queue file for deletion: {e}")
                        break
                
                original_len = len(season["episodes"])
                season["episodes"] = [ep for ep in season["episodes"] if ep.get("episode_number") != episode_number]
                found = original_len > len(season["episodes"])
                break
        
        if not found:
            return False
        
        tv['updated_on'] = datetime.utcnow()
        result = await self.dbs[db_key]["tv"].replace_one({"tmdb_id": tmdb_id}, tv)
        return result.modified_count > 0

    # Delete a whole season from a TV show
    async def delete_tv_season(self, tmdb_id: int, db_index: int, season_number: int) -> bool:
        """Deletes a whole season from a TV show.

        Args:
            tmdb_id (int): The TMDB ID of the TV show.
            db_index (int): The database index of the TV show.
            season_number (int): The season number to delete.

        Returns:
            bool: True if the season was deleted successfully, False otherwise.
        """
        db_key = f"storage_{db_index}"
        tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
        
        if not tv or "seasons" not in tv:
            return False
        
        for season in tv["seasons"]:
            if season.get("season_number") == season_number:
                for episode in season.get("episodes", []):
                    for quality in episode.get("telegram", []):
                        try:
                            old_id = quality.get("id")
                            if old_id:
                                decoded_data = await decode_string(old_id)
                                chat_id = int(f"-100{decoded_data['chat_id']}")
                                msg_id = int(decoded_data['msg_id'])
                                create_task(delete_message(chat_id, msg_id))
                        except Exception as e:
                            LOGGER.error(f"Failed to queue file for deletion: {e}")
                break
        
        original_len = len(tv["seasons"])
        tv["seasons"] = [s for s in tv["seasons"] if s.get("season_number") != season_number]
        
        if len(tv["seasons"]) == original_len:
            return False
        
        tv['updated_on'] = datetime.utcnow()
        result = await self.dbs[db_key]["tv"].replace_one({"tmdb_id": tmdb_id}, tv)
        return result.modified_count > 0

    # Delete a specific quality from a given TV episode
    async def delete_tv_quality(self, tmdb_id: int, db_index: int, season_number: int, episode_number: int, quality: str) -> bool:
        """Deletes a specific quality from a given TV episode.

        Args:
            tmdb_id (int): The TMDB ID of the TV show.
            db_index (int): The database index of the TV show.
            season_number (int): The season number of the episode.
            episode_number (int): The episode number.
            quality (str): The quality to delete.

        Returns:
            bool: True if the quality was deleted successfully, False otherwise.
        """
        db_key = f"storage_{db_index}"
        tv = await self.dbs[db_key]["tv"].find_one({"tmdb_id": tmdb_id})
        
        if not tv or "seasons" not in tv:
            return False
        
        found = False
        for season in tv["seasons"]:
            if season.get("season_number") == season_number:
                for episode in season["episodes"]:
                    if episode.get("episode_number") == episode_number and "telegram" in episode:
                        for q in episode["telegram"]:
                            if q.get("quality") == quality:
                                try:
                                    old_id = q.get("id")
                                    if old_id:
                                        decoded_data = await decode_string(old_id)
                                        chat_id = int(f"-100{decoded_data['chat_id']}")
                                        msg_id = int(decoded_data['msg_id'])
                                        create_task(delete_message(chat_id, msg_id))
                                except Exception as e:
                                    LOGGER.error(f"Failed to queue file for deletion: {e}")
                                break
                        
                        original_len = len(episode["telegram"])
                        episode["telegram"] = [q for q in episode["telegram"] if q.get("quality") != quality]
                        found = original_len > len(episode["telegram"])
                        break
        
        if not found:
            return False
        tv['updated_on'] = datetime.utcnow()
        result = await self.dbs[db_key]["tv"].replace_one({"tmdb_id": tmdb_id}, tv)
        return result.modified_count > 0

    async def get_media(self, channel: int, msg_id: int) -> Optional[dict]:
        """Checks if a message already exists in the database.

        Args:
            channel (int): The Telegram channel ID.
            msg_id (int): The Telegram message ID.

        Returns:
            Optional[dict]: The media item if it exists, otherwise None.
        """
        total_storage_dbs = len(self.dbs) - 1
        encoded_str = await encode_string({"chat_id": channel, "msg_id": msg_id})

        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            movie = await self.dbs[db_key]["movie"].find_one(
                {"telegram.id": encoded_str},
                {"_id": 1, "tmdb_id": 1, "title": 1, "media_type": 1, "telegram": 1}
            )
            if movie:
                return convert_objectid_to_str(movie)

            tv = await self.dbs[db_key]["tv"].find_one(
                {"seasons.episodes.telegram.id": encoded_str},
                {"_id": 1, "tmdb_id": 1, "title": 1, "media_type": 1, "seasons.episodes.telegram": 1}
            )
            if tv:
                return convert_objectid_to_str(tv)

        return None
                    
    # Get per-DB statistics (movies, tv shows, used size, etc.)
    async def get_database_stats(self):
        """Gets statistics for each database.

        Returns:
            list: A list of dictionaries, where each dictionary contains statistics for a database.
        """
        stats = []
        for key in self.dbs.keys():
            if key.startswith("storage_"):
                db = self.dbs[key]
                movie_count = await db["movie"].count_documents({})
                tv_count = await db["tv"].count_documents({})
                db_stats = await db.command("dbstats")
                stats.append({
                    "db_name": key,
                    "movie_count": movie_count,
                    "tv_count": tv_count,
                    "storageSize": db_stats.get("storageSize", 0),
                    "dataSize": db_stats.get("dataSize", 0)
                })
        return stats
