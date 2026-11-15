"""PDF page extraction in a bounded subprocess; normalization is injected separately."""

from io import BytesIO
import json
import logging
import os
from pathlib import Path
import resource
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


if __name__ == '__main__':
    try:
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        content = Path(sys.argv[1]).read_bytes()
        print(json.dumps(_extract(content), ensure_ascii=False))
    except Exception:
        raise SystemExit(2) from None
