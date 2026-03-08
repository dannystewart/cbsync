from __future__ import annotations

import operator
import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class SuppressedClipboard:
    """Tracks a remote clipboard item that should not be rebroadcast locally."""

    content_hash: str
    origin_device_id: str | None
    message_id: str | None
    sender_device_id: str | None
    recorded_at: float
    expires_at: float


class ClipboardSyncState:
    """Shared sync state for loop prevention and duplicate suppression."""

    def __init__(
        self,
        local_device_id: str,
        *,
        suppression_ttl_s: float = 10.0,
        recent_message_ttl_s: float = 120.0,
        max_entries: int = 256,
    ) -> None:
        self.local_device_id = local_device_id
        self.suppression_ttl_s = max(1.0, suppression_ttl_s)
        self.recent_message_ttl_s = max(10.0, recent_message_ttl_s)
        self.max_entries = max(32, max_entries)
        self._lock = threading.Lock()
        self._suppressed_hashes: dict[str, SuppressedClipboard] = {}
        self._recent_message_ids: dict[str, float] = {}

    def inspect_incoming_message(
        self,
        *,
        message_id: str | None,
        origin_device_id: str | None,
        sender_device_id: str | None,
    ) -> str | None:
        """Return a skip reason for an incoming sync message, if any."""

        with self._lock:
            self._prune_locked()

            if (
                origin_device_id
                and origin_device_id == self.local_device_id
                and sender_device_id != self.local_device_id
            ):
                if message_id:
                    self._recent_message_ids[message_id] = time.time() + self.recent_message_ttl_s
                return "originated_locally"

            if message_id:
                if message_id in self._recent_message_ids:
                    return "duplicate_message_id"
                self._recent_message_ids[message_id] = time.time() + self.recent_message_ttl_s

            return None

    def remember_remote_clipboard(
        self,
        *,
        content_hash: str,
        origin_device_id: str | None,
        message_id: str | None,
        sender_device_id: str | None,
    ) -> None:
        """Remember a remote clipboard item so the monitor can suppress one rebroadcast."""

        now = time.time()
        entry = SuppressedClipboard(
            content_hash=content_hash,
            origin_device_id=origin_device_id,
            message_id=message_id,
            sender_device_id=sender_device_id,
            recorded_at=now,
            expires_at=now + self.suppression_ttl_s,
        )

        with self._lock:
            self._prune_locked()
            self._suppressed_hashes[content_hash] = entry

    def consume_local_suppression(self, content_hash: str) -> SuppressedClipboard | None:
        """Consume a pending suppression entry for a locally observed clipboard hash."""

        with self._lock:
            self._prune_locked()
            entry = self._suppressed_hashes.get(content_hash)
            if entry is None:
                return None

            self._suppressed_hashes.pop(content_hash, None)
            return entry

    def forget_local_suppression(self, content_hash: str) -> None:
        """Remove a pending suppression entry if the remote write failed."""

        with self._lock:
            self._suppressed_hashes.pop(content_hash, None)

    def _prune_locked(self) -> None:
        now = time.time()

        expired_hashes = [
            content_hash
            for content_hash, entry in self._suppressed_hashes.items()
            if entry.expires_at <= now
        ]
        for content_hash in expired_hashes:
            self._suppressed_hashes.pop(content_hash, None)

        expired_messages = [
            message_id
            for message_id, expires_at in self._recent_message_ids.items()
            if expires_at <= now
        ]
        for message_id in expired_messages:
            self._recent_message_ids.pop(message_id, None)

        if len(self._suppressed_hashes) > self.max_entries:
            ordered_hashes = sorted(
                self._suppressed_hashes.items(),
                key=lambda item: item[1].recorded_at,
            )
            overflow = len(self._suppressed_hashes) - self.max_entries
            for content_hash, _entry in ordered_hashes[:overflow]:
                self._suppressed_hashes.pop(content_hash, None)

        if len(self._recent_message_ids) > self.max_entries:
            ordered_messages = sorted(self._recent_message_ids.items(), key=operator.itemgetter(1))
            overflow = len(self._recent_message_ids) - self.max_entries
            for message_id, _expires_at in ordered_messages[:overflow]:
                self._recent_message_ids.pop(message_id, None)
