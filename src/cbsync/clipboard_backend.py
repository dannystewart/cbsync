from __future__ import annotations

import io
import platform
import struct
from typing import Any

import pyperclip
from polykit import PolyLog

from cbsync.clipboard_data import ClipboardData

logger = PolyLog.get_logger()


def read_preferred(
    *,
    max_size_bytes: int | None = None,
    prefer_image: bool = True,
) -> ClipboardData | None:
    """Read the preferred clipboard data (text or image)."""
    system = platform.system()

    if prefer_image:
        if system == "Darwin":
            item = _mac_read_image(max_size_bytes=max_size_bytes)
            if item:
                return item
        elif system == "Windows":
            item = _win_read_image(max_size_bytes=max_size_bytes)
            if item:
                return item

    return _read_text(max_size_bytes=max_size_bytes)


def write(item: ClipboardData) -> None:
    """Write the clipboard data to the clipboard.

    Raises:
        ValueError: If the clipboard kind is unsupported.
    """
    system = platform.system()

    if item.kind == "text":
        pyperclip.copy(item.raw_content or "")
        return

    if item.kind != "image":
        msg = f"Unsupported clipboard kind: {item.kind}"
        raise ValueError(msg)

    if system == "Darwin":
        _mac_write_image_png(item.image_png_bytes or b"")
        return

    if system == "Windows":
        _win_write_image_png(item.image_png_bytes or b"")
        return

    logger.warning("Image clipboard write not supported on %s; ignoring.", system)


def _read_text(*, max_size_bytes: int | None) -> ClipboardData | None:
    """Read the text clipboard data."""
    try:
        text_content = pyperclip.paste()
        if not text_content:
            return None

        content_bytes = text_content.encode("utf-8")
        if max_size_bytes is not None and len(content_bytes) > max_size_bytes:
            logger.warning("Clipboard text too large: %s bytes.", len(content_bytes))
            return None

        return ClipboardData.from_text(text_content)
    except Exception as e:
        logger.error("Error reading clipboard text: %s", e)
        return None


def _mac_read_image(*, max_size_bytes: int | None) -> ClipboardData | None:
    try:
        import AppKit  # type: ignore
    except Exception as e:
        logger.debug("macOS image clipboard not available (missing PyObjC): %s", e)
        return None

    try:
        appkit = AppKit  # type: ignore
        appkit_any: Any = appkit

        pb = appkit_any.NSPasteboard.generalPasteboard()
        types = set(pb.types() or [])

        png_type = getattr(appkit_any, "NSPasteboardTypePNG", "public.png")
        tiff_type = getattr(appkit_any, "NSPasteboardTypeTIFF", "public.tiff")

        if png_type in types:
            return _mac_read_image_for_type(
                pb, pb_type=png_type, source_label="png", max_size_bytes=max_size_bytes
            )

        if tiff_type in types:
            return _mac_read_image_for_type(
                pb, pb_type=tiff_type, source_label="tiff", max_size_bytes=max_size_bytes
            )

        return None
    except Exception as e:
        logger.debug("Error reading macOS image clipboard: %s", e)
        return None


def _mac_read_image_for_type(
    pb: Any,
    *,
    pb_type: str,
    source_label: str,
    max_size_bytes: int | None,
) -> ClipboardData | None:
    data = pb.dataForType_(pb_type)
    if not data:
        return None

    png_bytes, metadata = _image_bytes_to_png_bytes(bytes(data))
    if not png_bytes:
        return None

    if max_size_bytes is not None and len(png_bytes) > max_size_bytes:
        logger.warning("Clipboard image too large: %s bytes.", len(png_bytes))
        return None

    logger.debug(
        "Read macOS clipboard image (%s): %s bytes (%sx%s).",
        source_label,
        len(png_bytes),
        metadata.get("width", "?"),
        metadata.get("height", "?"),
    )
    return ClipboardData.from_image_png_bytes(png_bytes, metadata=metadata)


def _mac_write_image_png(png_bytes: bytes) -> None:
    try:
        import AppKit  # type: ignore
        import Foundation  # type: ignore
    except Exception as e:
        logger.warning("macOS image clipboard write unavailable (missing PyObjC): %s", e)
        return

    try:
        if not png_bytes:
            return

        appkit = AppKit  # type: ignore
        foundation = Foundation  # type: ignore
        appkit_any: Any = appkit
        foundation_any: Any = foundation

        pb = appkit_any.NSPasteboard.generalPasteboard()
        pb.clearContents()

        png_type = getattr(appkit_any, "NSPasteboardTypePNG", "public.png")
        tiff_type = getattr(appkit_any, "NSPasteboardTypeTIFF", "public.tiff")
        legacy_tiff_type = getattr(appkit_any, "NSTIFFPboardType", None)

        nsdata_png = foundation_any.NSData.dataWithBytes_length_(png_bytes, len(png_bytes))

        nsimage = appkit_any.NSImage.alloc().initWithData_(nsdata_png)
        tiff_data = None
        if nsimage is not None:
            try:
                tiff_data = nsimage.TIFFRepresentation()
            except Exception:
                tiff_data = None

        declared_types: list[str] = [png_type, tiff_type]
        if legacy_tiff_type:
            declared_types.append(legacy_tiff_type)
        pb.declareTypes_owner_(declared_types, None)

        pb.setData_forType_(nsdata_png, png_type)
        if tiff_data is not None:
            pb.setData_forType_(tiff_data, tiff_type)
            if legacy_tiff_type:
                pb.setData_forType_(tiff_data, legacy_tiff_type)

        logger.debug("Wrote macOS clipboard image: %s bytes.", len(png_bytes))
    except Exception as e:
        logger.error("Error writing macOS image clipboard: %s", e)


def _win_read_image(*, max_size_bytes: int | None) -> ClipboardData | None:
    try:
        import win32clipboard  # type: ignore
        import win32con  # type: ignore
    except Exception as e:
        logger.debug("Windows image clipboard not available (missing pywin32): %s", e)
        return None

    try:
        win32clipboard.OpenClipboard()
        try:
            dib_format = None
            if win32clipboard.IsClipboardFormatAvailable(getattr(win32con, "CF_DIBV5", 17)):
                dib_format = getattr(win32con, "CF_DIBV5", 17)
            elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
                dib_format = win32con.CF_DIB

            if dib_format is None:
                return None

            dib_bytes = win32clipboard.GetClipboardData(dib_format)
            if not dib_bytes:
                return None

            png_bytes, metadata = _dib_bytes_to_png_bytes(bytes(dib_bytes))
            if not png_bytes:
                return None
            if max_size_bytes is not None and len(png_bytes) > max_size_bytes:
                logger.warning("Clipboard image too large: %s bytes.", len(png_bytes))
                return None

            return ClipboardData.from_image_png_bytes(png_bytes, metadata=metadata)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        logger.debug("Error reading Windows image clipboard: %s", e)
        return None


def _win_write_image_png(png_bytes: bytes) -> None:
    try:
        import win32clipboard  # type: ignore
        import win32con  # type: ignore
    except Exception as e:
        logger.warning("Windows image clipboard write unavailable (missing pywin32): %s", e)
        return

    try:
        dib_bytes = _png_bytes_to_dib_bytes(png_bytes)
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib_bytes)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        logger.error("Error writing Windows image clipboard: %s", e)


def _image_bytes_to_png_bytes(src_bytes: bytes) -> tuple[bytes, dict[str, Any]]:
    try:
        from PIL import Image  # type: ignore
    except Exception as e:
        logger.debug("Pillow not available; cannot decode image: %s", e)
        return b"", {}

    with Image.open(io.BytesIO(src_bytes)) as img:
        return _normalize_pil_image_to_png(img)


def _dib_bytes_to_png_bytes(dib_bytes: bytes) -> tuple[bytes, dict[str, Any]]:
    try:
        from PIL import Image  # type: ignore
    except Exception as e:
        logger.debug("Pillow not available; cannot decode DIB: %s", e)
        return b"", {}

    bmp_bytes = _dib_to_bmp_bytes(dib_bytes)
    with Image.open(io.BytesIO(bmp_bytes)) as img:
        return _normalize_pil_image_to_png(img)


def _png_bytes_to_dib_bytes(png_bytes: bytes) -> bytes:
    try:
        from PIL import Image  # type: ignore
    except Exception as e:
        msg = f"Pillow not available; cannot encode DIB: {e}"
        raise RuntimeError(msg) from e

    with Image.open(io.BytesIO(png_bytes)) as img:
        rgb_img = img.convert("RGBA")
        buf = io.BytesIO()
        rgb_img.save(buf, format="BMP")
        bmp = buf.getvalue()
        return bmp[14:]


def _normalize_pil_image_to_png(img: Any) -> tuple[bytes, dict[str, Any]]:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return b"", {}

    if isinstance(img, Image.Image):
        width, height = img.size
    else:
        width, height = None, None

    normalized = img.convert("RGBA")
    out = io.BytesIO()
    normalized.save(out, format="PNG", compress_level=6, optimize=False)
    metadata: dict[str, Any] = {}
    if width is not None and height is not None:
        metadata["width"] = width
        metadata["height"] = height
    return out.getvalue(), metadata


def _dib_to_bmp_bytes(dib_bytes: bytes) -> bytes:
    if len(dib_bytes) < 40:
        msg = "DIB data too short"
        raise ValueError(msg)

    header_size = struct.unpack_from("<I", dib_bytes, 0)[0]
    if header_size < 40 or header_size > len(dib_bytes):
        header_size = 40

    bpp = struct.unpack_from("<H", dib_bytes, 14)[0]
    clr_used = struct.unpack_from("<I", dib_bytes, 32)[0]
    if clr_used:
        palette_entries = clr_used
    elif bpp <= 8:
        palette_entries = 1 << bpp
    else:
        palette_entries = 0
    palette_bytes = palette_entries * 4

    off_bits = 14 + header_size + palette_bytes
    file_size = 14 + len(dib_bytes)

    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, off_bits)
    return file_header + dib_bytes
