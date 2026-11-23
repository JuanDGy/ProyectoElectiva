"""Configuración común de las pruebas.

En Windows, pytest usa por defecto %TEMP%/pytest-of-<usuario> para todas las
sesiones. Si dos ejecuciones coinciden, la segunda puede fallar con
``PermissionError [WinError 5]`` al cambiar permisos o limpiar directorios que
la primera todavía está usando. Cada proceso usa su propia raíz temporal y las
raíces de procesos ya terminados se eliminan al iniciar la siguiente sesión.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_OWN_BASETEMP: Path | None = None


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_stale_basetemps(root: Path) -> None:
    for candidate in root.glob("pytest-of-*"):
        pid_text = candidate.name.removeprefix("pytest-of-")
        if not pid_text.isdigit() or int(pid_text) == os.getpid():
            continue
        if not _process_alive(int(pid_text)):
            shutil.rmtree(candidate, ignore_errors=True)


def pytest_configure(config):
    global _OWN_BASETEMP
    if config.option.basetemp is None:
        temp_root = Path(tempfile.gettempdir())
        _remove_stale_basetemps(temp_root)
        _OWN_BASETEMP = temp_root / f"pytest-of-{os.getpid()}"
        config.option.basetemp = str(_OWN_BASETEMP)
        config.add_cleanup(lambda: shutil.rmtree(_OWN_BASETEMP, ignore_errors=True))
