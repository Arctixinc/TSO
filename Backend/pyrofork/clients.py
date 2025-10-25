from typing import Dict, Tuple, Optional
from asyncio import gather, create_task, sleep
from pyrogram import Client
from pyrogram.errors import AccessTokenExpired, FloodWait
from Backend.logger import LOGGER
from Backend.config import Telegram
from Backend.pyrofork.bot import multi_clients, work_loads, StreamBot
from os import environ

class TokenParser:
    @staticmethod
    def parse_from_env():
        tokens = {
            c + 1: t
            for c, (_, t) in enumerate(
                filter(
                    lambda n: n[0].startswith("MULTI_TOKEN"), 
                    sorted(environ.items())
                )
            )
        }
        return tokens

async def start_client(client_id: int, token: str) -> Optional[Tuple[int, Client]]:
    try:
        LOGGER.info(f"[Client {client_id}] Starting initialization...")
        client = Client(
            name=str(client_id),
            api_id=Telegram.API_ID,
            api_hash=Telegram.API_HASH,
            bot_token=token,
            sleep_threshold=120,
            no_updates=True,
            in_memory=True
        )
        await client.start()
        work_loads[client_id] = 0
        # LOGGER.info(f"[Client {client_id}] Started successfully.")
        return client_id, client

    except AccessTokenExpired:
        LOGGER.warning(f"[Client {client_id}] Token has expired — skipping.")
        return None
    except FloodWait as e:
        LOGGER.warning(f"[Client {client_id}] FloodWait: waiting {e.value}s before retrying...")
        await sleep(e.value)
        try:
            await client.start()
            work_loads[client_id] = 0
            LOGGER.info(f"[Client {client_id}] Started successfully after FloodWait.")
            return client_id, client
        except Exception as err:
            LOGGER.error(f"[Client {client_id}] Retry after FloodWait failed: {err}", exc_info=True)
            return None
    except Exception as e:
        LOGGER.error(f"[Client {client_id}] Failed to start — {e}", exc_info=True)
        return None

async def initialize_clients():
    multi_clients[0], work_loads[0] = StreamBot, 0
    all_tokens = TokenParser.parse_from_env()
    
    if not all_tokens:
        LOGGER.info("No additional Bot Clients found, using default client")
        return

    tasks = [create_task(start_client(i, token)) for i, token in all_tokens.items()]
    results = await gather(*tasks, return_exceptions=True)

    clients = {}
    failed_clients = []

    for idx, result in enumerate(results, start=1):
        token_index = list(all_tokens.keys())[idx-1]  # Keep track of token index
        if isinstance(result, Exception):
            LOGGER.error(f"Client {token_index} task failed with exception: {result}")
            failed_clients.append(token_index)
        elif result is None:
            failed_clients.append(token_index)
        else:
            client_id, client = result
            clients[client_id] = client

    multi_clients.update(clients)

    if clients:
        LOGGER.info(f"Successfully started clients: {list(clients.keys())}")
    if failed_clients:
        LOGGER.warning(f"Failed to start clients (check tokens): {failed_clients}")

    if len(multi_clients) > 1:
        LOGGER.info(f"Multi-Client Mode Enabled with {len(multi_clients)} clients")
    else:
        LOGGER.info("No additional clients were initialized, using default client")
