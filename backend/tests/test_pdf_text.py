"""Actual PDF bytes through the bounded subprocess, using fictional page content."""

from io import BytesIO
import unittest

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from benefitfinder.infrastructure.pdf_text import extract_pdf_pages


def pdf_bytes(*, blank: bool = False, encrypted: bool = False, pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=400,height=400)
        if not blank:
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),
                NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 12 Tf 30 200 Td (Fictional youth employment) Tj ET')
            page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('fictional-password')
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class PdfTextTest(unittest.TestCase):
    def test_real_pdf_subprocess_preserves_every_page(self) -> None:
        pages = extract_pdf_pages(pdf_bytes(pages=2))
        self.assertEqual(len(pages),2)
        self.assertTrue(all('Fictional youth employment' in text for text in pages))

    def test_unreadable_encrypted_or_excessive_document_has_only_fixed_error(self) -> None:
        for body in (b'PRIVATE invalid document',pdf_bytes(blank=True),
                     pdf_bytes(encrypted=True),pdf_bytes(pages=101)):
            with self.subTest(size=len(body)),self.assertRaisesRegex(
                ValueError,'^official_document_pdf_unavailable$'):
                extract_pdf_pages(body)
