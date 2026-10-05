# MLOps — EcoData ECCI, primera versión por cuenta

La ingesta escribe en `BD_energia`. ML trabaja exclusivamente con snapshots locales versionados y no modifica la hoja. La v1 predice consumo eléctrico en kWh, a un mes, por **Cuenta**. Costos, agua y totales por sede requieren validaciones adicionales.

## Flujo reproducible

```bash
# Respaldar Sheets después de la ingesta (requiere OAuth).
uv run python -m ml.snapshot

# Comparar ambos modelos con el mismo test y seleccionar parámetros en meses anteriores.
uv run python -m ml.train --model both --test-start 2025-11-01 --test-end 2025-12-01

# Revisar candidatos; se registran sin promoción automática.
uv run python -m ml.promote --list

# Probar un candidato específico sin escribir en Sheets.
uv run python -m ml.predict --cuenta 04525220 --year 2025 --month 11 \
  --model-path models/candidates/ID_CANDIDATO/model.joblib

# Solo un candidato que supere las referencias por MAE puede ser promovido.
uv run python -m ml.promote --candidate ID_CANDIDATO --metric mae

# Requiere un champion validado; reporta drift y antigüedad de los datos por cuenta.
uv run python -m ml.monitor
```

`--data RUTA.csv` permite entrenar o predecir con un snapshot explícito. Se preservan los identificadores como texto, incluidos ceros iniciales. El entrenamiento verifica el MD5 del snapshot registrado y calcula SHA-256 también para un CSV explícito.

Si no se indican cortes, el test contiene los últimos dos meses de calendario con cobertura suficiente: su mes final debe alcanzar al menos el 50% del máximo mensual de filas elegibles. Es una heurística, no una garantía de cobertura; para experimentos publicados hay que fijar los cortes explícitamente. Los meses posteriores quedan fuera del test principal y se exportan como diagnóstico.

## Preparación y calidad

- Unidad: cuenta/mes del fin del periodo facturado. Se mantienen las etiquetas de sede como trazabilidad, sin adivinar equivalencias.
- Facturas repetidas: cuenta + número de factura. Coincidencias se conservan una sola vez; discrepancias de consumo o fechas del mismo periodo se excluyen para revisión.
- Si una factura reaparece en un periodo posterior contradictorio, se conserva el periodo calendario más temprano y se excluyen los posteriores. Un documento futuro no elimina features de periodos anteriores. Sin fecha de ingesta por fila, la evaluación sigue siendo retrospectiva sobre el snapshot conciliado.
- Facturas distintas del mismo periodo de una cuenta: se excluyen todas hasta conciliarlas; no se suman ni se elige una arbitrariamente.
- Fechas y números inválidos, consumos negativos o no finitos se excluyen con motivo. Ceros reales se conservan.
- `Ajuste_Energia` no se concatena con la base: sus pendientes no son etiquetas válidas y sus registros procesados ya están en BD.
- El importe no es predictor del consumo futuro. Los errores de importe detectados quedan en la auditoría para revisión y no se corrigen en Sheets desde ML.
- `ml/quality_rules.json` identifica el consumo de CAMPUS junio 2025 que ENEL marca en revisión. Su inclusión/exclusión se compara solo en validación anterior al test. Las reglas usadas se embeben en el modelo y se guardan con hash.

## Features y temporalidad

El objetivo de cada fila es el consumo **del mismo mes solicitado t**. Las features incluyen Cuenta, consumos de t−1 y t−2, media y cantidad de observaciones en los tres meses anteriores, indicadores de faltantes y antigüedad de la última observación. Se calcula todo después de reindexar un calendario mensual por cuenta.

`strict` se abstiene si no hay consumo del mes anterior. `causal` permite usar el último consumo conocido hasta tres meses antes, marcando la ausencia y su antigüedad. Un lag_2 faltante se completa con lag_1 disponible; la media usa solo consumos observados de su ventana. No se imputan objetivos ni se interpola desde meses futuros.

Se compara la codificación seno/coseno del mes con una variante sin estacionalidad. No se usa lag_12 ni se afirma que exista estacionalidad anual validada: ninguna cuenta tiene dos años observados.

Entrenamiento e inferencia comparten `calendar_features`. La predicción filtra fechas posteriores o iguales al objetivo **antes** de limpiar la historia. Rechaza cuentas no entrenadas, modelos anteriores a v1 e histórico demasiado antiguo. La salida negativa se recorta a cero tanto en test como en inferencia.

## Validación, parámetros y comparación

- Validación expansiva: cuatro meses anteriores al test, julio a octubre en el experimento inicial. Cada fold se ajusta solo con etiquetas anteriores a su mes objetivo.
- Test común: filas con mes anterior observado y cuenta vista en entrenamiento, independientemente de la política de huecos seleccionada. Así la comparación usa las mismas cuentas, meses y objetivos.
- Los parámetros no se seleccionan con el test. Se usa MAE agregado de la validación previa; los empates favorecen la configuración más simple según un orden determinista.
- `LinearRegression` se compara con `RandomForestRegressor`: 300 árboles, semilla 42, profundidad 4/8 y mínimo 3/5 observaciones por hoja. También se prueban huecos strict/causal, estacionalidad y la inclusión de consumo cuestionado.
- Referencias: persistencia del mes anterior y media móvil. Métricas: MAE, RMSE, R², WAPE, MAPE solo sobre positivos con denominador, métricas por cuenta y cobertura.
- La cobertura y errores de la política causal se informan **por separado** de las métricas del test común, incluyendo abstenciones y predicciones adicionales.
- Noviembre/diciembre son pronósticos sucesivos de **un mes**. Diciembre puede usar noviembre observado. No equivale a dos meses predichos desde octubre.

## Artefactos

Cada candidato guarda en `models/candidates/ID/`:

| Archivo | Contenido |
|---|---|
| `model.joblib` | Pipeline con contrato de inferencia y reglas de calidad embebidas |
| `metadata.json` | Parámetros seleccionados, métricas, hashes de datos/código/reglas, cuentas y cortes |
| `train.csv`, `test.csv` | Datasets finales con features y fecha objetivo |
| `accepted_history.csv`, `excluded_rows.csv` | Decisiones de calidad trazables a la fila del snapshot |
| `quality_report.json` | Exclusiones, cobertura común y operacional, abstenciones y errores adicionales |
| `validation.json` | Todas las configuraciones y resultados por fold |
| `test_predictions.csv` | Predicción y referencias por fila del test común |
| `additional_gap_predictions.csv` | Filas adicionales cubiertas por la política causal |
| `sparse_tail_predictions.csv` | Diagnóstico fuera de los meses principales del test |

MLflow registra experimentos en `mlruns/v1_tracking.db`. Para visualizarlos:

```bash
uv run mlflow ui --backend-store-uri sqlite:///mlruns/v1_tracking.db
```

La comparación de modelos queda en `reports/comparison_FECHA.json`. `models/registry.json` mantiene candidatos y champion. No se promueve por ser el primer modelo: debe mejorar MAE frente a ambas referencias. Para reemplazar un champion se exige además la misma población de evaluación, y mejora estricta en la métrica elegida. Un test diferente requiere reevaluación común.

La v1 entrega predicciones localmente. `Predicciones` tiene esquema de total por sede y no admite un pronóstico por cuenta sin cambiar su contrato y conciliar el mapa de sedes. `--sede` solo se acepta si identifica una cuenta única; si hay varias, se debe indicar `--cuenta`. El monitoreo trabaja por cuenta y señala datos con más de dos meses de atraso.

Consultar [auditoría](AUDITORIA_MODELO_V1.md) y [resultados reales](RESULTADOS_MODELO_V1.md). El histórico termina principalmente en diciembre de 2025: estos resultados no validan predicciones para octubre de 2026 sin actualizar datos.
