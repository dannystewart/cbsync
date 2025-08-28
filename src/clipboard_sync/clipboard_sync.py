#!/usr/bin/env python3

"""Cross-platform clipboard synchronization application.

This application shares clipboard text content between devices on the same network. It runs a server
to receive updates and a client to send updates when the local clipboard changes.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import socket
import threading
import time
import uuid
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
    """Represents clipboard text data."""

    def __init__(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ):
        self.content = content
        self.size = len(content.encode("utf-8"))
        self.metadata = metadata or {}
        self.timestamp = time.time()
        self.hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate hash of the content for deduplication."""
        content_bytes = self.content.encode("utf-8")
        return hashlib.sha256(content_bytes).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "content": self.content,
            "size": self.size,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipboardData:
        """Create ClipboardData from dictionary."""
        obj = cls(data["content"], data.get("metadata"))
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
        """Get current clipboard text content."""
        try:
            text_content = pyperclip.paste()
            if text_content:
                content_bytes = text_content.encode("utf-8")
                if len(content_bytes) <= self.max_size_bytes:
                    return ClipboardData(text_content)
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
                                "Local clipboard changed: %s bytes.",
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
                            "Clipboard updated from %s: %s bytes.",
                            request.remote_addr,
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
                "device_id": get_device_id(),
            }), 200

    def _set_clipboard(self, clipboard_data: ClipboardData) -> None:
        """Set the local clipboard content."""
        try:
            pyperclip.copy(clipboard_data.content)
        except Exception as e:
            logger.error("Error setting clipboard: %s", str(e))

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


def get_device_id() -> str:
    """Generate a unique device identifier."""
    hostname = socket.gethostname()
    # Use a deterministic UUID based on hostname to ensure same device gets same ID
    device_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, hostname)
    return f"{hostname}-{device_uuid.hex[:8]}"


def _check_host_for_clipboard_sync(
    ip: str, port: int, timeout: float, peers: list[str], seen_devices: set[str], our_device_id: str
) -> None:
    """Check if a specific IP is running clipboard sync."""
    try:
        response = requests.get(f"http://{ip}:{port}/discover", timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "available":
                device_id = data.get("device_id", f"{data.get('hostname', 'unknown')}-{ip}")

                # Don't add ourselves to the peer list!
                if device_id == our_device_id:
                    logger.debug("Skipping our own device: %s", device_id)
                    return

                # Only add if we haven't seen this device before
                if device_id not in seen_devices:
                    seen_devices.add(device_id)
                    peers.append(ip)
                    logger.info("Found peer: %s (%s)", ip, data.get("hostname", "unknown"))
    except Exception:
        pass  # Host not reachable or not running clipboard sync


def _scan_network_range(
    network_prefix: str,
    port: int,
    timeout: float,
    peers: list[str],
    seen_devices: set[str],
    our_device_id: str,
) -> None:
    """Scan a network range for clipboard sync instances."""
    threads = []
    for i in range(1, 255):
        ip = f"{network_prefix}{i}"
        thread = threading.Thread(
            target=_check_host_for_clipboard_sync,
            args=(ip, port, timeout, peers, seen_devices, our_device_id),
            daemon=True,
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
    seen_devices = set()
    our_device_id = get_device_id()

    if not (network_prefix := _get_network_prefix(interface_ip)):
        return peers

    logger.info("Scanning network %s* for clipboard sync instances...", network_prefix)

    for attempt in range(10):  # Try multiple times to account for timing issues
        if attempt > 0:
            logger.debug("Retry attempt %d of 10...", attempt + 1)
            time.sleep(1)

        _scan_network_range(network_prefix, port, timeout, peers, seen_devices, our_device_id)

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
