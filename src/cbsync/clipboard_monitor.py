from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pyperclip
import requests
from polykit import PolyLog

from cbsync.clipboard_data import ClipboardData

if TYPE_CHECKING:
    from logging import Logger

    from cbsync.peer_discovery import PeerDiscoveryManager


class ClipboardMonitor:
    """Monitors clipboard changes and sends updates to other devices."""

    def __init__(
        self,
        server_port: int,
        discovery_manager: PeerDiscoveryManager | None,
        max_size_mb: int = 10,
    ):
        self.server_port: int = server_port
        self.discovery_manager: PeerDiscoveryManager | None = discovery_manager
        self.max_size_bytes: int = max_size_mb * 1024 * 1024
        self.running: bool = False
        self.last_clipboard_hash: str | None = None
        self.update_lock: threading.Lock = threading.Lock()
        self.session: requests.Session = requests.Session()
        self.logger: Logger = PolyLog.get_logger()

    def get_clipboard_content(self) -> ClipboardData | None:
        """Get current clipboard text content."""
        try:
            text_content = pyperclip.paste()
            if text_content:
                content_bytes = text_content.encode("utf-8")
                if len(content_bytes) <= self.max_size_bytes:
                    return ClipboardData(text_content)
                self.logger.warning("Clipboard text too large: %s bytes.", len(content_bytes))

        except Exception as e:
            self.logger.error("Error reading clipboard: %s", str(e))

        return None

    def send_to_peers(self, clipboard_data: ClipboardData) -> None:
        """Send clipboard data to all peer devices."""
        data = clipboard_data.to_dict()

        # Get current peer list from discovery manager
        peers = self.discovery_manager.get_peers() if self.discovery_manager else []

        if not peers:
            self.logger.debug("No peers available to send clipboard update")
            return

        self.logger.info("Sending clipboard update to %d peers: %s", len(peers), peers)

        for peer in peers:
            try:
                url = f"http://{peer}:{self.server_port}/clipboard"
                self.logger.debug("Sending to %s: %s", peer, url)
                response = self.session.post(
                    url, json=data, headers={"Content-Type": "application/json"}
                )
                if response.status_code == 200:
                    self.logger.info("Successfully sent clipboard to %s.", peer)
                else:
                    self.logger.warning("Failed to send to %s: %s", peer, response.status_code)
            except requests.exceptions.RequestException as e:
                self.logger.warning("Could not reach peer %s: %s", peer, str(e))

    def monitor_clipboard(self, shutdown_event: threading.Event) -> None:
        """Monitor clipboard for changes and send updates."""
        self.logger.debug("Starting clipboard monitoring...")

        while self.running and not shutdown_event.is_set():
            try:
                clipboard_data = self.get_clipboard_content()

                if clipboard_data and clipboard_data.hash != self.last_clipboard_hash:
                    with self.update_lock:  # Double-check after acquiring lock
                        if clipboard_data.hash != self.last_clipboard_hash:
                            self.logger.debug(
                                "Local clipboard changed: %s bytes.",
                                clipboard_data.size,
                            )
                            self.send_to_peers(clipboard_data)
                            self.last_clipboard_hash = clipboard_data.hash

                # Check for shutdown more frequently than clipboard changes
                for _ in range(10):  # Check 10 times per 0.5 seconds
                    if shutdown_event.is_set():
                        break
                    time.sleep(0.05)

            except Exception as e:
                self.logger.error("Error in clipboard monitoring: %s", str(e))
                time.sleep(1)

    def start(self, shutdown_event: threading.Event) -> None:
        """Start clipboard monitoring in background thread."""
        self.running = True
        monitor_thread = threading.Thread(
            target=self.monitor_clipboard, args=(shutdown_event,), daemon=True
        )
        monitor_thread.start()
        self.logger.debug("Clipboard monitor started.")

    def stop(self) -> None:
        """Stop clipboard monitoring."""
        self.running = False
        self.logger.debug("Clipboard monitor stopped.")
