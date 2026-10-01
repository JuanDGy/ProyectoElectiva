from __future__ import annotations

from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable obligatoria {name} en .env")
    return value


@dataclass(frozen=True)
class Settings:
    google_client_secret_file: str
    google_token_file: str

    drive_01a: str
    drive_01b: str
    drive_01c: str
    drive_01d: str
    drive_02_energy: str

    spreadsheet_id: str
    sheet_bd_energy: str
    sheet_adjust_energy: str
    sheet_predictions: str

    log_level: str
    pdf_min_text_characters: int = 100
    drive_history_root_id: str = ""


def get_settings() -> Settings:
    pdf_min_text_characters = int(os.getenv("PDF_MIN_TEXT_CHARACTERS", "100"))
    if pdf_min_text_characters < 1:
        raise ValueError("PDF_MIN_TEXT_CHARACTERS debe ser mayor que cero")
    return Settings(
        google_client_secret_file=os.getenv("GOOGLE_CLIENT_SECRET_FILE", "credentials.json"),
        google_token_file=os.getenv("GOOGLE_TOKEN_FILE", "token.json"),

        drive_01a=_required("DRIVE_FOLDER_01A"),
        drive_01b=_required("DRIVE_FOLDER_01B"),
        drive_01c=_required("DRIVE_FOLDER_01C"),
        drive_01d=_required("DRIVE_FOLDER_01D"),
        drive_02_energy=_required("DRIVE_FOLDER_02_ENERGIA"),

        spreadsheet_id=_required("GOOGLE_SPREADSHEET_ID"),
        sheet_bd_energy=os.getenv("SHEET_BD_ENERGIA", "BD_energia"),
        sheet_adjust_energy=os.getenv("SHEET_AJUSTE_ENERGIA", "Ajuste_Energia"),
        sheet_predictions=os.getenv("SHEET_PREDICCIONES", "Predicciones"),

        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        pdf_min_text_characters=pdf_min_text_characters,
        drive_history_root_id=os.getenv("DRIVE_HISTORY_ROOT_ID", ""),
    )
