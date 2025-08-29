from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import requests
from polykit import PolyLog

from cbsync.net_utils import get_device_id, get_network_prefix, scan_network_range

if TYPE_CHECKING:
    from logging import Logger


class PeerDiscoveryManager:
    """Manages continuous peer discovery on the network."""

    def __init__(
        self,
        port: int,
        interface_ip: str | None = None,
        discovery_interval: int = 10,
        health_check_interval: int = 10,
        timeout: float = 2.0,
    ):
        self.port: int = port
        self.interface_ip: str | None = interface_ip
        self.discovery_interval: int = discovery_interval
        self.health_check_interval: int = health_check_interval
        self.timeout: float = timeout
        self.running: bool = False
        self.peers: list[str] = []
        self.peers_lock: threading.Lock = threading.Lock()
        self.our_device_id: str = get_device_id()
        self.discovery_thread: threading.Thread | None = None
        self.logger: Logger = PolyLog.get_logger()
        self.last_discovery_time: float = 0

    def get_peers(self) -> list[str]:
        """Get a copy of the current peer list."""
        with self.peers_lock:
            return self.peers.copy()

    def add_peer(self, peer: str) -> bool:
        """Add a peer if not already present."""
        with self.peers_lock:
            if peer not in self.peers:
                self.peers.append(peer)
                self.logger.info("Added new peer: %s", peer)
                return True
        return False

    def remove_peer(self, peer: str) -> bool:
        """Remove a peer if present."""
        with self.peers_lock:
            if peer in self.peers:
                self.peers.remove(peer)
                self.logger.info("Removed peer: %s", peer)
                return True
        return False

    def _discover_peers_once(self) -> list[str]:
        """Perform a single peer discovery scan."""
        discovered_peers = []
        seen_devices = set()

        if not (network_prefix := get_network_prefix(self.interface_ip)):
            return discovered_peers

        self.logger.debug("Scanning network %s* for cbsync instances...", network_prefix)
        scan_network_range(
            network_prefix,
            self.port,
            self.timeout,
            discovered_peers,
            seen_devices,
            self.our_device_id,
        )

        return discovered_peers

    def _discovery_loop(self, shutdown_event: threading.Event) -> None:
        """Main discovery loop that runs continuously."""
        self.logger.info(
            "Starting peer discovery (discovery: %ds, health check: %ds)",
            self.discovery_interval,
            self.health_check_interval,
        )

        while self.running and not shutdown_event.is_set():
            self._health_check_loop()

            # Wait for next cycle (health check interval)
            for _ in range(self.health_check_interval * 10):  # Check 10 times per second
                if shutdown_event.is_set():
                    break
                time.sleep(0.1)

    def _health_check_loop(self) -> None:
        try:
            current_time = time.time()

            # Perform full discovery scan periodically
            if current_time - self.last_discovery_time >= self.discovery_interval:
                self.logger.debug("Performing full network discovery scan...")
                new_peers = self._discover_peers_once()
                self.last_discovery_time = current_time

                # Add new peers
                with self.peers_lock:
                    for peer in new_peers:
                        if peer not in self.peers:
                            self.peers.append(peer)
                            self.logger.info("Discovered new peer: %s", peer)

            # Always perform health checks on existing peers
            with self.peers_lock:
                current_peers = self.peers.copy()

            for peer in current_peers:
                if not self._is_peer_alive(peer):
                    self.remove_peer(peer)

            # Log current peer count
            with self.peers_lock:
                peer_count = len(self.peers)
            if peer_count > 0:
                self.logger.debug("Current peers: %d", peer_count)

        except Exception as e:
            self.logger.error("Error in peer discovery: %s", str(e))

    def _is_peer_alive(self, peer: str) -> bool:
        """Check if a peer is still responding."""
        try:
            response = requests.get(f"http://{peer}:{self.port}/health", timeout=self.timeout)
            return response.status_code == 200
        except Exception:
            return False

    def start(self, shutdown_event: threading.Event) -> None:
        """Start the discovery manager."""
        self.running = True
        self.discovery_thread = threading.Thread(
            target=self._discovery_loop, args=(shutdown_event,), daemon=True
        )
        self.discovery_thread.start()
        self.logger.info("Peer discovery manager started.")

    def stop(self) -> None:
        """Stop the discovery manager."""
        self.running = False
        if self.discovery_thread and self.discovery_thread.is_alive():
            self.discovery_thread.join(timeout=2)
        self.logger.info("Peer discovery manager stopped.")
