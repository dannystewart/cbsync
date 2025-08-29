from __future__ import annotations

import logging
import platform
import socket
import threading
import time
from typing import TYPE_CHECKING

import pyperclip
from flask import Flask, Response, jsonify, request
from polykit import PolyLog

from cbsync.clipboard_data import ClipboardData
from cbsync.net_utils import get_device_id

if TYPE_CHECKING:
    from logging import Logger


class ClipboardServer:
    """Flask server to receive clipboard updates from other devices."""

    def __init__(self, port: int, shutdown_event: threading.Event):
        self.port: int = port
        self.shutdown_event: threading.Event = shutdown_event
        self.app: Flask = Flask(__name__)
        self.last_received_hash: str | None = None
        self.update_lock: threading.Lock = threading.Lock()
        self.server_thread: threading.Thread | None = None
        self.logger: Logger = PolyLog.get_logger()

        # Suppress Flask's request logging for cleaner output
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup Flask routes."""
        self.app.route("/clipboard", methods=["POST"])(self._handle_clipboard_update)
        self.app.route("/health", methods=["GET"])(self._handle_health_check)
        self.app.route("/discover", methods=["GET"])(self._handle_discover)
        self.app.route("/shutdown", methods=["POST"])(self._handle_shutdown)

    def _handle_clipboard_update(self) -> tuple[Response, int]:
        """Handle clipboard update requests."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400

            clipboard_data = ClipboardData.from_dict(data)
            remote_addr = request.remote_addr or "unknown"
            self._process_clipboard_update(clipboard_data, remote_addr)
            return jsonify({"status": "success"}), 200

        except Exception as e:
            self.logger.error("Error processing clipboard update: %s", str(e))
            return jsonify({"error": str(e)}), 500

    def _process_clipboard_update(self, clipboard_data: ClipboardData, remote_addr: str) -> None:
        """Process a clipboard update with deduplication logic."""
        with self.update_lock:
            if clipboard_data.hash != self.last_received_hash:
                if clipboard_data.is_different_from_current_clipboard():
                    self._set_clipboard(clipboard_data)
                    self.last_received_hash = clipboard_data.hash
                    self.logger.info(
                        "Clipboard updated from %s: %s bytes.",
                        remote_addr,
                        clipboard_data.size,
                    )
                else:
                    self.logger.debug("Ignoring update; content is already in clipboard.")
                    # Still update the hash to avoid repeated checks
                    self.last_received_hash = clipboard_data.hash
            else:
                self.logger.debug("Ignoring duplicate clipboard update")

    def _handle_health_check(self) -> tuple[Response, int]:
        """Handle health check requests."""
        reported_platform = platform.system()
        if reported_platform == "Darwin":
            reported_platform = "macOS"
        return jsonify({"status": "healthy", "platform": reported_platform}), 200

    def _handle_discover(self) -> tuple[Response, int]:
        """Handle discovery requests."""
        return jsonify({
            "status": "available",
            "platform": platform.system(),
            "hostname": socket.gethostname(),
            "port": self.port,
            "device_id": get_device_id(),
        }), 200

    def _handle_shutdown(self) -> tuple[Response, int]:
        """Handle shutdown requests."""

        def shutdown_server_internal():
            time.sleep(0.5)  # Give time for response to be sent
            self.shutdown_event.set()  # Signal shutdown to main thread

        # Start shutdown in a separate thread to allow response to be sent
        shutdown_thread = threading.Thread(target=shutdown_server_internal, daemon=True)
        shutdown_thread.start()
        return jsonify({"status": "shutting down"}), 200

    def _set_clipboard(self, clipboard_data: ClipboardData) -> None:
        """Set the local clipboard content."""
        try:  # Use the raw content to preserve original formatting
            pyperclip.copy(clipboard_data.raw_content)
        except Exception as e:
            self.logger.error("Error setting clipboard: %s", str(e))

    def run(self) -> None:
        """Start the Flask server."""
        self.logger.info("Starting clipboard server on port %s.", self.port)
        try:
            self.app.run(host="0.0.0.0", port=self.port, debug=False, threaded=True)
        except Exception as e:
            if not self.shutdown_event.is_set():
                self.logger.error("Flask server error: %s", str(e))

    def start(self) -> None:
        """Start the server in a background thread."""
        self.server_thread = threading.Thread(target=self.run, daemon=True)
        self.server_thread.start()

    def stop(self) -> None:
        """Stop the Flask server."""
        self.logger.info("Stopping clipboard server...")
        try:
            # Shutdown Flask server gracefully
            import requests

            requests.post(f"http://localhost:{self.port}/shutdown", timeout=1)
        except Exception:
            # If graceful shutdown fails, the server will exit when the thread is terminated
            pass

        # Wait for server thread to finish
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2)
            if self.server_thread.is_alive():
                self.logger.warning("Server thread did not stop gracefully")
