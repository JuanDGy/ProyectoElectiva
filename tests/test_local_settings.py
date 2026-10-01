from app.config import get_settings


def test_pipeline_settings_do_not_require_external_ai_credentials(monkeypatch):
    for name in ('GEMINI_API_KEY', 'GEMINI_EXTRACT_MODEL', 'GEMINI_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    for name in ('DRIVE_FOLDER_01A', 'DRIVE_FOLDER_01B', 'DRIVE_FOLDER_01C',
                 'DRIVE_FOLDER_01D', 'DRIVE_FOLDER_02_ENERGIA', 'GOOGLE_SPREADSHEET_ID'):
        monkeypatch.setenv(name, 'test-id')
    settings = get_settings()
    assert settings.drive_01a == 'test-id'
    assert settings.spreadsheet_id == 'test-id'
