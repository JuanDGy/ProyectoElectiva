"""Extracción local y determinista de campos de facturas ENEL."""
from __future__ import annotations

from datetime import date
import re
import unicodedata


MONTHS = dict(zip(('ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC'), range(1, 13)))
NUMBER = r'\d+(?:[.,]\d+)*'
DATE = r'(?<!\d)(\d{1,2})\s*[/ -]?\s*(ENE(?:RO)?|FEB(?:RERO)?|MAR(?:ZO)?|ABR(?:IL)?|MAY(?:O)?|JUN(?:IO)?|JUL(?:IO)?|AGO(?:STO)?|SEP(?:TIEMBRE)?|OCT(?:UBRE)?|NOV(?:IEMBRE)?|DIC(?:IEMBRE)?)\s*[/ I-]*\s*(20\d{2})(?!\d)'


def _normalize(text: str) -> str:
    text = unicodedata.normalize('NFD', text.upper())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return re.sub(r'TOTAL\s*A(?:L)?\s*PAGAR', 'TOTAL A PAGAR', text)


def _commercial(text: str) -> bool:
    return 'ENEL' in text and 'CONSUMO ACTIVA SENCILLA' in text and 'SUBTOTAL VALOR CONSUMO' in text


def _number(value: str) -> float:
    # Colombian invoices use groups of three digits for thousands and one or
    # two decimal digits. No guessed correction of OCR digits or currency signs.
    value = value.strip()
    if re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+', value):
        return float(re.sub(r'[.,]', '', value))
    if ',' in value:
        return float(value.replace('.', '').replace(',', '.'))
    return float(value)


def _unique(values, field: str, notes: list[str]):
    values = set(values)
    if len(values) > 1:
        notes.append(f'{field} contradictorio entre las apariciones del texto')
        return None
    return next(iter(values), None)


def _totals(text: str):
    lines = text.splitlines()
    blockers = r'TOTAL\s+ENERGIA|TOTAL\s+ASEO|PORTAFOLIO|FECHA\s+DE\s+SUSPENSION|PAGO\s+OPORTUNO|OTROS\s+COBROS'
    for index, line in enumerate(lines):
        label = re.search(r'TOTAL\s+A\s+PAGAR\s*[:=]?\s*', line)
        if not label:
            continue
        candidates = [line[label.end():], *lines[index + 1:index + 9]]
        for offset, candidate in enumerate(candidates):
            if re.search(blockers, candidate):
                break
            match = re.fullmatch(r'\s*\$?\s*(' + NUMBER + r')\s*(?:COP|PESOS)?\s*', candidate)
            if match:
                if offset > 0 and '$' not in candidate and not re.search(r'[.,]', match[1]):
                    continue
                yield _number(match[1])
                break


def _consumptions(text: str):
    for label in re.finditer(r'CONSUMO\s+(?:MES|DEL\s+MES|ACTUAL|FACTURADO|DEL\s+PERIODO)\s*:', text):
        block = text[label.end():label.end() + 240]
        block = re.split(r'PAGO\s+OPORTUNO|TOTAL\s+A\s+PAGAR', block)[0]
        match = re.search(r'(?<![\d.,])(' + NUMBER + r')\s*KWH\b', block)
        if match:
            yield _number(match[1])
    if _commercial(text):
        # Commercial ENEL table: current/previous meter, difference, factor,
        # consumed/billed active energy, unit price and charged value.
        numeric_line = r'\s*(' + NUMBER + r')\s*\n'
        money_line = r'\s*\$\s*(' + NUMBER + r')\s*(?:\n|$)'
        pattern = r'CONSUMO ACTIVA SENCILLA\s*\n' + numeric_line * 6 + money_line * 2
        for row in re.finditer(pattern, text):
            current, previous, difference, factor, consumed, billed, rate, charged = map(_number, row.groups())
            if (factor > 0 and billed >= 0
                    and abs(current - previous - difference) < .01
                    and abs(difference * factor - consumed) < .01
                    and abs(consumed - billed) < .01
                    and abs(billed * rate - charged) <= billed * .01 + 1):
                yield billed


def _periods(text: str):
    for label in re.finditer(r'PERIODO\s+(?:FACTURADO|DE\s+CONSUMO|CONSUMO)\s*:', text):
        block = text[label.end():label.end() + 240]
        block = re.split(r'PAGO\s+OPORTUNO|FECHA\s+DE\s+SUSPENSION', block)[0]
        dates = list(re.finditer(DATE, block))
        if len(dates) < 2:
            continue
        try:
            start, end = [date(int(m[3]), MONTHS[m[2][:3]], int(m[1])) for m in dates[:2]]
        except ValueError:
            continue
        if start <= end:
            yield start.isoformat(), end.isoformat()
    if _commercial(text):
        # These invoices store the range as native text but its label is an
        # image. Require the specific commercial table before using a range.
        for match in re.finditer(DATE + r'\s+(?:A|AL)\s+' + DATE, text):
            try:
                start = date(int(match[3]), MONTHS[match[2][:3]], int(match[1]))
                end = date(int(match[6]), MONTHS[match[5][:3]], int(match[4]))
            except ValueError:
                continue
            if start <= end:
                yield start.isoformat(), end.isoformat()


def extract_invoice(text: str) -> dict:
    text = _normalize(text)
    notes: list[str] = []
    # ENEL's client number has seven base digits and a verification digit;
    # invoice numbers have nine base digits and a verification digit.
    accounts = re.findall(r'(?<![\d.,])\b(\d{7}\s*-\s*\d)\b', text)
    accounts += re.findall(r'NUMERO\s+DE\s+CLIENTE\s*(?:ES)?\s*:\s*(\d{8})(?!\d)', text)
    account = _unique((re.sub(r'\D', '', x) for x in accounts), 'Cuenta', notes)
    invoices = re.findall(r'(?<!\d)\b(\d{9}\s*-\s*\d)\b', text)
    invoice = _unique((re.sub(r'\s', '', x) for x in invoices), 'Numero de factura', notes)
    consumption = _unique(_consumptions(text), 'Consumo', notes)
    total = _unique(_totals(text), 'Valor facturado', notes)
    period = _unique(_periods(text), 'Periodo', notes)
    start, end = period if period else (None, None)
    result = {
        'Codigo': None, 'Cuenta': account, 'NumeroFactura': invoice,
        'kWh': consumption, 'ValorFacturado': total,
        'InicioPeriodo': start, 'FinPeriodo': end,
        'Año': int(end[:4]) if end else None, 'Mes': end[5:7] if end else None,
    }
    missing = [key for key, value in result.items() if key != 'Codigo' and value is None]
    if missing:
        notes.append('No se pudieron validar con reglas: ' + ', '.join(missing))
    result['Observaciones'] = ' | '.join(notes) or None
    return result
