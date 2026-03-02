#!/usr/bin/env python3

"""Cross-platform clipboard synchronization application.

This application shares clipboard text content between devices on the same network. It runs a server
to receive updates and a client to send updates when the local clipboard changes.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import requests
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
        heartbeat_path: Path | None = None,
        heartbeat_interval_s: float = 2.0,
    ):
        self.port = port
        self.max_size_mb = max_size_mb
        self.interface_ip = interface_ip
        self.discovery_interval = discovery_interval
        self.health_check_interval = health_check_interval
        self.enable_discovery = enable_discovery
        self.shutdown_event = threading.Event()
        self.started_at = time.time()

        self.heartbeat_path: Path = heartbeat_path or _default_heartbeat_path(port=self.port)
        self.heartbeat_interval_s: float = max(0.25, heartbeat_interval_s)
        self.last_server_health_ok: float = self.started_at
        self.heartbeat_thread: threading.Thread | None = None

        # Components
        self.server: ClipboardServer | None = None
        self.monitor: ClipboardMonitor | None = None
        self.discovery_manager: PeerDiscoveryManager | None = None

    def start(self) -> None:
        """Start the application."""
        # Start the server first so other devices can discover us
        self.server = ClipboardServer(self.port, self.shutdown_event, max_size_mb=self.max_size_mb)
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

        # Start heartbeat last so it can observe component status.
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

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

    def _heartbeat_loop(self) -> None:
        session = requests.Session()
        url = f"http://127.0.0.1:{self.port}/health"

        while not self.shutdown_event.is_set():
            now = time.time()

            server_ok = False
            try:
                r = session.get(url, timeout=(0.5, 0.75))
                server_ok = r.status_code == 200
            except Exception:
                server_ok = False

            if server_ok:
                self.last_server_health_ok = now

            peer_count = 0
            if self.discovery_manager is not None:
                try:
                    peer_count = len(self.discovery_manager.get_peers())
                except Exception:
                    peer_count = 0

            payload = {
                "pid": os.getpid(),
                "written_at": now,
                "started_at": self.started_at,
                "port": self.port,
                "discovery_enabled": self.enable_discovery,
                "peer_count": peer_count,
                "last_monitor_tick": float(getattr(self.monitor, "last_monitor_tick", 0) or 0),
                "last_send_attempt": float(getattr(self.monitor, "last_send_attempt", 0) or 0),
                "last_send_success": float(getattr(self.monitor, "last_send_success", 0) or 0),
                "last_discovery_tick": float(
                    getattr(self.discovery_manager, "last_discovery_tick", 0) or 0
                ),
                "last_healthcheck_tick": float(
                    getattr(self.discovery_manager, "last_healthcheck_tick", 0) or 0
                ),
                "last_server_health_ok": float(self.last_server_health_ok or 0),
            }

            try:
                _atomic_write_json(self.heartbeat_path, payload)
            except Exception as e:
                logger.error("Failed to write heartbeat to %s: %s", self.heartbeat_path, e)

            for _ in range(int(self.heartbeat_interval_s * 10)):
                if self.shutdown_event.is_set():
                    break
                time.sleep(0.1)

    def run(self) -> None:
        """Run the application until shutdown."""
        self.start()

        # Log current IP address for easy peer configuration
        current_ip = PeerDiscoveryManager.get_current_ip()
        if current_ip:
            logger.info(
                "cbsync started on %s (port %s). Press Ctrl+C to stop.", current_ip, self.port
            )
        else:
            logger.warning("Could not determine this device's IP address.")
            logger.info("cbsync started on port %s. Press Ctrl+C to stop.", self.port)

        if self.enable_discovery:
            logger.debug("Scanning for peers on %d second intervals.", self.discovery_interval)

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


def _default_heartbeat_path(*, port: int) -> Path:
    temp_dir = Path(tempfile.gettempdir())
    return temp_dir / f"cbsync-heartbeat-{port}.json"


def _default_lock_path(*, kind: str, port: int) -> Path:
    temp_dir = Path(tempfile.gettempdir())
    return temp_dir / f"cbsync-lock-{kind}-{port}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    tmp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _acquire_single_instance_lock(*, kind: str, port: int) -> Path | None:
    path = _default_lock_path(kind=kind, port=port)
    payload = {"pid": os.getpid(), "kind": kind, "port": port, "started_at": time.time()}

    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_json(path) or {}
            existing_pid = int(existing.get("pid") or 0)
            if _pid_exists(existing_pid):
                logger.warning(
                    "Another cbsync %s is already running for port %s (pid=%s).",
                    kind,
                    port,
                    existing_pid,
                )
                return None

            with contextlib.suppress(Exception):
                path.unlink()
            continue
        except Exception as e:
            logger.error("Failed to acquire %s lock %s: %s", kind, path, e)
            return None

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True))
        except Exception:
            with contextlib.suppress(Exception):
                os.close(fd)
            with contextlib.suppress(Exception):
                path.unlink()
            raise

        def _release() -> None:
            current = _read_json(path) or {}
            if int(current.get("pid") or 0) != os.getpid():
                return
            with contextlib.suppress(Exception):
                path.unlink()

        atexit.register(_release)
        return path

    return None


def _local_cbsync_health_ok(*, port: int) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/health", timeout=(0.4, 0.6))
        if r.status_code != 200:
            return False
        data = r.json()
        return data.get("status") == "healthy"
    except Exception:
        return False


def _terminate_process(proc: subprocess.Popen[bytes], *, timeout_s: float) -> None:
    if proc.poll() is not None:
        return

    with contextlib.suppress(Exception):
        proc.terminate()

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)

    with contextlib.suppress(Exception):
        proc.kill()


def _build_worker_cmd(args: object) -> list[str]:
    # Use -m so this works from the installed console script environment.
    cmd: list[str] = [sys.executable, "-m", "cbsync.main", "--worker"]

    for flag, value in (
        ("--port", getattr(args, "port", None)),
        ("--max-size", getattr(args, "max_size", None)),
        ("--interface", getattr(args, "interface", None)),
        ("--discovery-interval", getattr(args, "discovery_interval", None)),
        ("--health-check-interval", getattr(args, "health_check_interval", None)),
        ("--heartbeat-interval", getattr(args, "heartbeat_interval", None)),
        ("--heartbeat-path", getattr(args, "heartbeat_path", None)),
    ):
        if value is None:
            continue
        cmd.extend([flag, str(value)])

    if getattr(args, "no_discovery", False):
        cmd.append("--no-discovery")

    return cmd


def _supervisor_wait_for_restart_reason(
    *,
    proc: subprocess.Popen[bytes],
    heartbeat_path: Path,
    heartbeat_interval_s: float,
    heartbeat_timeout_s: float,
    discovery_enabled_default: bool,
    should_exit: threading.Event,
    proc_start_time: float,
) -> str | None:
    worker_pid = proc.pid

    while not should_exit.is_set():
        exit_code = proc.poll()
        if exit_code is not None:
            return f"worker_exit(code={exit_code})"

        now = time.time()
        hb = _read_json(heartbeat_path)

        # Allow a grace period for the worker to write its first heartbeat.
        if now - proc_start_time < heartbeat_timeout_s:
            time.sleep(0.5)
            continue

        if not hb:
            return "heartbeat_missing"

        hb_pid = hb.get("pid")
        if hb_pid != worker_pid:
            hb_age_s = now - float(hb.get("written_at", 0) or 0)
            if hb_age_s > heartbeat_timeout_s:
                return "heartbeat_pid_mismatch_stale"
            time.sleep(0.5)
            continue

        last_monitor_tick = float(hb.get("last_monitor_tick", 0) or 0)
        last_server_health_ok = float(hb.get("last_server_health_ok", 0) or 0)
        last_healthcheck_tick = float(hb.get("last_healthcheck_tick", 0) or 0)
        discovery_enabled = bool(hb.get("discovery_enabled", discovery_enabled_default))

        if now - last_monitor_tick > heartbeat_timeout_s:
            return f"monitor_tick_stale(age={round(now - last_monitor_tick, 1)})"

        if now - last_server_health_ok > heartbeat_timeout_s:
            return f"server_health_stale(age={round(now - last_server_health_ok, 1)})"

        if discovery_enabled and now - last_healthcheck_tick > (heartbeat_timeout_s * 2):
            return f"discovery_healthcheck_stale(age={round(now - last_healthcheck_tick, 1)})"

        time.sleep(max(0.5, heartbeat_interval_s / 2))

    return None


def run_supervisor(args: object) -> None:
    """Run a supervisor process that restarts the worker on stalls/crashes."""
    port = int(getattr(args, "port", 8765))
    heartbeat_interval_s = float(getattr(args, "heartbeat_interval", 2.0))
    heartbeat_timeout_s = float(getattr(args, "heartbeat_timeout", 15.0))
    backoff_min_s = float(getattr(args, "restart_backoff_min", 1.0))
    backoff_max_s = float(getattr(args, "restart_backoff_max", 30.0))

    heartbeat_path = Path(
        getattr(args, "heartbeat_path", None) or _default_heartbeat_path(port=port)
    )

    logger.info(
        "Starting cbsync supervisor (port=%s, heartbeat=%ss, timeout=%ss, path=%s).",
        port,
        heartbeat_interval_s,
        heartbeat_timeout_s,
        heartbeat_path,
    )

    if _acquire_single_instance_lock(kind="supervisor", port=port) is None:
        return

    should_exit = threading.Event()

    def _handle_signal(signum: int, frame: object | None) -> None:
        _ = frame
        logger.info("Supervisor received signal %s; shutting down.", signum)
        should_exit.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(Exception):
            signal.signal(sig, _handle_signal)

    restart_backoff_s = backoff_min_s
    proc: subprocess.Popen[bytes] | None = None

    while not should_exit.is_set():
        cmd = _build_worker_cmd(args)
        logger.info("Starting worker: %s", " ".join(cmd))

        try:
            proc = subprocess.Popen(cmd)
        except Exception as e:
            logger.error("Failed to start worker: %s", e)
            time.sleep(min(restart_backoff_s, backoff_max_s))
            restart_backoff_s = min(backoff_max_s, max(backoff_min_s, restart_backoff_s * 2))
            continue

        proc_start_time = time.time()
        logger.info("Worker started (pid=%s).", proc.pid)
        reason = _supervisor_wait_for_restart_reason(
            proc=proc,
            heartbeat_path=heartbeat_path,
            heartbeat_interval_s=heartbeat_interval_s,
            heartbeat_timeout_s=heartbeat_timeout_s,
            discovery_enabled_default=True,
            should_exit=should_exit,
            proc_start_time=proc_start_time,
        )
        if reason:
            logger.warning("Restarting worker (pid=%s, reason=%s).", proc.pid, reason)

        if proc.poll() is None:
            logger.info("Stopping worker (pid=%s).", proc.pid)
            _terminate_process(proc, timeout_s=3.0)

        if should_exit.is_set():
            break

        time.sleep(restart_backoff_s)
        restart_backoff_s = min(backoff_max_s, max(backoff_min_s, restart_backoff_s * 2))

    if proc is not None and proc.poll() is None:
        logger.info("Stopping worker on supervisor exit (pid=%s).", proc.pid)
        _terminate_process(proc, timeout_s=3.0)

    logger.info("Supervisor exiting.")


@handle_interrupt()
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
    parser.add_argument(
        "--supervise",
        action="store_true",
        help="run a supervisor that restarts the worker on stalls/crashes",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help="internal flag for supervisor mode",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=2.0,
        help="heartbeat write interval in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--heartbeat-timeout",
        type=float,
        default=15.0,
        help="heartbeat staleness timeout in seconds (default: 15.0)",
    )
    parser.add_argument(
        "--heartbeat-path",
        type=str,
        help="override heartbeat JSON path (default: temp dir per-port file)",
    )
    parser.add_argument(
        "--restart-backoff-min",
        type=float,
        default=1.0,
        help="minimum restart backoff in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--restart-backoff-max",
        type=float,
        default=30.0,
        help="maximum restart backoff in seconds (default: 30.0)",
    )

    args = parser.parse_args()

    if args.supervise and not args.worker:
        run_supervisor(args)
        return

    # Worker mode: run the application normally.
    if _local_cbsync_health_ok(port=args.port):
        logger.warning("cbsync already appears to be running on localhost:%s; exiting.", args.port)
        return

    if _acquire_single_instance_lock(kind="instance", port=args.port) is None:
        return

    heartbeat_path = (
        Path(args.heartbeat_path)
        if args.heartbeat_path
        else _default_heartbeat_path(port=args.port)
    )
    app = ClipboardSyncApp(
        port=args.port,
        max_size_mb=args.max_size,
        interface_ip=args.interface,
        discovery_interval=args.discovery_interval,
        health_check_interval=args.health_check_interval,
        enable_discovery=not args.no_discovery,
        heartbeat_path=heartbeat_path,
        heartbeat_interval_s=args.heartbeat_interval,
    )
    app.run()


if __name__ == "__main__":
    main()
