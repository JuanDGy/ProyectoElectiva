import numpy as np
import pymupdf
import pytest

from services.pdf_text import extract_text


def pdf_with_pages(*texts):
    with pymupdf.open() as document:
        for text in texts:
            document.new_page(width=300, height=200).insert_text((20, 40), text)
        return document.tobytes()


def fake_reader(monkeypatch, text='Texto recuperado con OCR'):
    class Reader:
        def readtext(self, image, *, detail, paragraph, **kwargs):
            assert isinstance(image, np.ndarray)
            assert image.ndim == 3 and image.shape[2] == 3
            assert detail == 0
            return [text]
    monkeypatch.setattr('services.pdf_text._get_reader', lambda: Reader())


def test_direct_text_at_threshold_needs_no_ocr(monkeypatch):
    monkeypatch.setattr('services.pdf_text._get_reader', lambda: pytest.fail('OCR innecesario'))
    assert extract_text(pdf_with_pages('Cuenta 1234'), min_characters=10) == 'Cuenta 1234'


def test_short_page_uses_ocr_and_preserves_other_pages(monkeypatch):
    fake_reader(monkeypatch)
    assert extract_text(pdf_with_pages('Cuenta 1234', ' ! '), min_characters=10) == 'Cuenta 1234\n\nTexto recuperado con OCR'


def test_threshold_can_force_ocr(monkeypatch):
    fake_reader(monkeypatch)
    assert extract_text(pdf_with_pages('Cuenta 1234'), min_characters=11) == 'Texto recuperado con OCR'


def test_empty_ocr_is_an_error(monkeypatch):
    fake_reader(monkeypatch, text=' \n')
    with pytest.raises(RuntimeError, match='texto'):
        extract_text(pdf_with_pages(''))


def test_invalid_threshold():
    with pytest.raises(ValueError):
        extract_text(b'%PDF-test', min_characters=0)


def test_spanish_native_text_keeps_accents(monkeypatch):
    monkeypatch.setattr('services.pdf_text._get_reader', lambda: pytest.fail('OCR innecesario'))
    assert extract_text(pdf_with_pages('Año, energía, consumo'), min_characters=10) == 'Año, energía, consumo'


def test_ocr_uses_high_resolution_and_preserves_separate_payment_lines(monkeypatch):
    class Reader:
        def readtext(self, image, **kwargs):
            # A 300-point-wide PDF page at 300 DPI is 1250 pixels wide.
            assert image.shape[1] == 1250
            assert kwargs['paragraph'] is False
            assert kwargs['decoder'] == 'greedy'
            assert kwargs['canvas_size'] >= 3500
            return ['TOTAL A PAGAR', '$53.525.650']
    monkeypatch.setattr('services.pdf_text._get_reader', lambda: Reader())
    assert extract_text(pdf_with_pages('')) == 'TOTAL A PAGAR\n$53.525.650'


def test_native_table_keeps_labels_separate_from_values(monkeypatch):
    monkeypatch.setattr('services.pdf_text._get_reader', lambda: pytest.fail('OCR innecesario'))
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=200)
        page.insert_text((20, 40), 'CONSUMO MES:', fontsize=8)
        page.insert_text((20, 45), '14988 kWh', fontsize=7)
        pdf = document.tobytes()
    assert extract_text(pdf, min_characters=10) == 'CONSUMO MES:\n14988 kWh'
