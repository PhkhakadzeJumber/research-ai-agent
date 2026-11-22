"""Tiny real PDFs for tests (step 6b), written by hand so no PDF-making library is needed.

make_pdf(pages) builds a valid PDF 1.4 file with one Helvetica text line per string, page by page;
pypdf extracts the lines back. encrypted(pdf) returns a password-protected copy (pypdf's own writer, RC4).
"""

from __future__ import annotations

import io


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    objects: dict[int, str] = {1: "<< /Type /Catalog /Pages 2 0 R >>",
                               3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids, number = [], 4
    for lines in pages:
        stream = "BT /F1 11 Tf 14 TL 72 760 Td " + " ".join(f"({_escape(line)}) Tj T*" for line in lines) + " ET"
        objects[number] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                           f"/Resources << /Font << /F1 3 0 R >> >> /Contents {number + 1} 0 R >>")
        objects[number + 1] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
        kids.append(f"{number} 0 R")
        number += 2
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"

    out, offsets = b"%PDF-1.4\n", {}
    for key in sorted(objects):
        offsets[key] = len(out)
        out += f"{key} 0 obj\n{objects[key]}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offsets[key]:010d} 00000 n \n" for key in sorted(objects)).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def encrypted(pdf: bytes, password: str = "secret") -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password=password, algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
