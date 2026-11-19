"""Extracción de facturas con Gemini (visión). Opcional: el pipeline nunca la requiere.

La clave se lee solo aquí, no en `app.config`, para que el procesamiento local siga
funcionando sin credenciales de IA. Toda salida se valida con las mismas reglas
deterministas antes de usarse: el LLM no reemplaza la validación.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
FIELDS = ["cuenta", "factura", "kwh", "valor_facturado", "inicio_periodo", "fin_periodo"]

PROMPT = """Eres un extractor de facturas de energía eléctrica colombianas (ENEL/Codensa).
Lee el PDF adjunto y devuelve SOLO los datos que aparecen literalmente en el documento.

Reglas generales:
- No inventes, no completes y no corrijas dígitos por lógica. Si un campo no aparece,
  devuélvelo como cadena vacía y confianza "baja".
- Ignora los textos de ejemplo, las rutas, los números de medidor, transformador y
  circuito, y los códigos de barras.

Campos y de dónde salen:
- cuenta: SOLO el recuadro "NÚMERO DE CLIENTE" (arriba a la derecha) o la etiqueta
  "NÚMERO DE CLIENTE". Nunca uses "Cuenta padre No." del resumen ejecutivo.
  Devuelve los dígitos, incluido el dígito de verificación si está separado por guion.
- factura: número de factura que aparece junto a "FACTURA DE SERVICIOS PÚBLICOS No.",
  en el encabezado "Documento Equivalente Electrónico No." o bajo el título de una
  factura exprés. Devuélvelo con guiones tal como se imprime.
- kwh: consumo del período facturado en kWh, como número decimal. Si la factura muestra
  varios consumos (reactiva, excedentes), usa únicamente la energía activa facturada.
- valor_facturado: SOLO el recuadro "TOTAL A PAGAR" (el que va junto al ícono de factura
  o el recuadro verde). Si el documento dice "$0", devuelve "0". Nunca uses "Total
  Energía", "Subtotal consumos", "Total Aseo" ni "Total otros".
- inicio_periodo y fin_periodo: fechas de "PERIODO FACTURADO" en formato AAAA-MM-DD.
- evidencias: lista con un objeto por campo, {"campo": "...", "cita": "..."}, donde la
  cita es la frase o etiqueta exacta del documento que respalda el valor (máximo 80
  caracteres). Usa el nombre del campo tal cual: cuenta, factura, kwh, valor_facturado,
  inicio_periodo, fin_periodo.
- confianza: "alta" si el dato es legible y no ambiguo, "media" si requiere interpretar
  una tabla, "baja" si está borroso o ausente.
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "cuenta": {"type": "string"},
        "factura": {"type": "string"},
        "kwh": {"type": "string"},
        "valor_facturado": {"type": "string"},
        "inicio_periodo": {"type": "string"},
        "fin_periodo": {"type": "string"},
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "evidencias": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "campo": {"type": "string"},
                    "cita": {"type": "string"},
                },
                "required": ["campo", "cita"],
            },
        },
    },
    "required": [*FIELDS, "confianza", "evidencias"],
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict
    model: str
    elapsed_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def gemini_settings() -> dict:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("Falta GEMINI_API_KEY en .env")
    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return {
        "key": key,
        "model": model,
        "timeout": float(os.getenv("GEMINI_TIMEOUT_SECONDS", "90")),
        "max_tokens": int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "1024")),
    }


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _number(value) -> float | None:
    text = str(value or "").strip().replace("$", "").replace(" ", "")
    text = text.replace(".", "").replace(",", ".") if re.fullmatch(r"[\d.,]+", text) else text
    try:
        return float(text)
    except ValueError:
        return None


def _date(value) -> str | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value or "").strip())
    return match.group(0) if match else None


def normalize_payload(payload: dict) -> dict:
    """Convierte la respuesta del LLM al contrato del pipeline, sin completar huecos."""
    return {
        "Cuenta": _digits(payload.get("cuenta")),
        "NumeroFactura": re.sub(r"\s", "", str(payload.get("factura") or "")),
        "kWh": _number(payload.get("kwh")),
        "ValorFacturado": _number(payload.get("valor_facturado")),
        "InicioPeriodo": _date(payload.get("inicio_periodo")),
        "FinPeriodo": _date(payload.get("fin_periodo")),
        "confianza": str(payload.get("confianza") or "").strip().lower() or None,
        "evidencias": payload.get("evidencias") or [],
    }


def stated_total(text: str) -> float | None:
    """El número que el documento declara como TOTAL A PAGAR, si es explícito."""
    match = re.search(r"TOTAL\s*A\s*PAGAR\s*:?\s*\$?\s*([\d][\d.,]*)", text or "", re.I)
    if not match:
        return None
    return _number(match.group(1))


def cross_check(data: dict, text: str) -> list[str]:
    """Contradicciones entre lo que propone el LLM y el texto extraído del PDF.

    No exige que todo aparezca en el texto (un PDF escaneado puede no tenerlo), pero
    sí rechaza un valor que contradiga una etiqueta explícita del documento.
    """
    warnings: list[str] = []
    digits = re.sub(r"\D", "", text or "")
    if data.get("Cuenta") and data["Cuenta"] not in digits:
        warnings.append("cuenta no aparece en el texto extraído")
    factura = _digits(data.get("NumeroFactura"))
    if factura and factura not in digits:
        warnings.append("número de factura no aparece en el texto extraído")

    valor = data.get("ValorFacturado")
    stated = stated_total(text)
    if valor is not None and stated is not None and abs(float(valor) - stated) > 0.5:
        warnings.append(f"el texto dice TOTAL A PAGAR {stated:g} y el LLM propone {float(valor):g}")

    start, end = data.get("InicioPeriodo"), data.get("FinPeriodo")
    if start and end and start > end:
        warnings.append("período invertido")
    return warnings


def _validate(data: dict) -> list[str]:
    warnings = []
    if data["Cuenta"] and len(data["Cuenta"]) not in (7, 8, 9, 10):
        warnings.append(f"cuenta con longitud atípica: {data['Cuenta']}")
    if data["kWh"] is not None and data["kWh"] < 0:
        warnings.append("kWh negativo")
    if data["ValorFacturado"] is not None and data["ValorFacturado"] <= 0:
        warnings.append("valor facturado no positivo")
    if data["InicioPeriodo"] and data["FinPeriodo"] and data["InicioPeriodo"] > data["FinPeriodo"]:
        warnings.append("período invertido")
    return warnings


def extract_invoice(pdf_bytes: bytes, *, key: str | None = None, model: str | None = None,
                    timeout: float | None = None, max_tokens: int | None = None) -> LLMResult:
    settings = gemini_settings()
    key = key or settings["key"]
    model = model or settings["model"]
    timeout = timeout or settings["timeout"]
    max_tokens = max_tokens or settings["max_tokens"]

    generation = {
        "temperature": 0,
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
        "responseSchema": RESPONSE_SCHEMA,
    }
    if model.startswith("gemini-3"):
        # La extracción estructurada no gana con razonamiento largo y este lo encarece.
        generation["thinkingConfig"] = {"thinkingBudget": 0}
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": PROMPT},
                {"inline_data": {"mime_type": "application/pdf",
                                 "data": base64.b64encode(pdf_bytes).decode("ascii")}},
            ],
        }],
        "generationConfig": generation,
    }

    start = time.perf_counter()
    try:
        response = requests.post(ENDPOINT.format(model=model), params={"key": key},
                                 json=body, timeout=timeout)
    except requests.RequestException as exc:
        return LLMResult({}, model, time.perf_counter() - start,
                         error=f"red: {type(exc).__name__}")
    elapsed = time.perf_counter() - start
    if response.status_code != 200:
        detail = response.text[:200].replace(key, "***")
        return LLMResult({}, model, elapsed, error=f"HTTP {response.status_code}: {detail}")

    payload = response.json()
    usage = payload.get("usageMetadata") or {}
    candidates = payload.get("candidates") or []
    if not candidates:
        block = (payload.get("promptFeedback") or {}).get("blockReason", "sin candidatos")
        return LLMResult({}, model, elapsed, error=f"respuesta vacía: {block}",
                         input_tokens=usage.get("promptTokenCount"),
                         output_tokens=usage.get("candidatesTokenCount"))

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return LLMResult({}, model, elapsed, raw=text, error="JSON inválido",
                         input_tokens=usage.get("promptTokenCount"),
                         output_tokens=usage.get("candidatesTokenCount"))

    data = normalize_payload(parsed)
    return LLMResult(data, model, elapsed, input_tokens=usage.get("promptTokenCount"),
                     output_tokens=usage.get("candidatesTokenCount"), raw=text,
                     warnings=_validate(data))
