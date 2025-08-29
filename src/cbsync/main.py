#!/usr/bin/env python3

"""Cross-platform clipboard synchronization application.

This application shares clipboard text content between devices on the same network. It runs a server
to receive updates and a client to send updates when the local clipboard changes.
"""

from __future__ import annotations

import threading
import time

from polykit import PolyArgs, PolyLog
from polykit.cli import handle_interrupt

from cbsync.clipboard_monitor import ClipboardMonitor
from cbsync.clipboard_server import ClipboardServer
from cbsync.peer_discovery import PeerDiscoveryManager

logger = PolyLog.get_logger()


class ClipboardSyncApp:
    """Main application class that manages all components."""

    def __init__(
        self,
        port: int = 8765,
        max_size_mb: int = 10,
        interface_ip: str | None = None,
        discovery_interval: int = 10,
        health_check_interval: int = 10,
        enable_discovery: bool = True,
    ):
        self.port = port
        self.max_size_mb = max_size_mb
        self.interface_ip = interface_ip
        self.discovery_interval = discovery_interval
        self.health_check_interval = health_check_interval
        self.enable_discovery = enable_discovery
        self.shutdown_event = threading.Event()

        # Components
        self.server: ClipboardServer | None = None
        self.monitor: ClipboardMonitor | None = None
        self.discovery_manager: PeerDiscoveryManager | None = None

    def start(self) -> None:
        """Start the application."""
        # Start the server first so other devices can discover us
        self.server = ClipboardServer(self.port, self.shutdown_event)
        self.server.start()

        # Give the server a moment to start
        time.sleep(1)

        # Start peer discovery if enabled
        if self.enable_discovery:
            self.discovery_manager = PeerDiscoveryManager(
                self.port, self.interface_ip, self.discovery_interval, self.health_check_interval
            )
            self.discovery_manager.start(self.shutdown_event)

        # Start clipboard monitoring
        self.monitor = ClipboardMonitor(self.port, self.discovery_manager, self.max_size_mb)
        self.monitor.start(self.shutdown_event)

    def stop(self) -> None:
        """Stop the application."""
        logger.info("Shutting down...")

        # Signal shutdown to all threads
        self.shutdown_event.set()

        # Stop discovery manager
        if self.discovery_manager:
            self.discovery_manager.stop()

        # Stop clipboard monitor
        if self.monitor:
            self.monitor.stop()

        # Stop Flask server
        if self.server:
            self.server.stop()

        logger.info("Shutdown complete.")

    def run(self) -> None:
        """Run the application until shutdown."""
        self.start()

        # Log current IP address for easy peer configuration
        current_ip = PeerDiscoveryManager.get_current_ip()
        if current_ip:
            logger.info("This device's IP address: %s (port %s)", current_ip, self.port)
            logger.info("Other devices can connect using: --peers %s", current_ip)
        else:
            logger.warning("Could not determine this device's IP address")

        logger.info("cbsync is running. Press Ctrl+C to stop.")
        if self.enable_discovery:
            logger.debug("Peer discovery enabled (interval: %d seconds)", self.discovery_interval)

        # Keep the main thread alive
        try:
            while not self.shutdown_event.is_set():
                # Check for shutdown more frequently
                for _ in range(10):
                    if self.shutdown_event.is_set():
                        break
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass  # Let the shutdown function handle it


@handle_interrupt()
def main():
    """Main application entry point."""
    parser = PolyArgs(description="Cross-platform cbsync")
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="port for the clipboard server (default: 8765)",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=10,
        help="maximum clipboard size in MB (default: 10)",
    )
    parser.add_argument(
        "--interface",
        type=str,
        help="specify network interface IP (e.g., 192.168.1.100) for discovery",
    )
    parser.add_argument(
        "--discovery-interval",
        type=int,
        default=10,
        help="peer discovery interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--health-check-interval",
        type=int,
        default=10,
        help="peer health check interval in seconds (default: 10)",
    )
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="disable automatic peer discovery",
    )

    args = parser.parse_args()

    # Create and run the application
    app = ClipboardSyncApp(
        port=args.port,
        max_size_mb=args.max_size,
        interface_ip=args.interface,
        discovery_interval=args.discovery_interval,
        health_check_interval=args.health_check_interval,
        enable_discovery=not args.no_discovery,
    )

    # Run the application
    app.run()


if __name__ == "__main__":
    main()
