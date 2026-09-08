"""Shared helpers for normalizing chat exports into the unified schema.

Unified schema invariants:
  - all timestamps are unix UTC floats (epoch seconds)
  - all rendered content is markdown
  - conversation key = f"{service}/{id}"
"""
from datetime import datetime
from urllib.parse import quote

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def utc_ts(v):
    """Convert any of the three export timestamp formats to a unix UTC float.

    - chatgpt: unix float seconds
    - deepseek: ISO-8601 with numeric offset (+08:00), micros
    - gemini:   RFC3339 UTC with 'Z' and milliseconds

    Python 3.8 note: datetime.fromisoformat() does NOT accept the 'Z'
    suffix, so we normalize 'Z' -> '+00:00' first (parses into an aware
    datetime). DeepSeek +08:00 parses fine natively.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    try:
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.timestamp()
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _sniff_mime(head):
    """Sniff MIME from the first bytes of a file (magic-bytes first)."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(PNG_MAGIC):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"GIF8":
        return "image/gif"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if head[:4] == b"PK\x03\x04":
        return "application/zip"  # docx/xlsx are zip-based; refined by ext below
    if head.startswith(b"%PDF"):
        return "application/pdf"
    # Emit partial for a reasonable chance of text detection
    try:
        head.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        pass
    return None


_EXT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".txt": "text/plain", ".md": "text/markdown", ".log": "text/plain",
    ".csv": "text/csv", ".json": "application/json", ".html": "text/html",
    ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def mime_for(name, head=b""):
    """Determine MIME: magic bytes first, then filename extension, then octet-stream."""
    if isinstance(head, (bytes, bytearray)) and len(head) >= 4:
        m = _sniff_mime(bytes(head[:16]))
        if m:
            if m == "application/zip" and name:
                ext = _ext_for(name)
                if ext in (".docx", ".xlsx"):
                    return _EXT_MIME[ext]
            return m
    if name:
        ext = _ext_for(name)
        if ext in _EXT_MIME:
            return _EXT_MIME[ext]
    return "application/octet-stream"


def _ext_for(name):
    """Lowercased extension for a filename, '' if none."""
    if "." in name:
        # Take last dot segment, but be careful with names like file.dat.abc
        return "." + name.rsplit(".", 1)[1].lower()
    return ""


def file_extension(name):
    """Original-style lowercase extension without the dot."""
    e = _ext_for(name)
    return e[1:] if e else ""


def asset_url(service, filename):
    """Build a Flask asset URL for a service-scoped filename."""
    from urllib.parse import quote
    return f"/asset/{service}/{quote(filename)}"


def asset_key_from_pointer(pointer):
    """Map an asset_pointer like 'sediment://file_000...8ac' or
    'file-service://file-wkkR' to the on-disk filename ('file_...acb.dat')."""
    if not pointer:
        return None
    key = pointer.split("://", 1)[-1].strip()
    if not key:
        return None
    return key if key.endswith(".dat") else key + ".dat"


def truncate(text, n=140):
    """Truncate text at n chars, appending ellipsis if cut."""
    if not text:
        return text
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def first_parts_text(parts):
    """Concatenate string elements of a ChatGPT-style parts array."""
    if not parts:
        return ""
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
    return "".join(out)