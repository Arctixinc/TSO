from asyncio import get_event_loop, sleep as asleep
import asyncio
import logging
from traceback import format_exc
from pyrogram import idle
from Backend import __version__, db
from Backend.helper.pinger import ping
from Backend.logger import LOGGER
from Backend.fastapi import server
from Backend.helper.pyro import restart_notification, setup_bot_commands
from Backend.pyrofork.bot import Helper, StreamBot
from Backend.pyrofork.clients import initialize_clients

# Import new loader components
from Backend.loader import Loader
from Backend.reloader import PluginReloader

loop = get_event_loop()

# Initialize Loader and Reloader
plugin_loader = Loader(plugin_dir="Backend/pyrofork/plugins")
reloader = PluginReloader(plugin_loader)

# Attach reloader to clients so the /reload command can access it
StreamBot.reloader = reloader
Helper.reloader = reloader

async def start_services():
    try:
        LOGGER.info(f"Initializing Telegram-Stremio v-{__version__}")

        # Load Plugins Manually
        LOGGER.info("Loading Plugins...")
        plugin_loader.load_all()
        reloader.initial_scan() # Initialize mtimes
        LOGGER.info("Plugins Loaded Successfully.")

        await asleep(1.2)
        
        await db.connect()
        await asleep(1.2)
        
        await StreamBot.start()
        StreamBot.username = StreamBot.me.username
        LOGGER.info(f"Bot Client : [@{StreamBot.username}]")
        await asleep(1.2)

        await Helper.start()
        Helper.username = Helper.me.username
        LOGGER.info(f"Helper Bot Client : [@{Helper.username}]")
        await asleep(1.2)

        LOGGER.info("Initializing Multi Clients...")
        await initialize_clients()
        await asleep(2)

        await setup_bot_commands(StreamBot)
        await asleep(2)

        LOGGER.info('Initializing Telegram-Stremio Web Server...')
        await restart_notification()
        loop.create_task(server.serve())
        loop.create_task(ping())
        
        LOGGER.info("Telegram-Stremio Started Successfully!")
        await idle()
    except Exception:
        LOGGER.error("Error during startup:\n" + format_exc())

async def stop_services():
    try:
        LOGGER.info("Stopping services...")

        pending_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in pending_tasks:
            task.cancel()
        
        await asyncio.gather(*pending_tasks, return_exceptions=True)

        await StreamBot.stop()
        await Helper.stop()

        await db.disconnect()
        
        LOGGER.info("Services stopped successfully.")
    except Exception:
        LOGGER.error("Error during shutdown:\n" + format_exc())

if __name__ == '__main__':
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        LOGGER.info('Service Stopping...')
    except Exception:
        LOGGER.error(format_exc())
    finally:
        loop.run_until_complete(stop_services())
        loop.stop()
        logging.shutdown()  
