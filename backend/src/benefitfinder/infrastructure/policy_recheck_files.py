"""Create-only local handoff; never overwrite prior review work."""

import json
import os
from pathlib import Path
import tempfile


def write_recheck_page(path: Path, page: dict[str, object]) -> None:
    encoded = json.dumps(page, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".rechecks-", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic create, failing if the output already exists (including symlinks).
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
