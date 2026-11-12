"""Bounded reads inside an explicitly selected local collection directory."""

from pathlib import Path, PurePosixPath


def read_collection_file(data_dir: Path, relative: str, limit: int) -> bytes:
    reference = PurePosixPath(relative)
    if reference.is_absolute() or ".." in reference.parts or "\0" in relative:
        raise ValueError("invalid_collection_file")
    root = data_dir.resolve()
    target = (root / reference).resolve()
    if not target.is_relative_to(root):
        raise ValueError("invalid_collection_file")
    with target.open("rb") as handle:
        contents = handle.read(limit + 1)
    if len(contents) > limit:
        raise ValueError("collection_file_too_large")
    return contents
