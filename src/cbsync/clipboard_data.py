from __future__ import annotations

import hashlib
import time
from typing import Any

import pyperclip
from polykit import PolyLog
from polykit.text import Text

logger = PolyLog.get_logger()


class ClipboardData:
    """Represents clipboard text data."""

    def __init__(self, content: str, metadata: dict[str, Any] | None = None):
        self.raw_content: str = content
        self.content: str = Text.normalize(content)
        self.size: int = len(self.content.encode("utf-8"))
        self.metadata: dict[str, Any] = metadata or {}
        self.timestamp: float = time.time()
        self.hash: str = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate hash of the normalized content for deduplication."""
        content_bytes = self.content.encode("utf-8")
        return hashlib.sha256(content_bytes).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "content": self.raw_content,  # Send raw content to preserve original formatting
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

    def is_equivalent_to(self, other: ClipboardData) -> bool:
        """Check if this clipboard data is equivalent to another."""
        return self.hash == other.hash

    def is_different_from_current_clipboard(self) -> bool:
        """Check if this content is different from what's currently in the clipboard."""
        try:
            current_content = pyperclip.paste()
            if not current_content:
                return bool(self.content)  # If clipboard is empty but we have content

            current_normalized = Text.normalize(current_content)
            return current_normalized != self.content
        except Exception as e:
            logger.debug("Error comparing with current clipboard: %s", str(e))
            return True  # Assume different if we can't compare
