"""Download and load Spanish OCR models without processing invoices."""

from services.pdf_text import OCR_MODEL_DIRECTORY, _get_reader


if __name__ == "__main__":
    _get_reader()
    print(f"OCR en español preparado en {OCR_MODEL_DIRECTORY}")
