from __future__ import annotations

import socket
import threading
import time
import uuid
from typing import TYPE_CHECKING

import requests
from polykit import PolyLog

if TYPE_CHECKING:
    from logging import Logger

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
        self.our_device_id: str = self.get_device_id()
        self.discovery_thread: threading.Thread | None = None
        self.logger: Logger = PolyLog.get_logger()
        self.last_discovery_time: float = 0

    @staticmethod
    def get_device_id() -> str:
        """Generate a deterministic unique device ID to ensure the same device gets the same ID."""
        hostname = socket.gethostname()
        device_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, hostname)
        return f"{hostname}-{device_uuid.hex[:8]}"

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

        if not (network_prefix := self.get_network_prefix(self.interface_ip)):
            return discovered_peers

        self.logger.debug("Scanning network %s* for cbsync instances...", network_prefix)
        self._scan_network_range(
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
        self.logger.debug(
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
                discovered_peers = self._discover_peers_once()
                self.last_discovery_time = current_time

                # Add only truly new peers
                with self.peers_lock:
                    new_peers_added = 0
                    for peer in discovered_peers:
                        if peer not in self.peers:
                            self.peers.append(peer)
                            self.logger.info("Discovered new peer: %s", peer)
                            new_peers_added += 1

                    if new_peers_added == 0 and discovered_peers:
                        self.logger.debug(
                            "No new peers found (already know %d peer%s)",
                            len(discovered_peers),
                            "s" if len(discovered_peers) > 1 else "",
                        )
                    elif new_peers_added > 0:
                        self.logger.info(
                            "Added %d new peer%s, total peers: %d",
                            new_peers_added,
                            "s" if new_peers_added > 1 else "",
                            len(self.peers),
                        )

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

    @staticmethod
    def get_current_ip() -> str | None:
        """Get the current IP address for this device."""
        interfaces = PeerDiscoveryManager._get_all_interfaces()
        if not interfaces:
            return None

        return PeerDiscoveryManager._get_preferred_interface(interfaces)

    def _scan_network_range(
        self,
        network_prefix: str,
        port: int,
        timeout: float,
        peers: list[str],
        seen_devices: set[str],
        our_device_id: str,
    ) -> None:
        """Scan a network range for cbsync instances."""
        threads = []
        for i in range(1, 255):
            ip = f"{network_prefix}{i}"
            thread = threading.Thread(
                target=self._check_host_for_cbsync,
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

    def _check_host_for_cbsync(
        self,
        ip: str,
        port: int,
        timeout: float,
        peers: list[str],
        seen_devices: set[str],
        our_device_id: str,
    ) -> None:
        """Check if a specific IP is running cbsync."""
        try:
            response = requests.get(f"http://{ip}:{port}/discover", timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "available":
                    device_id = data.get("device_id", f"{data.get('hostname', 'unknown')}-{ip}")

                    # Don't add ourselves to the peer list!
                    if device_id == our_device_id:
                        self.logger.debug("Skipping our own device: %s", device_id)
                        return

                    # Only add if we haven't seen this device before
                    if device_id not in seen_devices:
                        seen_devices.add(device_id)
                        peers.append(ip)
                        self.logger.info("Found peer: %s (%s)", ip, data.get("hostname", "unknown"))
        except Exception:
            pass  # Host not reachable or not running cbsync

    def get_network_prefix(self, interface_ip: str | None) -> str | None:
        """Get network prefix from interface IP or auto-detect."""
        if interface_ip:  # Use manually specified interface
            network_parts = interface_ip.split(".")
            if len(network_parts) == 4:
                network_prefix = ".".join(network_parts[:3]) + "."
                self.logger.info("Using specified network interface: %s", interface_ip)
                return network_prefix
            self.logger.error("Invalid interface IP format: %s", interface_ip)
            return None

        return self._get_local_network_prefix()

    def _get_local_network_prefix(self) -> str | None:
        """Get the local network prefix for discovery."""
        if not (interfaces := self._get_all_interfaces()):
            self.logger.warning("Could not determine local IP address.")
            return None

        preferred_ip = self._get_preferred_interface(interfaces)
        if not preferred_ip:
            self.logger.warning("No valid network interface found.")
            return None

        self.logger.debug("Using network interface: %s", preferred_ip)
        network_parts = preferred_ip.split(".")
        return ".".join(network_parts[:3]) + "."

    @staticmethod
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

    @staticmethod
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

    def start(self, shutdown_event: threading.Event) -> None:
        """Start the discovery manager."""
        self.running = True
        self.discovery_thread = threading.Thread(
            target=self._discovery_loop, args=(shutdown_event,), daemon=True
        )
        self.discovery_thread.start()
        self.logger.debug("Peer discovery manager started.")

    def stop(self) -> None:
        """Stop the discovery manager."""
        self.running = False
        if self.discovery_thread and self.discovery_thread.is_alive():
            self.discovery_thread.join(timeout=2)
        self.logger.debug("Peer discovery manager stopped.")
