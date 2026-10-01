from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pipeline.common import clean_strings, digits, nullable


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def choose_consumption(a_value: Any, b_value: Any) -> Optional[float]:
    a = _num(a_value)
    b = _num(b_value)

    # Se conserva la regla documentada: ambos deben ser numéricos.
    if a is None or b is None:
        return None

    ra, rb = round(a), round(b)
    if ra == rb:
        return b
    if abs(ra - rb) == 1:
        return max(a, b)
    return None


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _valid_range(start: Any, end: Any) -> bool:
    s, e = _parse_date(start), _parse_date(end)
    return bool(s and e and e >= s)


def _coherence_score(end_value: Any, year: Any, month: Any) -> int:
    d = _parse_date(end_value)
    if not d:
        return 0
    score = 0
    try:
        if year and d.year == int(year):
            score += 1
    except (TypeError, ValueError):
        pass
    try:
        if month and d.month == int(month):
            score += 1
    except (TypeError, ValueError):
        pass
    return score


def choose_period(a_start, a_end, b_start, b_end, year=None, month=None):
    a_ok = _valid_range(a_start, a_end)
    b_ok = _valid_range(b_start, b_end)

    if a_ok and b_ok:
        if str(a_start) == str(b_start) and str(a_end) == str(b_end):
            return b_start, b_end

        a_score = _coherence_score(a_end, year, month)
        b_score = _coherence_score(b_end, year, month)

        if a_score > b_score:
            return a_start, a_end
        return b_start, b_end  # B si empatan

    if b_ok:
        return b_start, b_end
    if a_ok:
        return a_start, a_end
    return None, None


def _year_month(a: dict, b: dict):
    year = nullable(a.get("año")) or nullable(b.get("año"))
    month = nullable(a.get("mes")) or nullable(b.get("mes"))

    end_candidate = nullable(b.get("finPeriodo")) or nullable(a.get("finPeriodo"))
    parsed = _parse_date(end_candidate)

    if not year and parsed:
        year = parsed.year
    if not month and parsed:
        month = parsed.month

    if month:
        try:
            month = f"{int(month):02d}"
        except (TypeError, ValueError):
            month = str(month).strip().zfill(2)

    return year, month


def consolidate(
    file_id: str,
    web_link: str,
    a: dict,
    b: dict,
    office: str | None = None,
) -> dict:
    a_account = digits(a.get("cuenta")) or None
    b_account = digits(b.get("cuenta")) or None
    account_conflict = bool(a_account and b_account and a_account != b_account)
    account = None if account_conflict else (b_account or a_account)

    a_invoice = nullable(a.get("numeroFactura"))
    b_invoice = nullable(b.get("numeroFactura"))

    invoice = None
    for candidate in (b_invoice, a_invoice):
        if candidate and digits(candidate) != account:
            invoice = candidate
            break

    consumption = choose_consumption(a.get("kwh"), b.get("kwh"))

    year, month = _year_month(a, b)
    start, end = choose_period(
        a.get("inicioPeriodo"),
        a.get("finPeriodo"),
        b.get("inicioPeriodo"),
        b.get("finPeriodo"),
        year=year,
        month=month,
    )

    notes = []
    if account_conflict:
        notes.append("Número de cuenta contradictorio entre las dos lecturas")
    if not office:
        notes.append("Sede no identificada desde la carpeta de origen")
    total_a, total_b = _num(a.get("valorFacturado")), _num(b.get("valorFacturado"))
    total = total_b if total_b is not None else total_a
    if total_a is not None and total_b is not None and abs(total_a - total_b) > 1:
        total = None
        notes.append("Valor facturado contradictorio entre las dos lecturas")
    if nullable(a.get("notes")):
        notes.append(f"Observaciones A: {a['notes']}")
    if nullable(b.get("notes")):
        notes.append(f"Observaciones B: {b['notes']}")

    result = {
        "ID": file_id,
        "Codigo": None,
        "Oficina": nullable(office),
        "Cuenta": account,
        "NumeroFactura": invoice,
        "kWh": consumption,
        "ValorFacturado": total,
        "Año": year,
        "Mes": month,
        "InicioPeriodo": start,
        "FinPeriodo": end,
        "LinkFactura": web_link,
        "Observaciones": " | ".join(notes) or None,
    }
    return clean_strings(result)
