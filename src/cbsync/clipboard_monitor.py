from __future__ import annotations

import platform
import threading
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests
from polykit import PolyLog

from cbsync.clipboard_backend import get_clipboard_sequence_number, read_preferred_with_status

if TYPE_CHECKING:
    from logging import Logger

    from cbsync.clipboard_data import ClipboardData
    from cbsync.peer_discovery import PeerDiscoveryManager
    from cbsync.sync_state import ClipboardSyncState


@dataclass(slots=True)
class ClipboardSendResult:
    """Result of sending clipboard data to peers."""

    attempted_peers: list[str]
    delivered_peers: set[str]
    failed_peers: set[str]

    @property
    def attempted_count(self) -> int:
        """Number of peers attempted to send to."""
        return len(self.attempted_peers)


class ClipboardMonitor:
    """Monitors clipboard changes and sends updates to other devices."""

    def __init__(
        self,
        server_port: int,
        discovery_manager: PeerDiscoveryManager | None,
        sync_state: ClipboardSyncState,
        max_size_mb: int = 15,
    ):
        self.server_port: int = server_port
        self.discovery_manager: PeerDiscoveryManager | None = discovery_manager
        self.sync_state = sync_state
        self.max_size_bytes: int = max_size_mb * 1024 * 1024
        self.running: bool = False
        self.last_handled_hash: str | None = None
        self.pending_clipboard_hash: str | None = None
        self.pending_message_id: str | None = None
        self.pending_origin_device_id: str | None = None
        self.pending_succeeded_peers: set[str] = set()
        self.pending_attempt_count: int = 0
        self.pending_retry_at: float = 0
        self.update_lock: threading.Lock = threading.Lock()
        self.session: requests.Session = requests.Session()
        self.logger: Logger = PolyLog.get_logger()
        self.request_timeout_s: tuple[float, float] = (0.75, 2.0)
        self.local_device_id: str = sync_state.local_device_id
        self.last_clipboard_sequence: int | None = get_clipboard_sequence_number()

        # Heartbeat/progress signals for the supervisor.
        now = time.time()
        self.last_monitor_tick: float = now
        self.last_send_attempt: float = 0
        self.last_send_success: float = 0

    def get_clipboard_content(self, *, sequence_changed: bool) -> ClipboardData | None:
        """Get current clipboard content, with extra retries for fresh Windows changes."""
        attempts = 4 if platform.system() == "Windows" and sequence_changed else 1
        delay_s = 0.06
        last_transient_failure = False

        for attempt in range(1, attempts + 1):
            try:
                result = read_preferred_with_status(
                    max_size_bytes=self.max_size_bytes,
                    prefer_image=True,
                )
            except Exception as e:
                self.logger.error("Error reading clipboard: %s", e)
                return None

            if result.item:
                if attempt > 1:
                    self.logger.debug(
                        "Recovered clipboard read on retry %d for sequence change %s.",
                        attempt,
                        self.last_clipboard_sequence,
                    )
                return result.item

            last_transient_failure = last_transient_failure or result.transient_failure
            if attempt < attempts:
                time.sleep(delay_s)

        if last_transient_failure:
            self.logger.debug("Clipboard read ended without content after transient failures.")
        return None

    def send_to_peers(
        self,
        clipboard_data: ClipboardData,
        *,
        target_peers: list[str],
    ) -> ClipboardSendResult:
        """Send clipboard data to all peer devices."""
        data = clipboard_data.to_dict()
        delivered_peers: set[str] = set()
        failed_peers: set[str] = set()

        if not target_peers:
            self.logger.debug("No peers available to send clipboard update")
            return ClipboardSendResult([], delivered_peers, failed_peers)

        self.logger.debug(
            "Sending clipboard update to %d peer%s: %s",
            len(target_peers),
            "s" if len(target_peers) > 1 else "",
            target_peers,
        )

        for peer in target_peers:
            try:
                url = f"http://{peer}:{self.server_port}/clipboard"
                self.logger.debug("Sending to %s: %s", peer, url)
                self.last_send_attempt = time.time()
                response = self.session.post(
                    url,
                    json=data,
                    headers={"Content-Type": "application/json"},
                    timeout=self.request_timeout_s,
                )
                try:
                    if response.status_code == 200:
                        self.last_send_success = time.time()
                        delivered_peers.add(peer)
                        self.logger.debug(
                            "Delivered clipboard %s to %s.",
                            clipboard_data.hash,
                            peer,
                        )
                    else:
                        failed_peers.add(peer)
                        self.logger.warning("Failed to send to %s: %s", peer, response.status_code)
                finally:
                    response.close()
            except requests.exceptions.RequestException as e:
                failed_peers.add(peer)
                self.logger.warning("Could not reach peer %s: %s", peer, e)

        return ClipboardSendResult(target_peers, delivered_peers, failed_peers)

    def _prepare_outbound_item(self, clipboard_data: ClipboardData) -> None:
        clipboard_data.metadata["message_id"] = self.pending_message_id or uuid.uuid4().hex
        clipboard_data.metadata["origin_device_id"] = (
            self.pending_origin_device_id or self.local_device_id
        )
        clipboard_data.metadata["sender_device_id"] = self.local_device_id

    def _reset_pending_state(self, clipboard_data: ClipboardData) -> None:
        self.pending_clipboard_hash = clipboard_data.hash
        self.pending_message_id = uuid.uuid4().hex
        self.pending_origin_device_id = self.local_device_id
        self.pending_succeeded_peers = set()
        self.pending_attempt_count = 0
        self.pending_retry_at = 0
        self.logger.debug(
            "Queued local clipboard %s (%s, %s bytes) for sync.",
            clipboard_data.hash,
            clipboard_data.kind,
            clipboard_data.size_bytes,
        )

    def _mark_clipboard_handled(self, clipboard_hash: str) -> None:
        self.last_handled_hash = clipboard_hash
        self.pending_clipboard_hash = None
        self.pending_message_id = None
        self.pending_origin_device_id = None
        self.pending_succeeded_peers = set()
        self.pending_attempt_count = 0
        self.pending_retry_at = 0

    def _schedule_retry(self) -> None:
        self.pending_attempt_count += 1
        retry_delay_s = min(5.0, 0.5 * (2 ** max(0, self.pending_attempt_count - 1)))
        self.pending_retry_at = time.time() + retry_delay_s
        self.logger.debug(
            "Will retry clipboard %s in %.2fs after incomplete delivery.",
            self.pending_clipboard_hash,
            retry_delay_s,
        )

    def _current_unsent_peers(self) -> list[str]:
        if self.discovery_manager is None:
            return []

        peers = self.discovery_manager.get_peers()
        return [peer for peer in peers if peer not in self.pending_succeeded_peers]

    def _should_probe_clipboard(self, now: float) -> tuple[bool, bool]:
        current_sequence = get_clipboard_sequence_number()
        if current_sequence is not None:
            sequence_changed = current_sequence != self.last_clipboard_sequence
            self.last_clipboard_sequence = current_sequence
            if sequence_changed:
                return True, True
            if self.pending_clipboard_hash and now >= self.pending_retry_at:
                return True, False
            return False, False

        if self.pending_clipboard_hash and now >= self.pending_retry_at:
            return True, False
        return True, False

    def _process_clipboard_item(self, clipboard_data: ClipboardData) -> None:
        with self.update_lock:
            now = time.time()
            suppression = self.sync_state.consume_local_suppression(clipboard_data.hash)
            if suppression is not None:
                self.logger.debug(
                    "Suppressing rebroadcast of remote clipboard %s from %s.",
                    clipboard_data.hash,
                    suppression.sender_device_id or "unknown",
                )
                self._mark_clipboard_handled(clipboard_data.hash)
                return

            if (
                clipboard_data.hash == self.last_handled_hash
                and clipboard_data.hash != self.pending_clipboard_hash
            ):
                return

            if clipboard_data.hash != self.pending_clipboard_hash:
                self._reset_pending_state(clipboard_data)

            target_peers = self._current_unsent_peers()
            if not target_peers:
                if self.pending_retry_at and now < self.pending_retry_at:
                    return

                if self.pending_succeeded_peers:
                    self.logger.info(
                        "Clipboard %s reached all currently known peers.",
                        clipboard_data.hash,
                    )
                    self._mark_clipboard_handled(clipboard_data.hash)
                else:
                    self.logger.debug(
                        "Clipboard %s has no reachable peers yet; keeping it pending.",
                        clipboard_data.hash,
                    )
                    self._schedule_retry()
                return

            self._prepare_outbound_item(clipboard_data)
            result = self.send_to_peers(clipboard_data, target_peers=target_peers)
            self.pending_succeeded_peers.update(result.delivered_peers)

            if not result.failed_peers and result.attempted_count > 0:
                self.logger.info(
                    "Item %s delivered to peers.",
                    clipboard_data.hash,
                )
                self._mark_clipboard_handled(clipboard_data.hash)
                return

            remaining_peers = self._current_unsent_peers()
            if not remaining_peers and self.pending_succeeded_peers:
                self.logger.info(
                    "Item %s delivered to peers after retry tracking.",
                    clipboard_data.hash,
                )
                self._mark_clipboard_handled(clipboard_data.hash)
                return

            self._schedule_retry()

    def monitor_clipboard(self, shutdown_event: threading.Event) -> None:
        """Monitor clipboard for changes and send updates."""
        self.logger.debug("Starting clipboard monitoring...")

        while self.running and not shutdown_event.is_set():
            try:
                self.last_monitor_tick = time.time()
                should_probe, sequence_changed = self._should_probe_clipboard(
                    self.last_monitor_tick
                )
                if should_probe:
                    clipboard_data = self.get_clipboard_content(sequence_changed=sequence_changed)
                    if clipboard_data:
                        self._process_clipboard_item(clipboard_data)

                # Check for shutdown more frequently than clipboard changes
                for _ in range(10):  # Check 10 times per 0.5 seconds
                    if shutdown_event.is_set():
                        break
                    time.sleep(0.05)

            except Exception as e:
                self.logger.error("Error in clipboard monitoring: %s", e)
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
