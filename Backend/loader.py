import os
import sys
import importlib
import logging
import glob
from pathlib import Path
from pyrogram import Client
from pyrogram.handlers.handler import Handler
from pyrogram.types import BotCommand
from Backend.pyrofork.bot import StreamBot, Helper

# Logger
reload_logger = logging.getLogger("reloader")

class Loader:
    def __init__(self, plugin_dir="Backend/pyrofork/plugins"):
        self.plugin_dir = plugin_dir
        # Store handlers to allow unloading: module_name -> [(client, handler_obj, group)]
        self.module_handlers = {}
        # Store commands: module_name -> [BotCommand]
        self.module_commands = {}

    def scan_plugin_files(self):
        """Recursively find all .py files in the plugin directory."""
        files = glob.glob(os.path.join(self.plugin_dir, "**", "*.py"), recursive=True)
        return [f for f in files if not f.endswith("__init__.py")]

    def get_module_name(self, file_path):
        """Convert file path to dotted module path."""
        # file_path: Backend/pyrofork/plugins/start.py
        # module: Backend.pyrofork.plugins.start

        # Normalize path separators
        path = os.path.relpath(file_path, os.getcwd())
        name, _ = os.path.splitext(path)
        return name.replace(os.sep, ".")

    def load_all(self):
        """Initial load of all plugins."""
        files = self.scan_plugin_files()
        reload_logger.info(f"Found {len(files)} plugins to load.")
        for f in files:
            try:
                self.load_module(f)
            except Exception as e:
                reload_logger.error(f"Failed to load {f}: {e}")

    def load_module(self, file_path):
        module_name = self.get_module_name(file_path)

        # Prepare tracking
        captured_handlers = []

        # Monkeypatch add_handler on both clients
        original_add_handler_sb = StreamBot.add_handler
        original_add_handler_helper = Helper.add_handler

        def make_tracker(client, original_method):
            def tracker(handler, group=0):
                captured_handlers.append((client, handler, group))
                return original_method(handler, group)
            return tracker

        StreamBot.add_handler = make_tracker(StreamBot, original_add_handler_sb)
        Helper.add_handler = make_tracker(Helper, original_add_handler_helper)

        try:
            if module_name in sys.modules:
                module = sys.modules[module_name]
                importlib.reload(module)
            else:
                module = importlib.import_module(module_name)
        finally:
            # Restore original methods
            StreamBot.add_handler = original_add_handler_sb
            Helper.add_handler = original_add_handler_helper

        # Now look for Unbound Decorators (@Client.on_message) that rely on "Smart Plugin" discovery
        # Pyrogram attaches a 'handlers' attribute to the decorated function.
        for name, value in vars(module).items():
            if hasattr(value, "handlers") and isinstance(value.handlers, list):
                # These are NOT registered yet. We register them to StreamBot by default.
                # NOTE: If a plugin is meant for Helper, it should probably use @Helper.on_message (Bound).
                # If it uses generic @Client.on_message (Unbound), we assume StreamBot.

                for handler_instance, group in value.handlers:
                    if isinstance(handler_instance, Handler):
                        StreamBot.add_handler(handler_instance, group)
                        captured_handlers.append((StreamBot, handler_instance, group))

        # Check for COMMANDS variable (Dynamic Command Registration)
        if hasattr(module, "COMMANDS"):
            cmds = getattr(module, "COMMANDS")
            if isinstance(cmds, list) and all(isinstance(c, BotCommand) for c in cmds):
                self.module_commands[module_name] = cmds
                reload_logger.info(f"Registered {len(cmds)} commands from {module_name}")

        # Save captured handlers for this module
        self.module_handlers[module_name] = captured_handlers
        reload_logger.info(f"Loaded {module_name} with {len(captured_handlers)} handlers")

    def reload_module(self, module_name, file_path):
        """Reloads an existing module using the single load_module logic."""
        self.load_module(file_path)

    def unload_module(self, module_name):
        """Remove handlers associated with this module."""
        # Unload handlers
        if module_name in self.module_handlers:
            handlers = self.module_handlers[module_name]
            for client, handler, group in handlers:
                try:
                    client.remove_handler(handler, group)
                except ValueError:
                    # Handler might have been removed already
                    pass
            del self.module_handlers[module_name]
            reload_logger.info(f"Unloaded handlers for {module_name}")

        # Unload commands
        if module_name in self.module_commands:
            del self.module_commands[module_name]
            reload_logger.info(f"Unloaded commands for {module_name}")
