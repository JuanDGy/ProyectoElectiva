from __future__ import annotations

import unicodedata


ENERGY_TERMS = (
    "kwh",
    "kilovatio",
    "kilovatio-hora",
    "energia activa",
    "consumo energia",
)

def normalize(text: str) -> str:
    value = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def classify(filename: str, ocr_text: str) -> str:
    text = normalize(ocr_text)
    return "ENERGIA" if any(term in text for term in ENERGY_TERMS) else "SIN_CLASIFICAR"
