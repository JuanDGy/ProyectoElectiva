"""Orquestación reproducible para publicar SIEMPRE en Predicciones_Cuenta.

Modo normal: usa el modelo automáticamente seleccionado y sincroniza la pestaña.
Modo completo: refresca BD_energia, compara y reentrena antes de sincronizar.
La escritura en Sheets exige --publicar (en ambos modos).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def build_steps(*, complete: bool = False, publish: bool = False,
                year: int | None = None, month: int | None = None,
                charts: bool = True):
    if (year is None) != (month is None):
        raise ValueError("Indica --anio y --mes juntos, o ninguno para usar el siguiente mes del histórico.")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("El mes debe estar entre 1 y 12.")
    if year is not None and year < 1900:
        raise ValueError("Año inválido.")
    stages = []
    if complete:
        stages.extend([
            ("Descargar BD_energia a un snapshot", "ml.snapshot", []),
            ("Evaluar las 13 familias sobre ese snapshot", "scripts.experiment_models",
             ["--models", "all", "--approaches", "level,delta,ratio"]),
            ("Entrenar y elegir el menor MAE de validación", "ml.train_auto",
             ["--modelos", "all", "--seleccion", "automatica"]),
        ])
    prediction_args = ["--model-path", "auto"]
    if year is not None:
        prediction_args.extend(["--year", str(year), "--month", str(month)])
    if publish:
        prediction_args.extend(["--write", "--sync"])
    stages.append(("Sincronizar Predicciones_Cuenta" if publish
                   else "Previsualizar Predicciones_Cuenta",
                   "ml.publish", prediction_args))
    if publish and charts:
        chart_args = ["--write"]
        if year is not None:
            chart_args.extend(["--year", str(year), "--month", str(month)])
        stages.append(("Actualizar Graficos_Predicciones", "ml.sheets_dashboard",
                       chart_args))
    return stages


def update_predictions(*, complete: bool = False, publish: bool = False,
                       year: int | None = None, month: int | None = None,
                       charts: bool = True, runner=None):
    steps = build_steps(complete=complete, publish=publish,
                        year=year, month=month, charts=charts)
    execute = runner or subprocess.run
    print("EcoData ECCI | Actualización integrada de predicciones", flush=True)
    print("Destino de resultados: hoja Predicciones_Cuenta", flush=True)
    if not publish:
        print("Modo lectura de Sheets: agrega --publicar para sincronizar.", flush=True)
        if complete:
            print("ATENCIÓN: --completo sí genera nuevos archivos y modelos locales.",
                  flush=True)
    for index, (description, module, args) in enumerate(steps, 1):
        command = [sys.executable, "-m", module, *args]
        print(f"[{index}/{len(steps)}] {description}", flush=True)
        execute(command, cwd=PROJECT_ROOT, check=True)
    print("Flujo terminado. Consulta Predicciones_Cuenta en Google Sheets."
          if publish else "Vista previa terminada; Sheets no modificado.",
          flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Actualizar las predicciones vigentes en Predicciones_Cuenta.")
    parser.add_argument("--completo", action="store_true",
                        help="Snapshot + competencia + entrenamiento + publicación")
    parser.add_argument("--publicar", action="store_true",
                        help="Escribir Predicciones_Cuenta y actualizar gráficos")
    parser.add_argument("--anio", type=int)
    parser.add_argument("--mes", type=int)
    parser.add_argument("--sin-graficos", action="store_true",
                        help="No actualizar Graficos_Predicciones")
    args = parser.parse_args(argv)
    try:
        update_predictions(complete=args.completo, publish=args.publicar,
                           year=args.anio, month=args.mes,
                           charts=not args.sin_graficos)
    except ValueError as e:
        parser.error(str(e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
