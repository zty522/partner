"""Configuration file watcher for hot-reload support."""

import os
from PySide6.QtCore import QObject, Signal, QFileSystemWatcher

class ConfigWatcher(QObject):
    """Monitors config files for changes and emits signals on modification."""

    config_changed = Signal(str)  # Emits the changed file path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watched_paths: set[str] = set()

    def watch(self, path: str):
        """Start watching a config file or directory."""
        if path and os.path.exists(path) and path not in self._watched_paths:
            self._watcher.addPath(path)
            self._watched_paths.add(path)

    def watch_config_dir(self, config_dir: str):
        """Watch all relevant config files in a directory."""
        if not os.path.isdir(config_dir):
            return
        for fname in ["partner_config.json", "global_config.json", "qq_config.json"]:
            path = os.path.join(config_dir, fname)
            if os.path.exists(path):
                self.watch(path)
        # Also watch agents directory
        agents_dir = os.path.join(config_dir, "agents")
        if os.path.isdir(agents_dir):
            for fname in os.listdir(agents_dir):
                if fname.endswith(".json"):
                    self.watch(os.path.join(agents_dir, fname))

    def _on_file_changed(self, path: str):
        """Emit signal when a file changes."""
        self.config_changed.emit(path)
