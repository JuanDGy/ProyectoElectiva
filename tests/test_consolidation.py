from pipeline.consolidation import choose_consumption, consolidate


def test_consumption_equal():
    assert choose_consumption(100, 100.4) == 100.4


def test_consumption_one_unit_uses_max():
    assert choose_consumption(100, 101) == 101


def test_consumption_far_apart_returns_none():
    assert choose_consumption(100, 105) is None


def test_consolidation_prefers_b_account_and_invoice():
    a = {
        "numeroFactura": "F-A",
        "kwh": 100,
        "año": 2026,
        "mes": 9,
        "inicioPeriodo": "2026-08-01",
        "finPeriodo": "2026-08-31",
        "notes": "A",
    }
    b = {
        "cuenta": "12-345",
        "numeroFactura": "F-B",
        "kwh": 100,
        "año": 2026,
        "mes": 9,
        "inicioPeriodo": "2026-08-01",
        "finPeriodo": "2026-08-31",
        "notes": "B",
    }

    result = consolidate(
        "id1",
        "https://drive.test",
        a,
        b,
        office="Principal",
    )

    assert result["Cuenta"] == "12345"
    assert result["NumeroFactura"] == "F-B"
    assert result["Oficina"] == "Principal"
    assert result["Codigo"] is None
