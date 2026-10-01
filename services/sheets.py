from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from googleapiclient.discovery import build

from services.google_auth import get_credentials


def _norm_header(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.lower().strip()
    return re.sub(r"[^a-z0-9]+", "", value)


class SheetsService:
    def __init__(self, client_secret_file: str, token_file: str, spreadsheet_id: str):
        creds = get_credentials(client_secret_file, token_file)
        self.api = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.spreadsheet_id = spreadsheet_id

    def get_rows(self, sheet_name: str) -> list[dict]:
        result = self.api.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'",
        ).execute()
        values = result.get("values", [])
        if not values:
            return []

        headers = values[0]
        rows = []
        for raw in values[1:]:
            padded = list(raw) + [""] * (len(headers) - len(raw))
            rows.append(dict(zip(headers, padded)))
        return rows

    def ensure_headers(self, sheet_name: str, headers: list[str]):
        values = self.api.spreadsheets().values()
        first = values.get(spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!1:1").execute().get("values", [])
        if first:
            current = first[0]
            compatible = len(current) <= len(headers) and all(
                _norm_header(a) == _norm_header(b) for a, b in zip(current, headers)
            )
            if not compatible:
                # Older runs appended invoice rows to a sheet without headers.
                legacy_invoice = len(current) >= 12 and bool(re.fullmatch(r"[\w-]{20,}", str(current[0]))) and "drive.google.com" in str(current[10])
                if not legacy_invoice:
                    raise RuntimeError(f"Encabezados incompatibles en {sheet_name}; no se modificó la hoja")
                info = self.api.spreadsheets().get(spreadsheetId=self.spreadsheet_id, fields="sheets(properties(sheetId,title))").execute()
                sheet_id = next(s["properties"]["sheetId"] for s in info["sheets"] if s["properties"]["title"] == sheet_name)
                self.api.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": [{"insertDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1}}}]}).execute()
        values.update(spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A1", valueInputOption="RAW", body={"values": [headers]}).execute()

    def append(self, sheet_name: str, values: Iterable, headers: list[str] | None = None) -> dict:
        if headers:
            self.ensure_headers(sheet_name, headers)
        return self.api.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [list(values)]},
        ).execute()

