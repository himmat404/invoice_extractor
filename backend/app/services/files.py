"""Upload inspection: type detection by content, readability checks, HEIC conversion and safe
ZIP extraction (spec 4.3, 21.3). Nothing here trusts client-supplied names or MIME types."""

import io
import posixpath
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class FileType:
    name: str
    content_type: str
    extension: str


PDF = FileType("pdf", "application/pdf", "pdf")
PNG = FileType("png", "image/png", "png")
JPEG = FileType("jpeg", "image/jpeg", "jpg")
HEIC = FileType("heic", "image/heic", "heic")
ZIP = FileType("zip", "application/zip", "zip")

_HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}


class FileRejected(Exception):
    """A file that can't be accepted. ``message`` is safe to show to customers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def detect_type(data: bytes) -> FileType | None:
    head = data[:16]
    if head.startswith(b"%PDF-") or data[:1024].lstrip().startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return HEIC
    if head.startswith(b"PK\x03\x04"):
        return ZIP
    return None


def safe_filename(name: str, fallback: str = "invoice") -> str:
    base = posixpath.basename(name.replace("\\", "/")).strip()
    cleaned = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    return (cleaned or fallback)[:200]


def inspect_pdf(data: bytes) -> int:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise FileRejected(
                "encrypted_pdf", "This PDF is password-protected. Remove the password and retry."
            )
        pages = len(reader.pages)
    except FileRejected:
        raise
    except (PdfReadError, ValueError, KeyError, OSError, TypeError) as exc:
        raise FileRejected(
            "unreadable_file", "This PDF appears to be damaged or unreadable."
        ) from exc
    if pages == 0:
        raise FileRejected("empty_file", "This PDF has no pages.")
    if pages > get_settings().upload_max_pdf_pages:
        raise FileRejected(
            "too_many_pages",
            f"This PDF has {pages} pages; the limit is {get_settings().upload_max_pdf_pages}.",
        )
    return pages


def inspect_image(data: bytes) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise FileRejected(
            "unreadable_file", "This image appears to be damaged or unreadable."
        ) from exc
    except Image.DecompressionBombError as exc:
        raise FileRejected("unreadable_file", "This image is too large to process.") from exc
    if width < 50 or height < 50:
        raise FileRejected("unreadable_file", "This image is too small to read.")


def convert_heic_to_jpeg(data: bytes) -> bytes:
    try:
        import pillow_heif
        from PIL import Image

        pillow_heif.register_heif_opener()
        with Image.open(io.BytesIO(data)) as img:
            out = io.BytesIO()
            img.convert("RGB").save(out, format="JPEG", quality=92)
            return out.getvalue()
    except ImportError as exc:  # pragma: no cover - dependency is installed
        raise FileRejected("unsupported_file_type", "HEIC images aren't supported yet.") from exc
    except Exception as exc:  # noqa: BLE001
        raise FileRejected("unreadable_file", "This HEIC image couldn't be read.") from exc


def inspect_document(data: bytes, ftype: FileType) -> int | None:
    """Validate readability; returns the page count for PDFs."""
    if ftype is PDF:
        return inspect_pdf(data)
    inspect_image(data)
    return 1


# --- ZIP ----------------------------------------------------------------------------------------


@dataclass
class ArchiveEntry:
    name: str
    data: bytes | None = None
    rejection: FileRejected | None = None


def _skip(name: str) -> bool:
    parts = name.split("/")
    hidden = any(p.startswith(".") and p not in (".", "..") for p in parts)
    return name.endswith("/") or "__MACOSX" in parts or hidden


def iter_zip(data: bytes, *, max_entries: int, max_entry_bytes: int) -> Iterator[ArchiveEntry]:
    """Yield archive members safely: bounded counts, sizes and compression ratios; never writes
    to disk, so member paths can't escape anywhere (they're reduced to a base name)."""
    s = get_settings()
    total_limit = s.upload_max_zip_uncompressed_mb * 1024 * 1024
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise FileRejected("archive_invalid", "This ZIP file is damaged or unreadable.") from exc
    members = [m for m in archive.infolist() if not _skip(m.filename)]
    if not members:
        raise FileRejected("empty_file", "This ZIP file contains no invoices.")
    if len(members) > max_entries:
        raise FileRejected(
            "batch_limit_exceeded",
            f"This ZIP contains {len(members)} files; your plan allows {max_entries} per upload.",
        )
    total = 0
    for member in members:
        name = safe_filename(member.filename)
        if member.flag_bits & 0x1:
            yield ArchiveEntry(
                name,
                rejection=FileRejected(
                    "encrypted_archive", "Password-protected ZIP entries aren't supported."
                ),
            )
            continue
        if member.file_size > max_entry_bytes:
            yield ArchiveEntry(name, rejection=_too_large(max_entry_bytes))
            continue
        if (
            member.compress_size
            and member.file_size / member.compress_size > s.upload_max_zip_ratio
        ):
            yield ArchiveEntry(
                name,
                rejection=FileRejected(
                    "archive_invalid", "This file is compressed suspiciously and was skipped."
                ),
            )
            continue
        # Read with a hard cap: the declared size in the header can't be trusted.
        with archive.open(member) as fh:
            content = fh.read(max_entry_bytes + 1)
        if len(content) > max_entry_bytes:
            yield ArchiveEntry(name, rejection=_too_large(max_entry_bytes))
            continue
        total += len(content)
        if total > total_limit:
            raise FileRejected("archive_too_large", "This ZIP file is too large once extracted.")
        if detect_type(content) is ZIP:
            yield ArchiveEntry(
                name,
                rejection=FileRejected(
                    "nested_archive", "ZIP files inside ZIP files aren't supported."
                ),
            )
            continue
        yield ArchiveEntry(name, data=content)


def _too_large(limit: int) -> FileRejected:
    return FileRejected(
        "file_too_large", f"This file is larger than your plan's {limit // (1024 * 1024)} MB limit."
    )


too_large = _too_large
