"""Detecta cambios reales en BD_energia y sincroniza las predicciones por cuenta.

Pensado para ejecutarse mediante systemd --user (timer) o manualmente.
No presupone que una escritura de Google Sheets lance Python por sí sola.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from app.config import get_ml_settings
from services.sheets import SheetsService

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data/ml/bd_prediction_sync_state.json"
LOCK_FILE = ROOT / "data/ml/bd_prediction_sync.lock"


def _acquire_lock(handle) -> bool:
    """Bloqueo exclusivo no bloqueante, portable entre Linux y Windows."""
    if os.name == "nt":
        handle.seek(0)
        if not handle.read(1):
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _release_lock(handle):
    if os.name == "nt":
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        if not _acquire_lock(handle):
            yield False
            return
        try:
            yield True
        finally:
            _release_lock(handle)


def fingerprint(rows: list[dict]) -> str:
    """Huella estable ante un simple reordenamiento de filas en Sheets."""
    canonical = [json.dumps(row, ensure_ascii=False, sort_keys=True,
                            default=str, separators=(",", ":")) for row in rows]
    return hashlib.sha256("\n".join(sorted(canonical)).encode("utf-8")).hexdigest()


def _save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def monitor_once(*, check_only=False, force=False, state_path=STATE_FILE,
                 lock_path=LOCK_FILE, service=None, runner=None) -> dict:
    state_path, lock_path = Path(state_path), Path(lock_path)
    with _exclusive_lock(lock_path) as acquired:
        if not acquired:
            print("Otra sincronización de BD_energia está en ejecución; no duplicar.")
            return {"status": "ocupado"}
        settings = get_ml_settings()
        sheets = service or SheetsService(
            settings.google_client_secret_file,
            settings.google_token_file, settings.spreadsheet_id)
        before_rows = sheets.get_rows(settings.sheet_bd_energy, unformatted=True)
        if not before_rows:
            raise RuntimeError("BD_energia está vacía: sincronización detenida.")
        before_digest = fingerprint(before_rows)
        old_state = (json.loads(state_path.read_text(encoding="utf-8"))
                     if state_path.exists() else {})
        changed = force or old_state.get("last_success_digest") != before_digest
        if not changed:
            print(f"BD_energia sin cambios: {len(before_rows)} filas; "
                  "Predicciones_Cuenta no necesita recalcularse.")
            return {"status": "sin_cambios", "rows": len(before_rows),
                    "digest": before_digest}
        if check_only:
            print(f"BD_energia cambió ({len(before_rows)} filas); "
                  "se necesita nuevo snapshot y reentrenamiento.")
            return {"status": "cambios_pendientes", "rows": len(before_rows),
                    "digest": before_digest}

        # Nunca marcar una versión como procesada ANTES de completar la
        # publicación. Si falla, el timer podrá reintentar.
        run = runner or subprocess.run
        print(f"BD_energia cambió: {len(before_rows)} filas. "
              "Iniciando snapshot -> competencia -> modelos -> "
              "Predicciones_Cuenta -> gráficos.", flush=True)
        run([sys.executable, "-m", "ml.workflow", "--completo", "--publicar"],
            cwd=ROOT, check=True)
        after_rows = sheets.get_rows(settings.sheet_bd_energy, unformatted=True)
        after_digest = fingerprint(after_rows)
        if after_digest != before_digest:
            raise RuntimeError("BD_energia cambió durante el entrenamiento. "
                               "El próximo chequeo debe actualizar la nueva versión.")
        result = {
            "last_success_digest": before_digest,
            "last_success_at": datetime.now(timezone.utc).isoformat(),
            "last_success_rows": len(after_rows),
            "last_prediction_sheet": "Predicciones_Cuenta",
        }
        _save_state(state_path, result)
        print(f"Sincronización verificada: BD_energia ({len(after_rows)} filas) "
              "-> Predicciones_Cuenta (sin inventar consumos).")
        return {"status": "actualizado", "rows": len(after_rows),
                "digest": before_digest}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Detectar cambios en BD_energia y actualizar predicciones de todas las cuentas.")
    parser.add_argument("--solo-verificar", action="store_true",
                        help="Consultar BD_energia sin entrenar ni publicar")
    parser.add_argument("--forzar", action="store_true",
                        help="Reejecutar incluso si la huella de BD_energia no cambió")
    args = parser.parse_args(argv)
    monitor_once(check_only=args.solo_verificar, force=args.forzar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
