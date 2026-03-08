from __future__ import annotations

import logging
import platform
import socket
import threading
import time
from typing import TYPE_CHECKING

from flask import Flask, Response, jsonify, request
from polykit import PolyLog

from cbsync.clipboard_backend import write
from cbsync.clipboard_data import ClipboardData
from cbsync.peer_discovery import PeerDiscoveryManager

if TYPE_CHECKING:
    from logging import Logger

    from cbsync.sync_state import ClipboardSyncState


class ClipboardServer:
    """Flask server to receive clipboard updates from other devices."""

    def __init__(
        self,
        port: int,
        shutdown_event: threading.Event,
        sync_state: ClipboardSyncState,
        max_size_mb: int = 10,
    ):
        self.port: int = port
        self.shutdown_event: threading.Event = shutdown_event
        self.sync_state = sync_state
        self.max_size_bytes: int = max_size_mb * 1024 * 1024
        self.app: Flask = Flask(__name__)
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
            self.logger.error("Error processing clipboard update: %s", e)
            return jsonify({"error": str(e)}), 500

    def _process_clipboard_update(self, clipboard_data: ClipboardData, remote_addr: str) -> None:
        """Process a clipboard update with deduplication logic."""
        message_id = clipboard_data.metadata.get("message_id")
        origin_device_id = clipboard_data.metadata.get("origin_device_id")
        sender_device_id = clipboard_data.metadata.get("sender_device_id")
        skip_reason = self.sync_state.inspect_incoming_message(
            message_id=message_id,
            origin_device_id=origin_device_id,
            sender_device_id=sender_device_id,
        )

        if skip_reason == "originated_locally":
            self.logger.debug(
                "Ignoring echoed clipboard %s from %s because it originated locally.",
                clipboard_data.hash,
                sender_device_id or remote_addr,
            )
            return

        if skip_reason == "duplicate_message_id":
            self.logger.debug(
                "Ignoring duplicate clipboard message %s from %s.",
                message_id,
                sender_device_id or remote_addr,
            )
            return

        with self.update_lock:
            if clipboard_data.size_bytes > self.max_size_bytes:
                self.logger.warning(
                    "Ignoring oversized clipboard update from %s (%s): %s bytes.",
                    remote_addr,
                    clipboard_data.kind,
                    clipboard_data.size_bytes,
                )
                return

            if clipboard_data.is_different_from_current_clipboard(
                max_size_bytes=self.max_size_bytes
            ):
                self.sync_state.remember_remote_clipboard(
                    content_hash=clipboard_data.hash,
                    origin_device_id=origin_device_id,
                    message_id=message_id,
                    sender_device_id=sender_device_id,
                )
                try:
                    write(clipboard_data)
                except Exception:
                    self.sync_state.forget_local_suppression(clipboard_data.hash)
                    raise

                self.logger.info(
                    "Clipboard updated from %s (%s): %s bytes.",
                    remote_addr,
                    clipboard_data.kind,
                    clipboard_data.size_bytes,
                )
            else:
                self.logger.debug("Ignoring update; content is already in clipboard.")

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
            "device_id": PeerDiscoveryManager.get_device_id(),
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

    def run(self) -> None:
        """Start the Flask server."""
        self.logger.info("Starting clipboard server on port %s.", self.port)
        try:
            self.app.run(host="0.0.0.0", port=self.port, debug=False, threaded=True)
        except Exception as e:
            if not self.shutdown_event.is_set():
                self.logger.error("Flask server error: %s", e)

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
