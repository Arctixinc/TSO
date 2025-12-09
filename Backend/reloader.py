import os
import sys
import time
import subprocess
import importlib
import logging
from datetime import datetime
from pathlib import Path

# Setup logging
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    filename="logs/reload.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
reload_logger = logging.getLogger("reloader")

class PluginReloader:
    def __init__(self, loader):
        self.loader = loader
        self.module_mtimes = {}

    def git_pull(self):
        """Pulls the latest code from git."""
        try:
            reload_logger.info("Starting git pull...")
            result = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                reload_logger.info(f"Git pull successful: {result.stdout}")
                return True, result.stdout
            else:
                reload_logger.error(f"Git pull failed: {result.stderr}")
                return False, result.stderr
        except Exception as e:
            reload_logger.error(f"Git pull exception: {e}")
            return False, str(e)

    def get_module_mtime(self, module_path):
        """Get the last modification time of a module file."""
        try:
            return os.path.getmtime(module_path)
        except OSError:
            return 0

    def reload_changed_modules(self):
        """
        Reloads modules that have changed on disk.
        Returns: (reloaded_modules_list, errors_list)
        """
        reloaded = []
        errors = []

        # 1. Update list of potential modules (scan directory again to find NEW files)
        # We rely on the loader to know the directory
        current_files = self.loader.scan_plugin_files()

        # 2. Check for changes or new files
        for file_path in current_files:
            module_name = self.loader.get_module_name(file_path)
            current_mtime = self.get_module_mtime(file_path)

            # Check if new or modified
            last_mtime = self.module_mtimes.get(module_name)

            if last_mtime is None:
                # New module
                reload_logger.info(f"New module detected: {module_name}")
                try:
                    self.loader.load_module(file_path)
                    self.module_mtimes[module_name] = current_mtime
                    reloaded.append(module_name)
                except Exception as e:
                    err_msg = f"Failed to load new module {module_name}: {e}"
                    reload_logger.error(err_msg)
                    errors.append(err_msg)

            elif current_mtime > last_mtime:
                # Modified module
                reload_logger.info(f"Change detected in {module_name}. Reloading...")
                try:
                    self.loader.unload_module(module_name)
                    self.loader.reload_module(module_name, file_path)
                    self.module_mtimes[module_name] = current_mtime
                    reloaded.append(module_name)
                except Exception as e:
                    err_msg = f"Failed to reload {module_name}: {e}"
                    reload_logger.error(err_msg)
                    errors.append(err_msg)

        return reloaded, errors

    def initial_scan(self):
        """Populate mtimes for currently loaded modules."""
        files = self.loader.scan_plugin_files()
        for f in files:
            name = self.loader.get_module_name(f)
            self.module_mtimes[name] = self.get_module_mtime(f)
