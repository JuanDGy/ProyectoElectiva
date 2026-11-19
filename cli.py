"""CLI unificada EcoData ECCI; ejecuta python cli.py --help o sin args."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def invoke(module=None, *params):
    cmd = [sys.executable]
    cmd += ["-m", module] if module else [str(ROOT / "main.py")]
    cmd += [str(p) for p in params if p is not None]
    print("Ejecutando:", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def status():
    path = ROOT / "models/registry.json"
    d = json.loads(path.read_text()) if path.exists() else {}
    files = sorted((ROOT / "data/llm_proposals").glob("pending_*.json"))
    print("EcoData ECCI | Estado")
    print("Champion:", (d.get("champion") or {}).get("id") or "Ninguno validado")
    print("Challengers:", len(d.get("challengers") or []))
    if files:
        last = json.loads(files[-1].read_text())
        print("Últimas propuestas:", last.get("resumen"))
    selected = ROOT / "models/auto_selection.json"
    if selected.exists():
        choice = json.loads(selected.read_text(encoding="utf-8"))
        print("Seleccionado por validación:", choice.get("selected_candidate"))
        print("Estado de selección:", choice.get("status"))
    print("Sheets: Predicciones_Cuenta, Historial_Predicciones y Graficos_Predicciones")
    return 0


def setup():
    p = argparse.ArgumentParser(description="EcoData: facturas, Gemini, ML y dashboard de Sheets.")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("estado", help="Resumen local sin usar Google")
    process = sub.add_parser("procesar", help="Procesar facturas nuevas")
    process.add_argument("--limite", type=int, default=1)
    history = sub.add_parser("historico", help="Importar Drive recursivamente")
    history.add_argument("--limite", type=int, default=10)
    history.add_argument("--todo", action="store_true")
    pred = sub.add_parser("predicciones", help="Ver/publicar pronósticos por cuenta")
    pred.add_argument("--modelo", help="Ruta al model.joblib si no hay champion")
    pred.add_argument("--anio", type=int)
    pred.add_argument("--mes", type=int)
    pred.add_argument("--publicar", action="store_true")
    pred.add_argument("--sincronizar", action="store_true",
                      help="Actualizar predicciones vigentes, archivar versiones anteriores y agregar nuevas")
    auto = sub.add_parser("entrenar", help="Entrenar familias, seleccionar mejor por validación y controlar Champion")
    auto.add_argument("--modelos", default="all", help="all o familias separadas por coma")
    auto.add_argument("--seleccion", choices=("automatica",), default="automatica")
    auto.add_argument("--reporte", help="Reporte experimental del snapshot actual")
    auto.add_argument("--evaluar", action="store_true",
                      help="Volver a ejecutar competencia antes del entrenamiento")
    auto.add_argument("--sin-promover", action="store_true")

    charts = sub.add_parser("graficos", help="Gráficos nativos de Google Sheets")
    charts.add_argument("--modelo", help="ID del modelo previamente publicado")
    charts.add_argument("--anio", type=int)
    charts.add_argument("--mes", type=int)
    charts.add_argument("--publicar", action="store_true")
    compare_sheets = sub.add_parser("comparativas", help="Tablas y gráficos OCR/LLM y modelos en Sheets")
    compare_sheets.add_argument("--publicar", action="store_true", help="Escribir las pestañas reservadas")
    compare_sheets.add_argument("--solo", choices=("todas", "ocr", "modelos"), default="todas")
    comp = sub.add_parser("competencia", help="Comparar modelos clásicos, RBF, MLP, SVR, LSTM")
    comp.add_argument("--modelos", default="all")
    comp.add_argument("--enfoques", default="level,delta,ratio")
    comp.add_argument("--inicio-test", default="2025-11-01")
    comp.add_argument("--fin-test", default="2025-12-01")
    ocr = sub.add_parser("comparar-ocr", help="Benchmark OCR vs Gemini")
    ocr.add_argument("--limite", type=int, default=15)
    ocr.add_argument("--semilla", type=int, default=7)
    pending = sub.add_parser("pendientes", help="Revisar o aplicar lote de facturas")
    pending.add_argument("accion", choices=("revisar", "aplicar", "auditar", "renovar", "aprobar"))
    pending.add_argument("--limite", type=int, default=10)
    pending.add_argument("--archivo", help="Archivo JSON revisado para aplicar")
    pending.add_argument("--sin-llm", action="store_true")
    pending.add_argument("--online", action="store_true", help="Solo auditoría: consultar Drive y Sheets")
    pending.add_argument("--verificar-pdf", action="store_true", help="Solo auditoría: cotejar SHA-256")
    return p


def dispatch(args, parser):
    cmd = args.command
    if cmd == "estado":
        return status()
    if cmd == "entrenar":
        return invoke("ml.train_auto", "--modelos", args.modelos,
                      "--seleccion", args.seleccion,
                      *(["--reporte", args.reporte] if args.reporte else []),
                      *(["--evaluar"] if args.evaluar else []),
                      *(["--sin-promover"] if args.sin_promover else []))
    if cmd == "procesar":
        if args.limite < 1:
            parser.error("--limite debe ser positivo")
        return invoke(None, "--limit", args.limite)
    if cmd == "historico":
        if args.todo:
            return invoke(None, "--history", "--all")
        return invoke(None, "--history", "--limit", args.limite)
    if cmd in ("predicciones", "graficos"):
        if (args.anio is None) != (args.mes is None):
            parser.error("Se requieren --anio y --mes juntos")
        options = []
        if args.modelo:
            options += ["--model-path" if cmd == "predicciones" else "--model", args.modelo]
        if args.anio is not None:
            options += ["--year", args.anio, "--month", args.mes]
        return invoke("ml.publish" if cmd == "predicciones" else "ml.sheets_dashboard",
                      *options, *(["--write"] if args.publicar else []),
                      *(["--sync"] if cmd == "predicciones" and args.sincronizar else []))
    if cmd == "comparativas":
        only = {"todas": "all", "ocr": "ocr", "modelos": "models"}[args.solo]
        return invoke("ml.comparisons", "--only", only,
                      *(["--write"] if args.publicar else []))
    if cmd == "competencia":
        return invoke("scripts.experiment_models", "--models", args.modelos,
                      "--approaches", args.enfoques, "--test-start", args.inicio_test,
                      "--test-end", args.fin_test)
    if cmd == "comparar-ocr":
        return invoke("scripts.compare_ocr_llm", "--limit", args.limite,
                      "--seed", args.semilla)
    if cmd == "pendientes":
        if args.accion == "aprobar":
            if not args.archivo:
                parser.error("Aprobar requiere --archivo JSON verificado")
            return invoke("pipeline.approval", "--from", args.archivo)
        if args.accion == "renovar":
            if not args.archivo:
                parser.error("Renovar requiere --archivo JSON de propuestas")
            return invoke("pipeline.refresh", "--from", args.archivo,
                          "--limit", args.limite)
        if args.accion == "auditar":
            if not args.archivo:
                parser.error("Auditar requiere --archivo JSON de propuestas")
            if args.verificar_pdf and not args.online:
                parser.error("--verificar-pdf requiere --online")
            return invoke("pipeline.reconcile", "--from", args.archivo,
                          *(["--live"] if args.online else []),
                          *(["--verify-pdf"] if args.verificar_pdf else []))
        if args.accion == "aplicar":
            if not args.archivo:
                parser.error("Requiere --archivo JSON; inspecciona antes los PDFs")
            return invoke("pipeline.pending", "--apply", "--from", args.archivo)
        return invoke("pipeline.pending", "--limit", args.limite,
                      *(["--no-llm"] if args.sin_llm else []))
    parser.print_help()
    return 0


def main(argv=None):
    p = setup()
    if argv is None:
        argv = sys.argv[1:]
    if not argv and sys.stdin.isatty():
        options = {
            "1": ["estado"], "2": ["procesar"], "3": ["historico"],
            "4": ["predicciones"], "5": ["graficos"],
            "6": ["competencia", "--modelos", "rbf,lstm,mlp,svr", "--enfoques", "level"],
            "7": ["comparar-ocr"], "8": ["pendientes", "revisar"],
            "9": ["comparativas"], "10": ["entrenar", "--modelos", "all"],
        }
        print("\nEcoData ECCI — Menú")
        for k, label in zip(options, ["Estado", "Ingesta (1)", "Histórico (10)",
                        "Predicciones (lectura)", "Gráficos Sheets (lectura)",
                        "Competencia neuronal", "Comparación OCR/LLM",
                        "Revisión de pendientes", "Comparativas en Sheets (vista previa)",
                        "Entrenar y seleccionar automáticamente"]):
            print(k + ". " + label)
        print("0. Salir")
        selection = input("Opción: ").strip()
        if selection == "0":
            return 0
        if selection not in options:
            print("Opción inválida")
            return 2
        argv = options[selection]
    return dispatch(p.parse_args(argv), p)


if __name__ == "__main__":
    raise SystemExit(main())
