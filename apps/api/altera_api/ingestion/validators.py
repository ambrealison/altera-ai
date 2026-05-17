"""Pre-ingestion upload validation and checksum utilities.

These checks run *before* the CSV parsing pipeline so we can reject
obviously bad files early and set the right terminal status.
"""
from __future__ import annotations

import hashlib

MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024  # 50 MB

#: Extensions accepted at the upload boundary.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"csv", "tsv", "txt"})

#: MIME types accepted at the upload boundary.
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/csv",
    "text/plain",
    "text/tab-separated-values",
    "application/csv",
    "application/octet-stream",
})


def validate_upload(
    filename: str,
    data: bytes,
    *,
    content_type: str | None = None,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> list[str]:
    """Return a list of pre-ingestion error messages. Empty list means OK.

    Checks: non-empty, size limit, file extension, content-type (if given).
    Does not parse the file — CSV-level errors are reported by the pipeline.
    """
    errors: list[str] = []

    if len(data) == 0:
        errors.append("file is empty")
        return errors  # further checks are meaningless on empty input

    if len(data) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        errors.append(f"file exceeds the {mb} MB limit ({len(data):,} bytes)")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(f".{e}" for e in sorted(ALLOWED_EXTENSIONS))
        errors.append(f"file type '.{ext}' is not allowed; accepted: {allowed}")

    if content_type is not None:
        bare = content_type.split(";")[0].strip().lower()
        if bare not in ALLOWED_CONTENT_TYPES:
            errors.append(f"content-type '{bare}' is not accepted for uploads")

    return errors


def compute_sha256(data: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()
