# MLOps — EcoData ECCI

Ciclo de vida del modelo predictivo de consumo (cero costo, local).
El modelo vive **desacoplado** de la ingesta de PDFs: la ingesta escribe en
`BD_energia` y el ciclo ML solo lee snapshots versionados.

## Componentes

| Pieza | Ubicación | Rol |
|---|---|---|
| Snapshot de datos | `ml/snapshot.py` → `data/raw/*.csv` + `data/manifest.jsonl` | Respaldo reproducible: cada CSV lleva MD5, filas y timestamp |
| Dataset/features | `ml/dataset.py` | Normaliza columnas, valida calidad, crea rezagos (`lag_1`, `lag_2`, `rolling_3`), split temporal (test = últimos 2 periodos), métricas MAE/RMSE/R²/MAPE |
| Entrenamiento | `ml/train.py` | Entrena `linear` o `random_forest`, registra en MLflow (`mlruns/`, experimento `ecodata-consumo`) y guarda el candidato versionado |
| Registro | `ml/registry.py` + `models/registry.json` | `champion` (en producción) vs `challengers` (candidatos) |
| Promoción | `ml/promote.py` | Solo promueve si el candidato **mejora** la métrica (por defecto MAPE) |
| Predicción | `ml/predict.py` | Calcula rezagos automáticamente desde el histórico y predice con el champion; opcionalmente escribe en `Predicciones` |
| Monitoreo | `ml/monitor.py` → `reports/` | Drift por sede (media reciente vs baseline), huecos de cobertura, vigencia del champion. Exit `0` = OK, `2` = reentrenar |

## Ciclo de vida

```bash
# 1. Respaldar el dataset consolidado (tras procesar facturas)
uv run python -m ml.snapshot

# 2. Entrenar candidatos (usa el último snapshot; o --data otro CSV)
uv run python -m ml.train --model linear
uv run python -m ml.train --model random_forest

# 3. Ver experimentos
uv run mlflow ui   # http://127.0.0.1:5000, experimento ecodata-consumo

# 4. Comparar y promover (solo si mejora el MAPE del champion)
uv run python -m ml.promote --list
uv run python -m ml.promote --candidate <run_id> --metric mape

# 5. Predecir (rezagos automáticos, sin escribir en Sheets)
uv run python -m ml.predict --sede CAMPUS --year 2026 --month 4 --no-log

# 6. Monitorear (revisión bimestral o tras cambios de tarifa)
uv run python -m ml.monitor            # exit 2 => reentrenar desde el paso 2
```

## Políticas

- **Datos:** nunca se entrena directo de Sheets en producción; siempre desde un
  snapshot con MD5 registrado (reproducibilidad absoluta).
- **Split:** evaluación fuera de tiempo (últimos 2 periodos globales), no split
  aleatorio: el objetivo es el mes siguiente.
- **Promoción:** un challenger solo reemplaza al champion con mejora medible;
  el champion anterior queda archivado como challenger (rollback trivial).
- **Reentrenamiento:** revisión bimestral + reentrenamiento ante drift
  (cambio ≥ 25 % en la media reciente de una sede), cambio de tarifas o
  champion con más de 60 días. Umbrales configurables (`--drift-threshold`).
- **Git local:** `main` conserva la versión conciliada y la rama
  `integracion/mlops` registra esta integración. No hay remoto ni GitHub Actions
  configurados; su publicación y CI quedan pendientes.
- **Secretos:** credenciales solo por `.env` / GitHub Secrets; `models/*.joblib`,
  `mlruns/`, `data/raw/` y `reports/` están ignorados por git.

## SLO

- Objetivo de ingesta: ≥ 98 % de ejecuciones sin errores fatales. El OCR local
  depende del tamaño del documento y puede tardar varios minutos; no se valida
  un objetivo de menos de cinco segundos por factura.
- Modelo: MAPE reportado en lenguaje de negocio + MAE/RMSE/R² para auditoría.
