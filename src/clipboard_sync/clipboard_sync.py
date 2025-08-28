#!/usr/bin/env python3

"""Cross-platform clipboard synchronization application.

This application shares clipboard contents (text, images, and files) between devices on the same
network. It runs a server to receive updates and a client to send updates when the local clipboard
changes.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import platform
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pyperclip
import requests
from flask import Flask, Response, jsonify, request
from polykit import PolyArgs, PolyLog
from polykit.cli import handle_interrupt

logger = PolyLog.get_logger()

LOCAL_PREFIXES = [
    "192.168.",
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
]


class ClipboardData:
    """Represents clipboard data with type and content."""

    def __init__(
        self,
        data_type: str,
        content: str | bytes,
        size: int,
        metadata: dict[str, Any] | None = None,
    ):
        self.data_type = data_type
        self.content = content
        self.size = size
        self.metadata = metadata or {}
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
        else:  # Image or file
            # Ensure content is bytes before base64 encoding
            if isinstance(self.content, str):
                content_bytes = self.content.encode("utf-8")
            else:
                content_bytes = self.content
            content = base64.b64encode(content_bytes).decode("utf-8")

        return {
            "type": self.data_type,
            "content": content,
            "size": self.size,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipboardData:
        """Create ClipboardData from dictionary."""
        content = data["content"]
        if data["type"] in {"image", "file"}:
            content = base64.b64decode(content)

        obj = cls(data["type"], content, data["size"], data.get("metadata"))
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
        self.session = requests.Session()

    def get_clipboard_content(self) -> ClipboardData | None:
        """Get current clipboard content if it's text, image, or file."""
        try:
            if platform.system() == "Darwin":  # macOS
                return self._get_macos_clipboard()
            if platform.system() == "Windows":
                return self._get_windows_clipboard()
            # Linux
            return self._get_linux_clipboard()

        except Exception as e:
            logger.error("Error reading clipboard: %s", str(e))

        return None

    def _get_macos_clipboard(self) -> ClipboardData | None:
        """Get clipboard content on macOS."""
        try:
            # Try to get image first
            result = subprocess.run(
                ["osascript", "-e", "the clipboard as «class PNGf»"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # We have image data, let's save it to a temp file and read it
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                        tmp_path = tmp_file.name

                    # Save clipboard image to temp file
                    subprocess.run(
                        ["osascript", "-e", "set the clipboard to (the clipboard as «class PNGf»)"],
                        check=True,
                    )

                    # Read the image file
                    with Path(tmp_path).open("rb") as f:
                        image_data = f.read()

                    # Clean up temp file
                    Path(tmp_path).unlink()

                    if len(image_data) <= self.max_size_bytes:
                        return ClipboardData("image", image_data, len(image_data))
                    logger.warning("Clipboard image too large: %s bytes.", len(image_data))

                except Exception as img_e:
                    logger.debug("Failed to read image from clipboard: %s", str(img_e))

            # Get text content
            text_content = pyperclip.paste()
            if text_content:
                content_bytes = text_content.encode("utf-8")
                if len(content_bytes) <= self.max_size_bytes:
                    return ClipboardData("text", text_content, len(content_bytes))
                logger.warning("Clipboard text too large: %s bytes.", len(content_bytes))

        except Exception as e:
            logger.error("Error reading macOS clipboard: %s", str(e))

        return None

    def _get_windows_clipboard(self) -> ClipboardData | None:
        """Get clipboard content on Windows."""
        try:
            # Try to get image first
            try:
                import win32clipboard  # type: ignore
                import win32con  # type: ignore

                win32clipboard.OpenClipboard()

                # Check if there's an image in the clipboard
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
                    # Get the image data
                    image_data = win32clipboard.GetClipboardData(win32con.CF_DIB)
                    win32clipboard.CloseClipboard()

                    if len(image_data) <= self.max_size_bytes:
                        return ClipboardData("image", image_data, len(image_data))
                    logger.warning("Clipboard image too large: %s bytes.", len(image_data))
                    return None

                win32clipboard.CloseClipboard()

            except ImportError:
                logger.debug("win32clipboard not available, skipping image support")
            except Exception as img_e:
                logger.debug("Failed to read image from Windows clipboard: %s", str(img_e))

            # Get text content
            text_content = pyperclip.paste()
            if text_content:
                content_bytes = text_content.encode("utf-8")
                if len(content_bytes) <= self.max_size_bytes:
                    return ClipboardData("text", text_content, len(content_bytes))
                logger.warning("Clipboard text too large: %s bytes.", len(content_bytes))

        except Exception as e:
            logger.error("Error reading Windows clipboard: %s", str(e))

        return None

    def _get_linux_clipboard(self) -> ClipboardData | None:
        """Get clipboard content on Linux."""
        try:
            # Try to get text content
            text_content = pyperclip.paste()
            if text_content:
                content_bytes = text_content.encode("utf-8")
                if len(content_bytes) <= self.max_size_bytes:
                    return ClipboardData("text", text_content, len(content_bytes))
                logger.warning("Clipboard text too large: %s bytes.", len(content_bytes))

        except Exception as e:
            logger.error("Error reading Linux clipboard: %s", str(e))

        return None

    def send_to_peers(self, clipboard_data: ClipboardData) -> None:
        """Send clipboard data to all peer devices."""
        data = clipboard_data.to_dict()

        for peer in self.peers:
            try:
                url = f"http://{peer}:{self.server_port}/clipboard"
                response = self.session.post(
                    url, json=data, headers={"Content-Type": "application/json"}
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
                            logger.debug(
                                "Local clipboard changed: %s (%s bytes).",
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


monitor: ClipboardMonitor | None = None


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
                            "Clipboard updated from %s: %s (%s bytes).",
                            request.remote_addr,
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
            reported_platform = platform.system()
            if reported_platform == "Darwin":
                reported_platform = "macOS"
            return jsonify({"status": "healthy", "platform": reported_platform}), 200

        @self.app.route("/discover", methods=["GET"])
        def discover() -> tuple[Response, int]:  # type: ignore[reportUnusedFunction]
            """Endpoint for network discovery."""
            return jsonify({
                "status": "available",
                "platform": platform.system(),
                "hostname": socket.gethostname(),
                "port": self.port,
            }), 200

    def _set_clipboard(self, clipboard_data: ClipboardData) -> None:
        """Set the local clipboard content."""
        try:
            if clipboard_data.data_type == "text":
                pyperclip.copy(clipboard_data.content)
            elif clipboard_data.data_type == "image":
                self._set_image_clipboard(clipboard_data)
            elif clipboard_data.data_type == "file":
                self._set_file_clipboard(clipboard_data)
            else:
                logger.warning("Unknown clipboard type: %s", clipboard_data.data_type)

        except Exception as e:
            logger.error("Error setting clipboard: %s", str(e))

    def _set_image_clipboard(self, clipboard_data: ClipboardData) -> None:
        """Set image clipboard content."""
        if platform.system() == "Darwin":  # macOS
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                    if isinstance(clipboard_data.content, str):
                        tmp_file.write(clipboard_data.content.encode("utf-8"))
                    else:
                        tmp_file.write(clipboard_data.content)
                    tmp_path = tmp_file.name

                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f'set the clipboard to (read (POSIX file "{tmp_path}") as «class PNGf»)',
                    ],
                    check=True,
                )

                # Clean up temp file
                Path(tmp_path).unlink()
                logger.info("Image set to clipboard on macOS")

            except Exception as e:
                logger.error("Error setting image clipboard on macOS: %s", str(e))

        elif platform.system() == "Windows":  # Windows
            try:
                import win32clipboard  # type: ignore
                import win32con  # type: ignore

                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()

                # Set the image data to clipboard
                win32clipboard.SetClipboardData(win32con.CF_DIB, clipboard_data.content)
                win32clipboard.CloseClipboard()

                logger.info("Image set to clipboard on Windows")

            except ImportError:
                logger.warning(
                    "win32clipboard not available, cannot set image clipboard on Windows"
                )
            except Exception as e:
                logger.error("Error setting image clipboard on Windows: %s", str(e))
        else:
            logger.info("Image clipboard not implemented for this platform")

    def _set_file_clipboard(self, clipboard_data: ClipboardData) -> None:
        """Set file clipboard content."""
        try:
            filename = clipboard_data.metadata.get("filename", "clipboard_file")
            temp_dir = Path(tempfile.gettempdir()) / "clipboard_sync"
            temp_dir.mkdir(exist_ok=True)

            file_path = temp_dir / filename
            with file_path.open("wb") as f:
                if isinstance(clipboard_data.content, str):
                    f.write(clipboard_data.content.encode("utf-8"))
                else:
                    f.write(clipboard_data.content)

            logger.info("File saved to: %s", file_path)

        except Exception as e:
            logger.error("Error handling file clipboard: %s", str(e))

    def run(self) -> None:
        """Start the Flask server."""
        logger.info("Starting clipboard server on port %s.", self.port)
        self.app.run(host="0.0.0.0", port=self.port, debug=False, threaded=True)


def _get_all_interfaces() -> list[str]:
    """Get all available network interface IPs."""
    interfaces = []

    try:
        import netifaces

        for interface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addrs:
                for addr_info in addrs[netifaces.AF_INET]:
                    ip = addr_info["addr"]
                    if not ip.startswith("127.") and not ip.startswith("169.254."):
                        interfaces.append(ip)  # Skip loopback and link-local addresses
    except ImportError:
        try:  # Fallback to socket method if netifaces not available
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                interfaces.append(local_ip)
        except Exception:
            pass

    return interfaces


def _get_preferred_interface(interfaces: list[str]) -> str | None:
    """Get the preferred interface from a list of available interfaces."""
    for ip in interfaces:
        for prefix in LOCAL_PREFIXES:
            if ip.startswith(prefix):
                network_parts = ip.split(".")
                if len(network_parts) == 4:
                    return ip

    # If no preferred prefix found, use the first interface
    if interfaces:
        ip = interfaces[0]
        network_parts = ip.split(".")
        if len(network_parts) == 4:
            return ip

    return None


def get_current_ip() -> str | None:
    """Get the current IP address for this device."""
    interfaces = _get_all_interfaces()
    if not interfaces:
        return None

    return _get_preferred_interface(interfaces)


def get_local_network_prefix() -> str | None:
    """Get the local network prefix for discovery."""
    if not (interfaces := _get_all_interfaces()):
        logger.warning("Could not determine local IP address.")
        return None

    preferred_ip = _get_preferred_interface(interfaces)
    if not preferred_ip:
        logger.warning("No valid network interface found.")
        return None

    logger.info("Using network interface: %s", preferred_ip)
    network_parts = preferred_ip.split(".")
    return ".".join(network_parts[:3]) + "."


def _check_host_for_clipboard_sync(ip: str, port: int, timeout: float, peers: list[str]) -> None:
    """Check if a specific IP is running clipboard sync."""
    try:
        response = requests.get(f"http://{ip}:{port}/discover", timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "available":
                peers.append(ip)
                logger.info("Found peer: %s (%s)", ip, data.get("hostname", "unknown"))
    except Exception:
        pass  # Host not reachable or not running clipboard sync


def _scan_network_range(network_prefix: str, port: int, timeout: float, peers: list[str]) -> None:
    """Scan a network range for clipboard sync instances."""
    threads = []
    for i in range(1, 255):
        ip = f"{network_prefix}{i}"
        thread = threading.Thread(
            target=_check_host_for_clipboard_sync, args=(ip, port, timeout, peers), daemon=True
        )
        threads.append(thread)
        thread.start()

        # Limit concurrent threads
        if len(threads) >= 50:
            for t in threads:
                t.join(timeout=0.1)
            threads = []

    # Wait for remaining threads
    for thread in threads:
        thread.join(timeout=0.1)


def _get_network_prefix(interface_ip: str | None) -> str | None:
    """Get network prefix from interface IP or auto-detect."""
    if interface_ip:  # Use manually specified interface
        network_parts = interface_ip.split(".")
        if len(network_parts) == 4:
            network_prefix = ".".join(network_parts[:3]) + "."
            logger.info("Using specified network interface: %s", interface_ip)
            return network_prefix
        logger.error("Invalid interface IP format: %s", interface_ip)
        return None

    return get_local_network_prefix()


def discover_peers(port: int, timeout: float = 2.0, interface_ip: str | None = None) -> list[str]:
    """Discover other clipboard sync instances on the network."""
    peers = []

    if not (network_prefix := _get_network_prefix(interface_ip)):
        return peers

    logger.info("Scanning network %s* for clipboard sync instances...", network_prefix)

    for attempt in range(10):  # Try multiple times to account for timing issues
        if attempt > 0:
            logger.debug("Retry attempt %d of 10...", attempt + 1)
            time.sleep(1)

        _scan_network_range(network_prefix, port, timeout, peers)

        if peers:  # If we found peers, we can stop early
            break

    if not peers:
        logger.info("No peers found after 10 attempts.")

    return peers


def shutdown() -> None:
    """Shutdown the application."""
    logger.info("Shutting down...")
    if monitor:
        monitor.stop()


@handle_interrupt(callback=shutdown)
def main():
    """Main application entry point."""
    parser = PolyArgs(description="Cross-platform clipboard sync")
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="port for the clipboard server (default: 8765)",
    )
    parser.add_argument(
        "--peers",
        nargs="*",
        default=[],
        help="IP addresses of peer devices",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=10,
        help="maximum clipboard size in MB (default: 10)",
    )
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="run as server only (no clipboard monitoring)",
    )

    parser.add_argument(
        "--interface",
        type=str,
        help="specify network interface IP (e.g., 192.168.1.100) for discovery",
    )

    args = parser.parse_args()

    # Start the server first so other devices can discover us
    server = ClipboardServer(args.port)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Give the server a moment to start
    time.sleep(1)

    # Auto-discover peers if no peers specified and not server-only mode
    if not args.peers and not args.server_only:
        logger.info("No peers specified. Discovering peers on the network...")
        discovered_peers = discover_peers(args.port, interface_ip=args.interface)
        if discovered_peers:
            args.peers = discovered_peers
            logger.info("Using discovered peers: %s", ", ".join(discovered_peers))
        else:
            logger.warning("No peers discovered. You may need to specify peers manually.")
            logger.error("Please specify peer IP addresses with --peers or use --server-only.")
            logger.error("Example: python clipboard_sync.py --peers 192.168.1.100 192.168.1.101")
            return

    # Start clipboard monitoring if not server-only mode
    if not args.server_only and args.peers:
        monitor = ClipboardMonitor(args.port, args.peers, args.max_size)
        monitor.start()

    # Log current IP address for easy peer configuration
    current_ip = get_current_ip()
    if current_ip:
        logger.info("This device's IP address: %s", current_ip)
        logger.info("Other devices can connect using: --peers %s", current_ip)
    else:
        logger.warning("Could not determine this device's IP address")

    logger.info("Clipboard sync is running. Press Ctrl+C to stop.")
    reported_platform = platform.system()
    if reported_platform == "Darwin":
        reported_platform = "macOS"
    logger.info("Platform: %s", reported_platform)
    logger.info("Server port: %s", args.port)
    if args.peers:
        logger.info("Peers: %s", ", ".join(args.peers))

    # Keep the main thread alive
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
