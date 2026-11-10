"""공식 HTTP·개인정보 없이 가상 코드 자료로 원천 경계와 API를 검증한다."""

import io
import http.client
import json
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from jsonschema import validate

from benefitfinder.application.region_options import RegionOptionsUnavailable
from benefitfinder.bootstrap import create_app
from benefitfinder.infrastructure.region_codes import (
    DOWNLOAD_URL,
    MAX_ARCHIVE_BYTES,
    OfficialRegionCatalog,
    download_region_archive,
    parse_region_archive,
)

ROOT = Path(__file__).resolve().parents[2]
# 이름은 가상값이다. 코드 계층 형식만 실물과 같다.
ROWS = [
    "1100000000\t가상시\t존재",
    "1111000000\t가상시 가상구\t존재",
    "2900000000\t폐지시\t폐지",
    "2911000000\t폐지시 폐지구\t폐지",
    "4100000000\t가상도\t존재",
    "4111000000\t가상도 가상시\t존재",
    "4111100000\t가상도 가상시 일반구\t존재",
    "4111110100\t가상도 가상시 일반구 가상동\t존재",
    "4113000000\t가상도 다른시\t존재",
    # 원천의 특수 계층을 재현하는 최소 고정명 (실제 수집 자료 아님).
    "3611000000\t세종특별자치시\t존재",
]


def archive(rows: list[str], header: str = "법정동코드\t법정동명\t폐지여부") -> bytes:
    """최소 가상 CP949 ZIP을 메모리에 만든다."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        target.writestr("synthetic.txt", (header + "\r\n" + "\r\n".join(rows)).encode("cp949"))
    return output.getvalue()


class RegionArchiveTest(unittest.TestCase):
    def test_active_municipalities_sejong_and_stable_order(self) -> None:
        regions = parse_region_archive(archive(list(reversed(ROWS))))
        self.assertEqual([r.sido_code for r in regions], ["11", "36", "41"])
        self.assertEqual([c.code for c in regions[2].sigungu], ["41110", "41130"])
        self.assertEqual(regions[2].sigungu[0].name, "가상시")
        self.assertEqual(regions[1].sigungu[0].code, "36110")
        self.assertEqual(regions[1].sido_name, regions[1].sigungu[0].name)

    def test_rejects_corrupt_empty_duplicate_or_incomplete_hierarchy(self) -> None:
        cases = [
            b"<html>upstream error</html>",
            archive([]),
            archive(ROWS, "wrong header"),
            archive(ROWS + [ROWS[0]]),
            archive([ROWS[0]]),
            archive([ROWS[1]]),
            archive(ROWS + ["bad\tname\t존재"]),
            archive(ROWS + ["9911000000\t없는도 가상구\t존재"]),
            archive(ROWS + ["9911000000\t가상\t미확인"]),
            b"a" * (MAX_ARCHIVE_BYTES + 1),
        ]
        for value in cases:
            with self.subTest(size=len(value)), self.assertRaises(RegionOptionsUnavailable):
                parse_region_archive(value)

    def test_download_uses_official_post_and_bounded_read(self) -> None:
        response = Mock()
        response.read.return_value = archive(ROWS)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("urllib.request.urlopen", return_value=response) as send:
            self.assertEqual(download_region_archive(), archive(ROWS))
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, DOWNLOAD_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertIn(b"codeseId=", request.data)
        self.assertEqual(send.call_args.kwargs["timeout"], 10)
        response.read.assert_called_once_with(MAX_ARCHIVE_BYTES + 1)

    def test_broken_http_protocol_is_unavailable_and_safe_at_api_boundary(self) -> None:
        for error in (
            http.client.BadStatusLine("private status"),
            http.client.IncompleteRead(b"private partial body"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch("urllib.request.urlopen", side_effect=error):
                    catalog = OfficialRegionCatalog()
                    with TestClient(create_app(region_catalog=catalog)) as client:
                        response = client.get("/api/v1/options")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "dependency_unavailable")
                self.assertNotIn("private", response.text)


class RegionCatalogTest(unittest.TestCase):
    def test_lazy_cache_expiry_failure_cooldown_and_recovery(self) -> None:
        now = [10.0]
        download = Mock(return_value=archive(ROWS))
        catalog = OfficialRegionCatalog(download=download, monotonic=lambda: now[0])
        download.assert_not_called()
        first = catalog.get_regions()
        self.assertIs(catalog.get_regions(), first)
        self.assertEqual(download.call_count, 1)
        now[0] += 86400
        download.side_effect = RegionOptionsUnavailable("private upstream detail")
        for _ in range(2):
            with self.assertRaises(RegionOptionsUnavailable):
                catalog.get_regions()
        self.assertEqual(download.call_count, 2)  # 만료 자료도 내보내지 않는다.
        now[0] += 60
        download.side_effect = None
        self.assertEqual(catalog.get_regions(), first)
        self.assertEqual(download.call_count, 3)

    def test_concurrent_requests_download_once(self) -> None:
        download = Mock(return_value=archive(ROWS))
        catalog = OfficialRegionCatalog(download=download)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: catalog.get_regions(), range(8)))
        self.assertTrue(all(value == results[0] for value in results))
        download.assert_called_once()


class OptionsApiTest(unittest.TestCase):
    def test_real_http_contract_and_complete_band_enums(self) -> None:
        catalog = OfficialRegionCatalog(download=lambda: archive(ROWS))
        with TestClient(create_app(region_catalog=catalog)) as client:
            response = client.get("/api/v1/options")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            schema = json.loads((ROOT / "contracts/v1/options.schema.json").read_text())
            validate(payload, schema)
            youth = json.loads((ROOT / "contracts/v1/youth-profile.schema.json").read_text())
            data = youth["$defs"]["youth_domain_data"]["properties"]
            enums = {
                "income": youth["properties"]["income_band"]["enum"],
                "asset": data["asset_band"]["enum"],
                "deposit": data["contract"]["properties"]["deposit_band"]["enum"],
                "rent": data["contract"]["properties"]["rent_band"]["enum"],
                "parent_income": data["household"]["properties"]["parent_income_band"]["enum"],
                "parent_asset": data["household"]["properties"]["parent_asset_band"]["enum"],
            }
            for key, allowed in enums.items():
                self.assertEqual({x["value"] for x in payload["bands"][key]}, set(allowed) - {None})
            spec = client.get("/openapi.json").json()
            self.assertIn("/api/v1/options", spec["paths"])

    def test_unavailable_and_empty_are_safe_503_with_retry(self) -> None:
        for source in (
            Mock(side_effect=RegionOptionsUnavailable("secret upstream body")),
            Mock(return_value=()),
        ):
            catalog = Mock(get_regions=source)
            with TestClient(create_app(region_catalog=catalog)) as client:
                response = client.get("/api/v1/options")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["retry-after"], "60")
                self.assertEqual(response.json()["code"], "dependency_unavailable")
                self.assertNotIn("secret", response.text)
                schema = json.loads((ROOT / "contracts/v1/error.schema.json").read_text())
                validate(response.json(), schema)
                self.assertEqual(client.get("/health").status_code, 200)
