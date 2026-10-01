import pytest

from pipeline.invoice_rules import extract_invoice


COMMERCIAL = '''ENEL COLOMBIA S.A. ESP.
0452522-0
347783445-0
27 FEB/2025 A 27 MAR/2025
CONSUMO ACTIVA SENCILLA
196.430
195.316
1.114
1
1.114
1.114
$843,71
$939.888
CONSUMO REACTIVA SENCILLA
0
0
0
0
0
0
$272,98
$0
SUBTOTAL VALOR CONSUMO
TOTAL A PAGAR
$0
'''


def test_commercial_invoice_reads_billed_active_energy_and_period():
    result = extract_invoice(COMMERCIAL)
    assert result['kWh'] == 1114
    assert result['InicioPeriodo'] == '2025-02-27'
    assert result['FinPeriodo'] == '2025-03-27'
    assert result['Cuenta'] == '04525220'
    assert result['ValorFacturado'] == 0


def test_commercial_invoice_does_not_use_reactive_energy():
    result = extract_invoice(COMMERCIAL.replace('CONSUMO ACTIVA SENCILLA', 'OTRO CONCEPTO'))
    assert result['kWh'] is None


def test_incomplete_consumption_table_requires_review():
    assert extract_invoice(COMMERCIAL.replace('196.430\n', ''))['kWh'] is None


def test_inconsistent_meter_readings_require_review():
    assert extract_invoice(COMMERCIAL.replace('196.430', '296.430'))['kWh'] is None


def test_total_label_split_into_lines_is_supported():
    assert extract_invoice(COMMERCIAL.replace('TOTAL A PAGAR', 'Totala\nPagar'))['ValorFacturado'] == 0


def test_multiple_commercial_periods_require_review():
    result = extract_invoice(COMMERCIAL + '\n28 MAR/2025 A 28 ABR/2025')
    assert result['InicioPeriodo'] is None


def test_numeric_daterange_without_commercial_context_is_not_used():
    assert extract_invoice('27 FEB/2025 A 27 MAR/2025')['InicioPeriodo'] is None
