"""Read native PDF text and OCR only pages below the configured threshold."""

from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path

import numpy as np
import pymupdf


logger = logging.getLogger(__name__)
OCR_MODEL_DIRECTORY = Path(__file__).resolve().parents[1] / '.cache' / 'easyocr'


@lru_cache(maxsize=1)
def _get_reader():
    # Loading OCR and downloading its weights is unnecessary for native PDFs.
    import easyocr

    return easyocr.Reader(
        ['es'], gpu=False,
        model_storage_directory=str(OCR_MODEL_DIRECTORY),
        user_network_directory=str(OCR_MODEL_DIRECTORY / 'user_network'),
        verbose=False,
    )


def _ocr_page(page) -> str:
    pixmap = page.get_pixmap(dpi=300, colorspace=pymupdf.csRGB, alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, 3,
    )
    # Preserve short labels and amounts as separate lines. Increase the detector
    # canvas too, so rendering at 300 DPI is not immediately downscaled to 2560.
    parts = _get_reader().readtext(
        image, detail=0, paragraph=False, decoder='greedy', canvas_size=3500,
    )
    return '\n'.join(parts).strip()


def extract_text(pdf_bytes: bytes, min_characters: int = 100) -> str:
    if min_characters < 1:
        raise ValueError('PDF_MIN_TEXT_CHARACTERS debe ser mayor que cero')

    with pymupdf.open(stream=pdf_bytes, filetype='pdf') as document:
        result = []
        for page_number, page in enumerate(document, start=1):
            # Sorting whole lines can interleave labels and values in ENEL's
            # tightly spaced tables. Keep each native text block intact.
            text = '\n'.join(block[4].strip() for block in page.get_text('blocks', sort=True) if block[6] == 0)
            characters = sum(character.isalnum() for character in text)
            if characters >= min_characters:
                logger.info('Página %s: texto directo (%s caracteres útiles)', page_number, characters)
                result.append(text.strip())
            else:
                logger.info('Página %s: OCR local en español (%s caracteres; mínimo %s)', page_number, characters, min_characters)
                result.append(_ocr_page(page))
        text = '\n\n'.join(result).strip()
        if not any(character.isalnum() for character in text):
            raise RuntimeError('No se pudo obtener texto del PDF, ni mediante OCR local')
        return text
