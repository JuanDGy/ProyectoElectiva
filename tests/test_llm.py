import pytest

from services.llm import (cross_check, gemini_settings, normalize_payload, stated_total)


def test_normalize_payload_keeps_digits_and_dates():
    data = normalize_payload({
        "cuenta": "0466730-9",
        "factura": "351236809-3",
        "kwh": "502,0",
        "valor_facturado": "$ 1.234.500",
        "inicio_periodo": "2025-03-28",
        "fin_periodo": "2025-04-28",
        "confianza": "ALTA",
        "evidencias": [{"campo": "cuenta", "cita": "0466730-9"}],
    })
    assert data["Cuenta"] == "04667309"
    assert data["NumeroFactura"] == "351236809-3"
    assert data["kWh"] == 502.0
    assert data["ValorFacturado"] == 1234500.0
    assert data["InicioPeriodo"] == "2025-03-28"
    assert data["confianza"] == "alta"
    assert data["evidencias"] == [{"campo": "cuenta", "cita": "0466730-9"}]


def test_normalize_payload_leaves_missing_fields_empty():
    data = normalize_payload({"cuenta": "", "kwh": "", "inicio_periodo": "28/03/2025"})
    assert data["Cuenta"] == ""
    assert data["kWh"] is None
    assert data["InicioPeriodo"] is None


def test_stated_total_reads_zero_and_thousands():
    assert stated_total("TOTAL A PAGAR\n$0\nOtros") == 0.0
    assert stated_total("TOTAL A PAGAR $24.880") == 24880.0
    assert stated_total("TOTAL A PAGAR: 1.234.500") == 1234500.0
    assert stated_total("sin etiqueta") is None


def test_cross_check_flags_the_wrong_total_when_document_says_zero():
    data = normalize_payload({"valor_facturado": "430930", "cuenta": "04667309"})
    warnings = cross_check(data, "NÚMERO DE CLIENTE 0466730-9\nTOTAL A PAGAR\n$0\nTOTAL ENERGÍA $430.930")
    assert any("TOTAL A PAGAR 0" in warning for warning in warnings)


def test_cross_check_accepts_a_consistent_row():
    data = normalize_payload({
        "cuenta": "0467600-7", "factura": "354949957-1",
        "valor_facturado": "0", "inicio_periodo": "2025-04-29", "fin_periodo": "2025-05-28",
    })
    text = "NÚMERO DE CLIENTE 0467600-7\nDocumento 354949957-1\nTOTAL A PAGAR\n$0"
    assert cross_check(data, text) == []


def test_gemini_settings_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        gemini_settings()


def test_gemini_settings_reads_model_and_timeout(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x" * 10)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "45")
    settings = gemini_settings()
    assert settings["model"] == "gemini-3.1-flash-lite"
    assert settings["timeout"] == 45.0
