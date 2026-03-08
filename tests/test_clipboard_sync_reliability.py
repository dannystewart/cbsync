# type: ignore[reportArgumentType]
from __future__ import annotations

import threading
import unittest
from unittest import mock

from cbsync.clipboard_backend import ClipboardReadResult
from cbsync.clipboard_data import ClipboardData
from cbsync.clipboard_monitor import ClipboardMonitor, ClipboardSendResult
from cbsync.clipboard_server import ClipboardServer
from cbsync.sync_state import ClipboardSyncState


class FakeDiscoveryManager:
    def __init__(self, peers: list[str] | None = None) -> None:
        self._peers = peers or []

    def get_peers(self) -> list[str]:
        return list(self._peers)

    def set_peers(self, peers: list[str]) -> None:
        self._peers = list(peers)


class ClipboardReliabilityTests(unittest.TestCase):
    def test_remote_applied_clipboard_is_suppressed_by_monitor(self) -> None:
        sync_state = ClipboardSyncState("local-device")
        monitor = ClipboardMonitor(8765, FakeDiscoveryManager(["10.0.0.2"]), sync_state)  # pyright: ignore[reportArgumentType]
        item = ClipboardData.from_text(
            "hello world",
            metadata={
                "message_id": "message-1",
                "origin_device_id": "peer-device",
                "sender_device_id": "peer-device",
            },
        )
        sync_state.remember_remote_clipboard(
            content_hash=item.hash,
            origin_device_id="peer-device",
            message_id="message-1",
            sender_device_id="peer-device",
        )

        with mock.patch.object(monitor, "send_to_peers") as send_mock:
            getattr(monitor, "_process_clipboard_item")(item)

        send_mock.assert_not_called()
        self.assertEqual(monitor.last_handled_hash, item.hash)
        self.assertIsNone(monitor.pending_clipboard_hash)

    def test_pending_clipboard_retries_until_peer_delivery_succeeds(self) -> None:
        sync_state = ClipboardSyncState("local-device")
        discovery = FakeDiscoveryManager([])
        monitor = ClipboardMonitor(8765, discovery, sync_state)
        item = ClipboardData.from_text("retry me")

        getattr(monitor, "_process_clipboard_item")(item)

        self.assertEqual(monitor.pending_clipboard_hash, item.hash)
        self.assertIsNone(monitor.last_handled_hash)

        discovery.set_peers(["10.0.0.2"])
        with mock.patch.object(
            monitor,
            "send_to_peers",
            return_value=ClipboardSendResult(
                attempted_peers=["10.0.0.2"],
                delivered_peers={"10.0.0.2"},
                failed_peers=set(),
            ),
        ) as send_mock:
            getattr(monitor, "_process_clipboard_item")(item)

        send_mock.assert_called_once()
        self.assertEqual(monitor.last_handled_hash, item.hash)
        self.assertIsNone(monitor.pending_clipboard_hash)

    def test_windows_sequence_change_retries_transient_clipboard_read(self) -> None:
        sync_state = ClipboardSyncState("local-device")
        monitor = ClipboardMonitor(8765, FakeDiscoveryManager([]), sync_state)
        item = ClipboardData.from_text("snip")

        with (
            mock.patch("cbsync.clipboard_monitor.platform.system", return_value="Windows"),
            mock.patch("cbsync.clipboard_monitor.time.sleep"),
            mock.patch(
                "cbsync.clipboard_monitor.read_preferred_with_status",
                side_effect=[
                    ClipboardReadResult(
                        item=None,
                        transient_failure=True,
                        source="image",
                        reason="clipboard_busy",
                    ),
                    ClipboardReadResult(item=item, source="image"),
                ],
            ) as read_mock,
        ):
            result = monitor.get_clipboard_content(sequence_changed=True)

        self.assertEqual(result, item)
        self.assertEqual(read_mock.call_count, 2)

    def test_server_ignores_echoed_message_from_local_origin(self) -> None:
        sync_state = ClipboardSyncState("local-device")
        server = ClipboardServer(8765, threading.Event(), sync_state)
        echoed = ClipboardData.from_text(
            "same text",
            metadata={
                "message_id": "message-echo",
                "origin_device_id": "local-device",
                "sender_device_id": "peer-device",
            },
        )

        with (
            mock.patch("cbsync.clipboard_server.write") as write_mock,
            mock.patch.object(
                ClipboardData,
                "is_different_from_current_clipboard",
                side_effect=AssertionError("echoed message should be skipped before compare"),
            ),
        ):
            getattr(server, "_process_clipboard_update")(echoed, "10.0.0.2")

        write_mock.assert_not_called()

    def test_repeated_identical_content_with_new_message_id_is_processed_intentionally(self) -> None:
        sync_state = ClipboardSyncState("local-device")
        server = ClipboardServer(8765, threading.Event(), sync_state)
        first = ClipboardData.from_text(
            "unchanged",
            metadata={
                "message_id": "message-1",
                "origin_device_id": "peer-device",
                "sender_device_id": "peer-device",
            },
        )
        second = ClipboardData.from_text(
            "unchanged",
            metadata={
                "message_id": "message-2",
                "origin_device_id": "peer-device",
                "sender_device_id": "peer-device",
            },
        )

        with (
            mock.patch("cbsync.clipboard_server.write") as write_mock,
            mock.patch.object(ClipboardData, "is_different_from_current_clipboard", return_value=True),
        ):
            process_update = getattr(server, "_process_clipboard_update")
            process_update(first, "10.0.0.2")
            process_update(second, "10.0.0.2")

        self.assertEqual(write_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
