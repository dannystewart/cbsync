from __future__ import annotations

import base64
import hashlib
import time
from typing import Any

from polykit import PolyLog
from polykit.text import Text

logger = PolyLog.get_logger()


class ClipboardData:
    """Represents clipboard data (text or image)."""

    def __init__(
        self,
        *,
        kind: str,
        text: str | None = None,
        image_png_bytes: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        if kind not in {"text", "image"}:
            msg = f"Unsupported clipboard kind: {kind}"
            raise ValueError(msg)

        self.kind: str = kind

        self.raw_content: str | None = text
        self.content: str = Text.normalize(text or "") if kind == "text" else ""

        self.image_png_bytes: bytes | None = image_png_bytes if kind == "image" else None

        self.metadata: dict[str, Any] = metadata or {}
        self.timestamp: float = time.time()
        self.size_bytes: int = self._calculate_size_bytes()
        self.hash: str = self._calculate_hash()

    @classmethod
    def from_text(cls, text: str, metadata: dict[str, Any] | None = None) -> ClipboardData:
        """Create ClipboardData from text."""
        return cls(kind="text", text=text, metadata=metadata)

    @classmethod
    def from_image_png_bytes(
        cls, png_bytes: bytes, metadata: dict[str, Any] | None = None
    ) -> ClipboardData:
        """Create ClipboardData from image PNG bytes."""
        return cls(kind="image", image_png_bytes=png_bytes, metadata=metadata)

    def _canonical_bytes(self) -> bytes:
        if self.kind == "text":
            return self.content.encode("utf-8")
        if self.kind == "image":
            if not self.image_png_bytes:
                return b""
            return self.image_png_bytes
        return b""

    def _calculate_size_bytes(self) -> int:
        return len(self._canonical_bytes())

    def _calculate_hash(self) -> str:
        """Calculate hash of canonical bytes for deduplication."""
        canonical_bytes = self._canonical_bytes()
        to_hash = f"{self.kind}:".encode() + canonical_bytes
        return hashlib.sha256(to_hash).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Raises:
            ValueError: If the clipboard kind is unsupported.
        """
        if self.kind == "text":
            return {
                "kind": "text",
                "content": self.raw_content or "",
                "size": self.size_bytes,
                "metadata": self.metadata,
                "timestamp": self.timestamp,
                "hash": self.hash,
            }

        if self.kind == "image":
            png_bytes = self.image_png_bytes or b""
            return {
                "kind": "image",
                "image_png_b64": base64.b64encode(png_bytes).decode("ascii"),
                "mime": "image/png",
                "size": self.size_bytes,
                "metadata": self.metadata,
                "timestamp": self.timestamp,
                "hash": self.hash,
            }

        msg = f"Unsupported clipboard kind: {self.kind}"
        raise ValueError(msg)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipboardData:
        """Create ClipboardData from dictionary.

        Raises:
            ValueError: If the clipboard kind is unsupported.
        """
        kind = data.get("kind")
        metadata = dict(data.get("metadata") or {})
        remote_hash = data.get("hash")

        if not kind:
            obj = cls.from_text(data["content"], metadata)
            obj.timestamp = data.get("timestamp", obj.timestamp)
            if remote_hash and remote_hash != obj.hash:
                obj.metadata["remote_hash"] = remote_hash
            return obj

        if kind == "text":
            obj = cls.from_text(data.get("content", ""), metadata)
            obj.timestamp = data.get("timestamp", obj.timestamp)
            if remote_hash and remote_hash != obj.hash:
                obj.metadata["remote_hash"] = remote_hash
            return obj

        if kind == "image":
            b64 = data.get("image_png_b64", "")
            png_bytes = base64.b64decode(b64.encode("ascii")) if b64 else b""
            obj = cls.from_image_png_bytes(png_bytes, metadata)
            obj.timestamp = data.get("timestamp", obj.timestamp)
            if remote_hash and remote_hash != obj.hash:
                obj.metadata["remote_hash"] = remote_hash
            return obj

        msg = f"Unsupported clipboard kind: {kind}"
        raise ValueError(msg)

    def is_equivalent_to(self, other: ClipboardData) -> bool:
        """Check if this clipboard data is equivalent to another."""
        return self.hash == other.hash and self.kind == other.kind

    def is_different_from_current_clipboard(self, max_size_bytes: int | None = None) -> bool:
        """Check if this item is different from what's currently in the clipboard."""
        try:
            from cbsync.clipboard_backend import read_preferred

            current = read_preferred(max_size_bytes=max_size_bytes)
            if not current:
                return self.size_bytes > 0
            return current.hash != self.hash or current.kind != self.kind
        except Exception as e:
            logger.debug("Error comparing with current clipboard: %s", e)
            return True  # Assume different if we can't compare

    @property
    def size(self) -> int:
        """Get the size of the clipboard data in bytes."""
        return self.size_bytes
