from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def nullable(value: Any):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def clean_strings(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if isinstance(value, str):
            value = value.replace("\\n", " ").replace("\n", " ").replace("\r", " ")
            value = re.sub(r"[\x00-\x1F\x7F]", " ", value)
            value = re.sub(r"\s+", " ", value).strip()
        out[key] = value
    return out


def complete(data: dict) -> bool:
    required = [
        "Oficina",
        "Cuenta",
        "NumeroFactura",
        "kWh",
        "ValorFacturado",
        "InicioPeriodo",
        "FinPeriodo",
        "Año",
        "Mes",
    ]
    return all(data.get(field) is not None and str(data[field]).strip() for field in required)


def safe_filename_piece(value: Any, fallback: str = "SIN_DATO") -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[\x00-\x1F]", "", text)
    return text or fallback
