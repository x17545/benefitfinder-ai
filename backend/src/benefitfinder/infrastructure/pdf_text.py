"""PDF page extraction in a bounded subprocess; normalization is injected separately."""

from io import BytesIO
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from pypdf import PdfReader

MAX_PAGES = 100
MAX_TEXT_CHARS = 100_000


def _extract(body: bytes) -> list[str]:
    logging.getLogger('pypdf').setLevel(logging.CRITICAL)
    reader = PdfReader(BytesIO(body), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError
    pages = []
    for page in reader.pages:
        text = page.extract_text(extraction_mode='layout', layout_mode_strip_rotated=False)
        pages.append(text)
        if sum(len(value) for value in pages) > MAX_TEXT_CHARS:
            raise ValueError
    return pages


def extract_pdf_pages(body: bytes) -> list[str]:
    """Reject encrypted, scanned, oversized or slow documents without raw error text."""
    if type(body) is not bytes or not body.startswith(b'%PDF-') or len(body) > 16 * 1024 * 1024:
        raise ValueError('official_document_pdf_unavailable')
    try:
        with TemporaryDirectory(prefix='benefitfinder-pdf-') as temporary:
            root = Path(temporary)
            source = root/'source.pdf'
            source.write_bytes(body)
            source.chmod(0o600)
            result = subprocess.run([sys.executable, '-m', __name__, str(source)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25, check=True,
                env={key:value for key,value in os.environ.items()
                     if key in ('PATH', 'SYSTEMROOT', 'PYTHONPATH', 'LANG', 'LC_ALL')})
            if len(result.stdout) > 1024 * 1024:
                raise ValueError
            pages = json.loads(result.stdout)
            if (not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES
                or any(type(page) is not str for page in pages)
                or sum(map(len,pages)) > MAX_TEXT_CHARS
                or not any(page.strip() for page in pages)):
                raise ValueError
            return pages
    except (OSError, ValueError, subprocess.SubprocessError, RecursionError):
        raise ValueError('official_document_pdf_unavailable') from None


def _darwin_virtual_size() -> int:
    import ctypes
    import struct

    # XNU proc_info.h: PROC_PIDTASKINFO (4), struct proc_taskinfo.
    layout = struct.Struct("=6Q12i")
    info = ctypes.create_string_buffer(layout.size)
    proc_pidinfo = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidinfo
    proc_pidinfo.argtypes = (
        ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
    )
    proc_pidinfo.restype = ctypes.c_int
    if proc_pidinfo(os.getpid(), 4, 0, info, layout.size) != layout.size:
        raise OSError("pdf_worker_address_space_unavailable")
    virtual_size = layout.unpack(info)[0]
    if virtual_size == 0:
        raise OSError("pdf_worker_address_space_unavailable")
    return virtual_size


def _limit_worker() -> None:
    # Workers must fail closed when the OS cannot enforce the limits. Keeping the
    # import here lets other job commands run on systems without resource.
    import resource

    address_space = 768 * 1024 * 1024
    if sys.platform == "darwin":
        # dyld and the allocator reserve virtual regions before the worker starts.
        address_space += _darwin_virtual_size()
    for kind, cap in ((resource.RLIMIT_AS, address_space), (resource.RLIMIT_CPU, 20)):
        for inherited in resource.getrlimit(kind):
            if inherited != resource.RLIM_INFINITY:
                cap = min(cap, inherited)
        resource.setrlimit(kind, (cap, cap))


if __name__ == '__main__':
    try:
        _limit_worker()
        content = Path(sys.argv[1]).read_bytes()
        print(json.dumps(_extract(content), ensure_ascii=False))
    except Exception:
        raise SystemExit(2) from None
