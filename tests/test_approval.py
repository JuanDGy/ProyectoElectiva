"""Aprobaciones de lotes vinculadas al documento y separadas de la aplicación."""
from pipeline.approval import approve, has_valid_approval
from pipeline.pending import apply_proposals
from test_pending import FakeDrive, FakeSheets, proposal, settings


def test_manual_approval_requires_exact_confirmation():
    candidate = proposal(pdf_sha256="a" * 64)
    messages = []
    updated, stats = approve([candidate], confirm=lambda prompt: "APROBAR",
                             output=messages.append)
    assert stats["aprobadas"] == 1
    assert has_valid_approval(updated[0])
    assert any("drive.google.com" in message for message in messages)

    skipped, stats = approve([candidate], confirm=lambda prompt: "si", output=lambda *args: None)
    assert stats["rechazadas"] == 1
    assert not has_valid_approval(skipped[0])


def test_approval_is_bound_to_pdf_digest():
    updated, _ = approve([proposal(pdf_sha256="a" * 64)],
                         confirm=lambda prompt: "APROBAR", output=lambda *args: None)
    changed = dict(updated[0], pdf_sha256="b" * 64)
    assert has_valid_approval(updated[0])
    assert not has_valid_approval(changed)


def test_real_apply_path_rejects_unreviewed_proposals(tmp_path):
    drive, sheet = FakeDrive(), FakeSheets()
    result = apply_proposals([proposal()], tmp_path / "audit.jsonl",
                             drive=drive, sheets=sheet, settings=settings(),
                             require_review=True)
    assert result["omitidas"] == 1
    assert not drive.moved and not sheet.written
    assert "SIN_APROBACION_MANUAL" in (tmp_path / "audit.jsonl").read_text()


def test_real_apply_path_accepts_manual_approval(tmp_path):
    candidate = proposal(pdf_sha256="a" * 64)
    updated, _ = approve([candidate], confirm=lambda prompt: "APROBAR",
                         output=lambda *args: None)
    drive, sheet = FakeDrive(), FakeSheets()
    result = apply_proposals(updated, tmp_path / "audit.jsonl",
                             drive=drive, sheets=sheet, settings=settings(),
                             require_review=True)
    assert result["aplicadas"] == 1
    assert len(sheet.written) == 1
