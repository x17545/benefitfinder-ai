"""공고 HTML·PDF 텍스트 변환(notice-text-v1) 테스트. 모든 HTML·문장은 가상이다."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.source_chunks import plan_source_chunks
from benefitfinder.ingestion.notice_text import (
    MAX_LINE_CHARS,
    NOTICE_DECODE_FAILED,
    NOTICE_TEXT_EMPTY,
    NoticeTextError,
    decode_html,
    fits_chunked_extraction,
    html_to_text,
    pdf_pages_to_text,
)

PAGE = """<!doctype html>
<html><head><title>가상 청년정책 포털</title><style>.x{color:red}</style>
<script>var key = "SECRET";</script></head>
<body>
<header><a href="/">홈</a> 로그인</header>
<nav><ul><li>정책</li><li>공지</li></ul></nav>
<div id="contents">
  <h3>[가상] 청년 월세 지원 공고</h3>
  <p>지원 대상: 만 19세 ~ 34세&nbsp;무주택 청년<br>소득: 기준 중위소득 60% 이하</p>
  <ul><li>월 최대 20만원</li><li>최대 12개월</li></ul>
  <table>
    <tr><th>구분</th><th>내용</th></tr>
    <tr><td>신청 기간</td><td>2026. 3. 4. ~ 3. 31.</td></tr>
  </table>
  <p>중복 지원 불가:   다른 월세 지원을 받는 경우 제외</p>
</div>
<aside>관련 사이트</aside>
<footer>주소 · 전화 000-0000-0000</footer>
</body></html>"""

EXPECTED = """[가상] 청년 월세 지원 공고

지원 대상: 만 19세 ~ 34세 무주택 청년
소득: 기준 중위소득 60% 이하

- 월 최대 20만원
- 최대 12개월

구분 | 내용
신청 기간 | 2026. 3. 4. ~ 3. 31.

중복 지원 불가: 다른 월세 지원을 받는 경우 제외
"""

KST = timezone(timedelta(hours=9))


class HtmlToTextTests(unittest.TestCase):
    def test_extracts_main_region_as_paragraphs(self) -> None:
        self.assertEqual(html_to_text(PAGE), EXPECTED)

    def test_drops_scripts_menus_and_footer(self) -> None:
        text = html_to_text(PAGE)
        for removed in ("SECRET", "로그인", "정책\n", "관련 사이트", "000-0000", "color"):
            self.assertNotIn(removed, text)

    def test_same_input_gives_same_text(self) -> None:
        self.assertEqual(html_to_text(PAGE), html_to_text(PAGE))
        reordered_whitespace = PAGE.replace("\n", "\r\n").replace("  ", "\t")
        self.assertEqual(html_to_text(reordered_whitespace), EXPECTED)

    def test_without_main_region_uses_body_minus_chrome(self) -> None:
        html = "<body><nav>메뉴</nav><div><p>가상 본문 1</p><p>가상 본문 2</p></div></body>"
        self.assertEqual(html_to_text(html), "가상 본문 1\n\n가상 본문 2\n")

    def test_article_and_role_main_are_main_regions(self) -> None:
        html = "<div>사이트 소개</div><div role='main'><p>가상 공고</p></div>"
        self.assertEqual(html_to_text(html), "가상 공고\n")
        html = "<div>사이트 소개</div><article><p>가상 기사</p></article>"
        self.assertEqual(html_to_text(html), "가상 기사\n")

    def test_unclosed_tags_and_pre(self) -> None:
        html = "<div id='content'><p>첫 문단<p>둘째 문단<pre>  표 형식\n  유지</pre></div>"
        self.assertEqual(html_to_text(html), "첫 문단\n\n둘째 문단\n\n표 형식\n유지\n")

    def test_long_lines_are_split_at_spaces(self) -> None:
        sentence = "가상 문장입니다 " * 120
        text = html_to_text(f"<p>{sentence}</p>")
        self.assertTrue(all(len(line) <= MAX_LINE_CHARS for line in text.splitlines()))
        self.assertEqual(" ".join(text.split()), " ".join(sentence.split()))

    def test_normalizes_unicode_and_entities(self) -> None:
        decomposed = "가"  # 'ㄱ'+'ㅏ' 조합형 → '가'
        text = html_to_text(f"<p>{decomposed} &amp; &lt;안내&gt;</p>")
        self.assertEqual(text, "가 & <안내>\n")

    def test_empty_page_fails_with_fixed_code(self) -> None:
        for html in ("", "<script>x</script>", "<nav>메뉴</nav><footer>꼬리</footer>"):
            with self.assertRaises(NoticeTextError) as caught:
                html_to_text(html)
            self.assertEqual(str(caught.exception), NOTICE_TEXT_EMPTY)

    def test_result_is_valid_agent_input(self) -> None:
        text = html_to_text(PAGE)
        document = SourceDocument(
            id="youthcenter-virtual-notice-1",
            policy_id="youth:youthcenter:virtual",
            source_url="https://example.invalid/notice",
            document_version="v2026-11-12",
            collected_at=datetime(2026, 11, 12, 10, 0, tzinfo=KST),
            text=text,
            official=True,
        )
        self.assertEqual(len(document.content_sha256), 64)
        self.assertTrue(fits_chunked_extraction(text))
        self.assertGreaterEqual(len(plan_source_chunks(text)), 1)


class DecodeTests(unittest.TestCase):
    def test_uses_header_then_meta_then_fallbacks(self) -> None:
        korean = "<p>가상 공고</p>"
        self.assertEqual(decode_html(korean.encode("cp949"), "text/html; charset=EUC-KR"), korean)
        meta = '<meta charset="euc-kr"><p>가상 공고</p>'
        self.assertEqual(decode_html(meta.encode("cp949"), "text/html"), meta)
        self.assertEqual(decode_html(korean.encode("cp949"), None), korean)
        self.assertEqual(decode_html(("﻿" + korean).encode("utf-8"), None), korean)

    def test_wrong_header_falls_back(self) -> None:
        korean = "<p>가상 공고</p>"
        self.assertEqual(decode_html(korean.encode("utf-8"), "text/html; charset=ascii"), korean)
        self.assertEqual(decode_html(korean.encode("utf-8"), "text/html; charset=nope"), korean)

    def test_undecodable_fails_with_fixed_code(self) -> None:
        with self.assertRaises(NoticeTextError) as caught:
            decode_html(b"\xff\xfe\xfd\xfc\x80\x81", None)
        self.assertEqual(str(caught.exception), NOTICE_DECODE_FAILED)


class PdfPagesTests(unittest.TestCase):
    def test_adds_page_markers_read_by_chunker(self) -> None:
        text = pdf_pages_to_text(["가상 1쪽\n\n\n지원 내용", "", "가상 3쪽   끝"])
        self.assertEqual(
            text,
            "=== PDF PAGE 1 ===\n가상 1쪽\n\n지원 내용\n\n"
            "=== PDF PAGE 2 ===\n\n"
            "=== PDF PAGE 3 ===\n가상 3쪽 끝\n",
        )
        chunks = plan_source_chunks(text)
        self.assertEqual(chunks[0].core_start, 1)
        self.assertTrue(fits_chunked_extraction(text))

    def test_no_pages_or_all_blank_fails(self) -> None:
        for pages in ([], ["", "  \n"]):
            with self.assertRaises(NoticeTextError) as caught:
                pdf_pages_to_text(pages)
            self.assertEqual(str(caught.exception), NOTICE_TEXT_EMPTY)

    def test_oversized_page_is_reported_not_truncated(self) -> None:
        dense = "\n".join(f"가상 조항 {n}번 내용입니다" for n in range(400))
        text = pdf_pages_to_text([dense])
        self.assertEqual(text.count("가상 조항"), 400)
        self.assertFalse(fits_chunked_extraction(text))


if __name__ == "__main__":
    unittest.main()
