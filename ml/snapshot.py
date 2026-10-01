"""Snapshot versionado del dataset: Sheet -> CSV + MD5 + timestamp (reproducibilidad)."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import get_ml_settings
from services.sheets import SheetsService

RAW_DIR = Path("data/raw")
MANIFEST = Path("data/manifest.jsonl")


def take_snapshot(
    rows: list[dict],
    raw_dir: Path = RAW_DIR,
    manifest: Path = MANIFEST,
) -> dict:
    if not rows:
        raise RuntimeError("La hoja BD_energia no tiene filas para respaldar.")
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = raw_dir / f"bd_energia_{stamp}.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8")

    md5 = hashlib.md5(csv_path.read_bytes()).hexdigest()
    entry = {
        "file": csv_path.as_posix(),
        "md5": md5,
        "rows": len(rows),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with manifest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def latest_snapshot(
    raw_dir: Path = RAW_DIR, manifest: Path = MANIFEST
) -> dict | None:
    if not manifest.exists():
        return None
    entries = [
        json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    entries = [e for e in entries if Path(e["file"]).exists()]
    return entries[-1] if entries else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Respaldar BD_energia a CSV versionado.")
    parser.add_argument("--sheet", default=None)
    parser.add_argument("--raw-dir", default=str(RAW_DIR))
    parser.add_argument("--manifest", default=str(MANIFEST))
    args = parser.parse_args()

    settings = get_ml_settings()
    sheets = SheetsService(
        settings.google_client_secret_file,
        settings.google_token_file,
        settings.spreadsheet_id,
    )
    rows = sheets.get_rows(args.sheet or settings.sheet_bd_energy)
    entry = take_snapshot(rows, Path(args.raw_dir), Path(args.manifest))
    print(f"Snapshot: {entry['file']} | filas={entry['rows']} | md5={entry['md5']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
