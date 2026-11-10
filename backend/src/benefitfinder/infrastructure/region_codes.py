"""행정표준코드 공개 전체자료 → 시도/자치 시군구 목록 (인증키 불필요).

원문은 저장하지 않는다. 유효한 목록만 프로세스에서 24시간 보관하고 다음 조회 때
갱신한다. 만료 후 갱신 실패 시 오래된 코드를 내보내지 않는다.
"""

from __future__ import annotations

import csv
import http.client
import io
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Callable

from benefitfinder.application.region_options import (
    DistrictOption,
    RegionOption,
    RegionOptionsUnavailable,
)

SOURCE_PAGE = "https://www.code.go.kr/stdcode/regCodeL.do"
DOWNLOAD_URL = "https://www.code.go.kr/etc/codeFullDown.do"
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024


def parse_region_archive(archive: bytes) -> tuple[RegionOption, ...]:
    """CP949 TSV의 현존 코드만 변환한다. 손상·빈 목록·불완전 계층은 거부한다."""
    try:
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("oversized archive")
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            files = source.infolist()
            if len(files) != 1 or files[0].file_size > MAX_TEXT_BYTES:
                raise ValueError("unexpected archive contents")
            with source.open(files[0]) as member:
                content = member.read(MAX_TEXT_BYTES + 1)
            if len(content) > MAX_TEXT_BYTES:
                raise ValueError("oversized text")
        rows = csv.reader(io.StringIO(content.decode("cp949")), delimiter="\t")
        if next(rows, None) != ["법정동코드", "법정동명", "폐지여부"]:
            raise ValueError("unexpected header")
        active: dict[str, str] = {}
        for row in rows:
            if not row:
                continue
            if len(row) != 3:
                raise ValueError("unexpected row")
            code, name, status = row
            if not re.fullmatch(r"[0-9]{10}", code) or status not in {"존재", "폐지"}:
                raise ValueError("unexpected code/status")
            if status == "폐지":
                continue
            if not name.strip() or code in active:
                raise ValueError("empty name or duplicate code")
            active[code] = name.strip()

        provinces = {c[:2]: n for c, n in active.items() if c.endswith("00000000")}
        # 세종은 공식 원천에 시도 3600000000 없이 3611000000 한 행으로 나온다.
        if active.get("3611000000") == "세종특별자치시":
            provinces["36"] = "세종특별자치시"
        children: dict[str, list[DistrictOption]] = {code: [] for code in provinces}
        for code, name in active.items():
            if not code.endswith("00000") or code.endswith("00000000"):
                continue
            parent = provinces.get(code[:2])
            if parent is None or not (name == parent or name.startswith(parent + " ")):
                raise ValueError("missing parent")
            local_name = name.removeprefix(parent).strip()
            # 자치 시군구를 사용한다. '수원시 장안구' 같은 일반구는 상위 시로 선택한다.
            if " " in local_name:
                continue
            children[code[:2]].append(DistrictOption(code[:5], local_name or parent))
        if not provinces or any(not values for values in children.values()):
            raise ValueError("empty region hierarchy")
        return tuple(
            RegionOption(code, provinces[code], tuple(sorted(children[code], key=lambda c: c.code)))
            for code in sorted(provinces)
        )
    except (
        ValueError,
        UnicodeError,
        csv.Error,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        EOFError,
        zlib.error,
    ) as error:
        raise RegionOptionsUnavailable("공식 지역 코드 자료를 확인하지 못했습니다.") from error


def download_region_archive() -> bytes:
    """공식 화면의 전체자료 공개 다운로드와 같은 POST. 응답 크기/대기를 제한한다."""
    request = urllib.request.Request(
        DOWNLOAD_URL,
        data=urllib.parse.urlencode({"codeseId": "법정동코드"}).encode("ascii"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "benefitfinder-ai/0.1 (region options)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("oversized response")
        return archive
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        http.client.HTTPException,
    ) as error:
        raise RegionOptionsUnavailable("공식 지역 코드 다운로드를 완료하지 못했습니다.") from error


class OfficialRegionCatalog:
    """중복 다운로드를 막고, 성공 24시간·실패 60초를 보관하는 원천 어댑터."""

    def __init__(
        self,
        *,
        download: Callable[[], bytes] = download_region_archive,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._download = download
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._regions: tuple[RegionOption, ...] = ()
        self._expires_at = 0.0
        self._retry_at = 0.0

    def get_regions(self) -> tuple[RegionOption, ...]:
        """시작 시 HTTP를 호출하지 않고 최초 조회/만료 시점에만 갱신한다."""
        with self._lock:
            now = self._monotonic()
            if self._regions and now < self._expires_at:
                return self._regions
            if now < self._retry_at:
                raise RegionOptionsUnavailable("지역 목록을 잠시 후 다시 조회해 주세요.")
            try:
                regions = parse_region_archive(self._download())
            except RegionOptionsUnavailable:
                self._retry_at = self._monotonic() + 60
                raise
            self._regions = regions
            self._expires_at = self._monotonic() + 24 * 60 * 60
            self._retry_at = 0.0
            return regions
