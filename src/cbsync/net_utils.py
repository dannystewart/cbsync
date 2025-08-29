from __future__ import annotations

import socket
import threading
import time
import uuid

import requests
from polykit import PolyLog

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


def get_device_id() -> str:
    """Generate a deterministic unique device ID to ensure the same device gets the same ID."""
    hostname = socket.gethostname()
    device_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, hostname)
    return f"{hostname}-{device_uuid.hex[:8]}"


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


def _check_host_for_cbsync(
    ip: str, port: int, timeout: float, peers: list[str], seen_devices: set[str], our_device_id: str
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
                    logger.debug("Skipping our own device: %s", device_id)
                    return

                # Only add if we haven't seen this device before
                if device_id not in seen_devices:
                    seen_devices.add(device_id)
                    peers.append(ip)
                    logger.info("Found peer: %s (%s)", ip, data.get("hostname", "unknown"))
    except Exception:
        pass  # Host not reachable or not running cbsync


def scan_network_range(
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
            target=_check_host_for_cbsync,
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


def get_network_prefix(interface_ip: str | None) -> str | None:
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
    """Discover other cbsync instances on the network."""
    peers = []
    seen_devices = set()
    our_device_id = get_device_id()

    if not (network_prefix := get_network_prefix(interface_ip)):
        return peers

    logger.info("Scanning network %s* for cbsync instances...", network_prefix)

    for attempt in range(10):  # Try multiple times to account for timing issues
        if attempt > 0:
            logger.debug("Retry attempt %d of 10...", attempt + 1)
            time.sleep(1)

        scan_network_range(network_prefix, port, timeout, peers, seen_devices, our_device_id)

        if peers:  # If we found peers, we can stop early
            break

    if not peers:
        logger.info("No peers found after 10 attempts.")

    return peers
