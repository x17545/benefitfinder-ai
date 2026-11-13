"""Explicit public-document collection targets; no policy facts or approval."""

from dataclasses import dataclass
import re
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from benefitfinder.application.collection import SourceResponse

SOURCE_NAME = 'official_documents'
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


def public_document_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (type(value) is str and len(value) <= 2048 and value.isprintable()
                and not any(char.isspace() for char in value)
                and parsed.scheme == 'https' and bool(parsed.hostname)
                and not parsed.username and not parsed.password and not parsed.fragment
                and parsed.port in (None, 443)
                and not any(any(part in key.lower() for part in ('key', 'token', 'secret', 'password'))
                            for key, _ in parse_qsl(parsed.query)))
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class OfficialDocumentTarget:
    external_id: str
    policy_id: str
    url: str
    format: str
    method: str = 'GET'
    form: tuple[tuple[str, str], ...] = ()
    categories: tuple[str, ...] = ()
    required_text: tuple[str, ...] = ()
    content_id: str | None = None

    def __post_init__(self) -> None:
        try:
            if (re.fullmatch(r'[A-Za-z0-9:_-]{1,120}', self.external_id) is None
                or re.fullmatch(r'youth:[A-Za-z0-9:_-]{1,100}', self.policy_id) is None
                or not public_document_url(self.url) or self.format not in ('html', 'pdf')
                or self.method not in ('GET', 'POST') or not isinstance(self.form, tuple)
                or len(self.form) > 8 or (self.method == 'GET' and self.form)
                or not isinstance(self.categories, tuple)
                or len(self.categories) != len(set(self.categories))
                or not set(self.categories) <= {'employment', 'housing'}
                or not isinstance(self.required_text, tuple) or len(self.required_text) > 8
                or any(type(text) is not str or not 1 <= len(text) <= 200
                       or not text.strip() or not text.isprintable() for text in self.required_text)
                or (self.content_id is not None and (self.format != 'html'
                    or re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,99}',self.content_id) is None))):
                raise ValueError
            names = set()
            for key, value in self.form:
                if (type(key) is not str or type(value) is not str
                    or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', key) is None
                    or key in names or not 1 <= len(value) <= 500 or not value.isprintable()
                    or any(part in key.lower() for part in ('key', 'token', 'secret', 'password'))):
                    raise ValueError
                names.add(key)
        except (TypeError, ValueError, AttributeError):
            raise ValueError('invalid_official_document_target') from None


class OfficialDocumentFetcher(Protocol):
    def fetch(self, target: OfficialDocumentTarget) -> SourceResponse: ...
