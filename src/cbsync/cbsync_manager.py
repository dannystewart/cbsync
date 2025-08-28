#!/usr/bin/env python3

"""Process management script for clipboard sync."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Use platform-appropriate temp directory
TEMP_DIR = Path(tempfile.gettempdir())
PID_FILE = TEMP_DIR / "cbsync.pid"
LOG_FILE = TEMP_DIR / "cbsync.log"


def get_pid() -> int | None:
    """Get the PID from the PID file."""
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None
    return None


def save_pid(pid: int) -> None:
    """Save PID to file."""
    PID_FILE.write_text(str(pid), encoding="utf-8")


def remove_pid_file() -> None:
    """Remove the PID file."""
    if PID_FILE.exists():
        PID_FILE.unlink()


def is_running(pid: int) -> bool | None:
    """Check if process is running."""
    try:
        os.kill(pid, 0)  # Send signal 0 to check if process exists
        return True
    except OSError:
        return False


def start(args: list[str] | None = None) -> None:
    """Start the cbsync application."""
    if args is None:
        args = []

    pid = get_pid()
    if pid and is_running(pid):
        print(f"cbsync is already running (PID: {pid})")
        return

    print("Starting cbsync...")

    # Start the application (no need for --server-only, it runs normally by default)
    cmd = ["python", "src/cbsync/cbsync.py", *args]

    with Path(LOG_FILE).open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)

    save_pid(process.pid)
    print(f"cbsync started (PID: {process.pid})")
    print(f"Log file: {LOG_FILE}")


def stop() -> None:
    """Stop the cbsync application."""
    pid = get_pid()

    if not pid or not is_running(pid):
        print("cbsync is not running")
        remove_pid_file()
        return

    print(f"Stopping cbsync (PID: {pid})...")

    try:
        # Try graceful shutdown first
        os.kill(pid, signal.SIGINT)

        # Wait for graceful shutdown
        for _ in range(10):
            if not is_running(pid):
                print("✓ cbsync stopped gracefully")
                remove_pid_file()
                return
            time.sleep(0.5)

        # Force kill if graceful shutdown failed
        print("Force killing process...")
        os.kill(pid, signal.SIGTERM)

        # Wait a bit more
        time.sleep(1)

        if is_running(pid):
            print("Force killing with SIGKILL...")
            os.kill(pid, signal.SIGKILL)

        print("✓ cbsync stopped")
        remove_pid_file()

    except OSError as e:
        print(f"Error stopping process: {e}")
        remove_pid_file()


def status() -> None:
    """Show status of cbsync."""
    pid = get_pid()

    if not pid:
        print("cbsync is not running")
        return

    if is_running(pid):
        print(f"cbsync is running (PID: {pid})")
        if LOG_FILE.exists():
            print(f"Log file: {LOG_FILE}")
    else:
        print("cbsync is not running (stale PID file)")
        remove_pid_file()


def logs() -> None:
    """Show recent logs."""
    if LOG_FILE.exists():
        print("Recent logs:")
        print("-" * 50)
        try:
            with Path(LOG_FILE).open(encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-20:]:  # Show last 20 lines
                    print(line.rstrip())
        except OSError as e:
            print(f"Error reading log file: {e}")
    else:
        print("No log file found")


def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: cbsman <command> [args...]")
        print("Commands:")
        print("  start [args...]    - Start cbsync")
        print("  stop               - Stop cbsync")
        print("  status             - Show status")
        print("  logs               - Show recent logs")
        print("  restart [args...]  - Restart cbsync")
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:] if len(sys.argv) > 2 else []

    if command == "start":
        start(args)
    elif command == "stop":
        stop()
    elif command == "status":
        status()
    elif command == "logs":
        logs()
    elif command == "restart":
        stop()
        time.sleep(1)
        start(args)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
