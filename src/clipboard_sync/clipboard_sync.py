#!/usr/bin/env python3

"""Cross-platform clipboard synchronization application.

This application shares clipboard contents (text and images) between devices on the same network.
It runs a server to receive updates and a client to send updates when the local clipboard changes.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import platform
import threading
import time
from typing import Any

import pyperclip
import requests
from flask import Flask, Response, jsonify, request
from polykit import PolyLog

logger = PolyLog.get_logger()


class ClipboardData:
    """Represents clipboard data with type and content."""

    def __init__(self, data_type: str, content: str | bytes, size: int):
        self.data_type = data_type
        self.content = content
        self.size = size
        self.timestamp = time.time()
        self.hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate hash of the content for deduplication."""
        if isinstance(self.content, str):
            content_bytes = self.content.encode("utf-8")
        else:
            content_bytes = self.content
        return hashlib.sha256(content_bytes).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        if self.data_type == "text":
            content = self.content
        else:  # image
            content = base64.b64encode(self.content).decode("utf-8")

        return {
            "type": self.data_type,
            "content": content,
            "size": self.size,
            "timestamp": self.timestamp,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipboardData:
        """Create ClipboardData from dictionary."""
        content = data["content"]
        if data["type"] == "image":
            content = base64.b64decode(content)

        obj = cls(data["type"], content, data["size"])
        obj.timestamp = data["timestamp"]
        obj.hash = data["hash"]
        return obj


class ClipboardMonitor:
    """Monitors clipboard changes and sends updates to other devices."""

    def __init__(self, server_port: int, peers: list[str], max_size_mb: int = 10):
        self.server_port = server_port
        self.peers = peers
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.running = False
        self.last_clipboard_hash: str | None = None
        self.update_lock = threading.Lock()

    def get_clipboard_content(self) -> ClipboardData | None:
        """Get current clipboard content if it's text or image."""
        try:
            # Try to get image first (if available)
            if platform.system() == "Darwin":  # macOS
                # On macOS, try to get image from clipboard
                try:
                    import subprocess

                    result = subprocess.run(
                        ["osascript", "-e", "the clipboard as «class PNGf»"],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        # We have image data, but for simplicity, we'll focus on text
                        pass
                except Exception:
                    pass

            # Get text content
            text_content = pyperclip.paste()
            if text_content:
                content_bytes = text_content.encode("utf-8")
                if len(content_bytes) <= self.max_size_bytes:
                    return ClipboardData("text", text_content, len(content_bytes))
                logger.warning("Clipboard text too large: %s bytes.", len(content_bytes))

        except Exception as e:
            logger.error("Error reading clipboard: %s", str(e))

        return None

    def send_to_peers(self, clipboard_data: ClipboardData) -> None:
        """Send clipboard data to all peer devices."""
        data = clipboard_data.to_dict()

        for peer in self.peers:
            try:
                url = f"http://{peer}:{self.server_port}/clipboard"
                response = requests.post(
                    url, json=data, timeout=3, headers={"Content-Type": "application/json"}
                )
                if response.status_code == 200:
                    logger.info("Successfully sent clipboard to %s.", peer)
                else:
                    logger.warning("Failed to send to %s: %s", peer, response.status_code)
            except requests.exceptions.RequestException as e:
                logger.debug("Could not reach peer %s: %s", peer, str(e))

    def monitor_clipboard(self) -> None:
        """Monitor clipboard for changes and send updates."""
        logger.info("Starting clipboard monitoring...")

        while self.running:
            try:
                clipboard_data = self.get_clipboard_content()

                if clipboard_data and clipboard_data.hash != self.last_clipboard_hash:
                    with self.update_lock:  # Double-check after acquiring lock
                        if clipboard_data.hash != self.last_clipboard_hash:
                            logger.info(
                                "Clipboard changed: %s (%s bytes).",
                                clipboard_data.data_type,
                                clipboard_data.size,
                            )
                            self.send_to_peers(clipboard_data)
                            self.last_clipboard_hash = clipboard_data.hash

                time.sleep(0.5)  # Check every 500ms

            except Exception as e:
                logger.error("Error in clipboard monitoring: %s", str(e))
                time.sleep(1)

    def start(self) -> None:
        """Start clipboard monitoring in background thread."""
        self.running = True
        monitor_thread = threading.Thread(target=self.monitor_clipboard, daemon=True)
        monitor_thread.start()
        logger.info("Clipboard monitor started.")

    def stop(self) -> None:
        """Stop clipboard monitoring."""
        self.running = False
        logger.info("Clipboard monitor stopped.")


class ClipboardServer:
    """Flask server to receive clipboard updates from other devices."""

    def __init__(self, port: int):
        self.port = port
        self.app = Flask(__name__)
        self.last_received_hash: str | None = None
        self.update_lock = threading.Lock()

        # Suppress Flask's request logging for cleaner output
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup Flask routes."""

        @self.app.route("/clipboard", methods=["POST"])
        def receive_clipboard() -> tuple[Response, int]:  # type: ignore[reportUnusedFunction]
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "No data provided"}), 400

                clipboard_data = ClipboardData.from_dict(data)

                with self.update_lock:  # Avoid setting clipboard if it's already what we just sent
                    if clipboard_data.hash != self.last_received_hash:
                        self._set_clipboard(clipboard_data)
                        self.last_received_hash = clipboard_data.hash
                        logger.info(
                            "Updated clipboard: %s (%s bytes).",
                            clipboard_data.data_type,
                            clipboard_data.size,
                        )
                    else:
                        logger.debug("Ignoring duplicate clipboard update")

                return jsonify({"status": "success"}), 200

            except Exception as e:
                logger.error("Error processing clipboard update: %s", str(e))
                return jsonify({"error": str(e)}), 500

        @self.app.route("/health", methods=["GET"])
        def health_check() -> tuple[Response, int]:  # type: ignore[reportUnusedFunction]
            return jsonify({"status": "healthy", "platform": platform.system()}), 200

    def _set_clipboard(self, clipboard_data: ClipboardData) -> None:
        """Set the local clipboard content."""
        try:
            if clipboard_data.data_type == "text":
                pyperclip.copy(clipboard_data.content)
            else:  # TODO: Implement image support
                logger.info("Received image data (image clipboard not implemented)")

        except Exception as e:
            logger.error("Error setting clipboard: %s", str(e))

    def run(self) -> None:
        """Start the Flask server."""
        logger.info("Starting clipboard server on port %s.", self.port)
        self.app.run(host="0.0.0.0", port=self.port, debug=False, threaded=True)


def main():
    """Main application entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Cross-platform clipboard sync")
    parser.add_argument(
        "--port", type=int, default=8765, help="Port for the clipboard server (default: 8765)"
    )
    parser.add_argument("--peers", nargs="*", default=[], help="IP addresses of peer devices")
    parser.add_argument(
        "--max-size", type=int, default=10, help="Maximum clipboard size in MB (default: 10)"
    )
    parser.add_argument(
        "--server-only", action="store_true", help="Run as server only (no clipboard monitoring)"
    )

    args = parser.parse_args()

    if not args.peers and not args.server_only:
        logger.error("Please specify peer IP addresses with --peers or use --server-only.")
        logger.error("Example: python clipboard_sync.py --peers 192.168.1.100 192.168.1.101")
        return

    # Start the server in a background thread
    server = ClipboardServer(args.port)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Give the server a moment to start
    time.sleep(1)

    # Start clipboard monitoring if not server-only mode
    monitor = None
    if not args.server_only and args.peers:
        monitor = ClipboardMonitor(args.port, args.peers, args.max_size)
        monitor.start()

    try:
        logger.info("Clipboard sync is running. Press Ctrl+C to stop.")
        logger.info("Platform: %s", platform.system())
        logger.info("Server port: %s", args.port)
        if args.peers:
            logger.info("Peers: %s", ", ".join(args.peers))

        # Keep the main thread alive
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if monitor:
            monitor.stop()


if __name__ == "__main__":
    main()
