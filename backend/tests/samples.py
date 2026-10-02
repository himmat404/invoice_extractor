"""Generate real sample documents for upload tests."""

import io
import itertools
import zipfile

from PIL import Image
from pypdf import PdfWriter

_counter = itertools.count(1)


def pdf(pages: int = 1, encrypt: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Title": f"Invoice {next(_counter)}"})  # unique content per call
    if encrypt:
        writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def image(fmt: str = "PNG", size: tuple[int, int] = (200, 300)) -> bytes:
    n = next(_counter)
    img = Image.new("RGB", size, ((n * 37) % 256, (n * 91) % 256, (n * 53) % 256))
    out = io.BytesIO()
    if fmt == "HEIC":
        import pillow_heif

        pillow_heif.from_pillow(img).save(out, format="HEIF")
        return out.getvalue()
    img.save(out, format=fmt)
    return out.getvalue()


def zip_of(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return out.getvalue()
