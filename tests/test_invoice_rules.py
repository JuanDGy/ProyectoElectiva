import pytest

from pipeline.invoice_rules import extract_invoice


INVOICE = '''Para pagos y consultas tu número de cliente es:
0752759-6
FACTURA DE SERVICIOS PUBLICOS No. 357250391-1
PERIODO FACTURADO: 20 MAY/2025 A 18 JUN/2025
CONSUMO MES: 59410 kWh
Total Energía $53.194.240
Total Aseo $331.410
TOTAL A PAGAR
$53.525.650
'''


def test_enel_extracts_fields_using_labels_and_spanish_dates():
    result = extract_invoice(INVOICE)
    assert {key: result[key] for key in ('Cuenta', 'NumeroFactura', 'kWh', 'ValorFacturado', 'InicioPeriodo', 'FinPeriodo', 'Año', 'Mes')} == {
        'Cuenta': '07527596', 'NumeroFactura': '357250391-1', 'kWh': 59410,
        'ValorFacturado': 53525650, 'InicioPeriodo': '2025-05-20',
        'FinPeriodo': '2025-06-18', 'Año': 2025, 'Mes': '06',
    }


def test_ocr_period_with_table_labels_and_separator_i():
    text = INVOICE.replace('PERIODO FACTURADO: 20 MAY/2025 A 18 JUN/2025',
        'PERIODO FACTURADO:\nDÍAS FACTURADOS:\nCONSUMO MES:\nClientes Colombia\núltimos 6 meses\n20 MAYI2025 A 18 JUNI2025')
    result = extract_invoice(text)
    assert result['InicioPeriodo'] == '2025-05-20'
    assert result['FinPeriodo'] == '2025-06-18'


def test_conflicting_total_is_not_silently_corrected():
    result = extract_invoice(INVOICE + '\nTOTAL A PAGAR\n853.525.650')
    assert result['ValorFacturado'] is None
    assert 'contradictorio' in result['Observaciones'].lower()


def test_repeated_identical_totals_are_valid():
    assert extract_invoice(INVOICE + '\nTOTAL A PAGAR\n$53.525.650')['ValorFacturado'] == 53525650


def test_energy_subtotal_is_not_used_as_total_to_pay():
    assert extract_invoice(INVOICE.split('TOTAL A PAGAR')[0])['ValorFacturado'] is None


def test_payment_date_is_not_used_as_consumption_period():
    text = INVOICE.replace('PERIODO FACTURADO: 20 MAY/2025 A 18 JUN/2025',
                           'PAGO OPORTUNO: 03 JUL/2025\nFECHA DE SUSPENSIÓN: 07 JUL/2025')
    result = extract_invoice(text)
    assert result['InicioPeriodo'] is None
    assert result['FinPeriodo'] is None


@pytest.mark.parametrize('value,expected', [('0', 0), ('1.980,33', 1980.33), ('59,410', 59410)])
def test_consumption_numbers(value, expected):
    assert extract_invoice(INVOICE.replace('59410', value))['kWh'] == expected


def test_invalid_date_requires_review():
    assert extract_invoice(INVOICE.replace('20 MAY/2025', '32 MAY/2025'))['InicioPeriodo'] is None


def test_conflicting_account_requires_review():
    assert extract_invoice(INVOICE + '\nNúmero de cliente: 0326876-6')['Cuenta'] is None


def test_table_consumption_separated_from_label():
    text = INVOICE.replace('CONSUMO MES: 59410 kWh', 'CONSUMO MES:\nClientes Colombia\núltimos 6 meses\n20 MAYI2025 A 18 JUNI2025\n30\n59410 kWh\n15.039 kWh')
    assert extract_invoice(text)['kWh'] == 59410


def test_barcode_fragment_is_not_a_payment_amount():
    text = INVOICE.split('TOTAL A PAGAR')[0] + '\nTOTAL A PAGAR\n0752759-6\n(415)7707209914253\n900\n000053525650\nFACTURA DE SERVICIOS PUBLICOS\n$53.525.650'
    assert extract_invoice(text)['ValorFacturado'] == 53525650
