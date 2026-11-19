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

`scripts/experiment_models.py` compara enfoques (nivel, delta, razón) y familias (lineal, Ridge, Huber, Random Forest con MSE y MAE, Extra Trees, Gradient Boosting, HistGradientBoosting) con la misma validación expansiva y bootstrap pareado frente a la persistencia. Escribe `reports/experiment_models_FECHA.{json,csv}` y no modifica registro ni hojas. Sirve para justificar qué familia conviene reentrenar cuando entren datos nuevos, no para promover automáticamente.

La v1 entrega predicciones localmente. `Predicciones` tiene esquema de total por sede y no admite un pronóstico por cuenta sin cambiar su contrato y conciliar el mapa de sedes. `--sede` solo se acepta si identifica una cuenta única; si hay varias, se debe indicar `--cuenta`. El monitoreo trabaja por cuenta y señala datos con más de dos meses de atraso.

### Publicación por cuenta y reporte visual

`ml.publish` escribe en la pestaña `Predicciones_Cuenta` (columna aparte de `Predicciones`), con una fila por cuenta: fecha de ejecución, sede, cuenta, año, mes, consumo predicho, modelo, antigüedad del último dato y política de rezago. Por defecto pronostica el primer mes sin ninguna observación; `--year`/`--month` fijan otro. Solo escribe con `--write`, crea la pestaña si falta y no duplica filas: la identidad es cuenta + año + mes + modelo, así que dos modelos pueden pronosticar el mismo mes y compararse. Las cuentas sin historial suficiente se abstienen y se cuentan en el resumen (`publicadas`, `observadas`, `abstenciones`, `sin_entrenamiento`).

`ml.report` genera `reports/report_FECHA/index.html` y cuatro PNG: MAE de los candidatos frente a la persistencia, distribución de errores (con los 12 mayores), cobertura de prueba por mes y series por cuenta con histórico, predicciones de prueba y pronóstico futuro. No reentrena ni escribe en Sheets.

```bash
uv run python -m ml.publish --model-path models/candidates/ID/model.joblib          # solo pantalla
uv run python -m ml.publish --model-path models/candidates/ID/model.joblib --write  # hoja
uv run python -m ml.report --candidate models/candidates/ID
```

Consultar [auditoría](AUDITORIA_MODELO_V1.md) y [resultados reales](RESULTADOS_MODELO_V1.md). El histórico termina principalmente en diciembre de 2025: estos resultados no validan predicciones para octubre de 2026 sin actualizar datos.

### Extensión de modelos y visualizaciones en la nube (noviembre de 2026)

`ml.neural_models` incorpora una red RBF (centros K-Means, base gaussiana y salida
Ridge) y una LSTM de PyTorch que procesa los rezagos t−2 y t−1 en orden. La
competencia experimental `scripts.experiment_models` admite RBF, LSTM, MLP
y SVR, y parámetros `--models`, `--approaches`, `--test-start`, `--test-end`.
La LSTM no implica memoria de más de dos meses: no hay evidencia suficiente para
atribuirle ventajas con series largas. Ningún modelo se promueve solo por su familia.

Resultados iniciales del enfoque level sobre las mismas 69 observaciones de
noviembre-diciembre de 2025 (MAE menor es mejor):

| Modelo | MAE kWh |
|---|---:|
| Persistencia (referencia) | 454,74 |
| RBF, 24 centros | 645,14 |
| RBF, 12 centros | 757,85 |
| LSTM | 818,00 |
| MLP | 1488,43 |
| SVR | 1585,88 |

Datos completos del experimento en `reports/experiment_models_20261118_172739.json`.
La elección por validación previa favoreció LSTM dentro de estas configuraciones,
pero **no** supera persistencia en el test. No promover todavía.

`ml.sheets_dashboard` lee `Predicciones_Cuenta` y el snapshot versionado;
con `--write` actualiza la pestaña `Graficos_Predicciones` y tres gráficos
editables de Google Sheets. Evita duplicación de gráficos usando el título.
La cohorte se limita a un modelo y un mes; el histórico incompleto se expresa
como cobertura, no como total institucional. Usa `uv run python cli.py graficos`
para consultar sin modificar y agrega `--publicar` para escribir.

La aplicación de pendientes registra una fase PREPARADA y verifica la ubicación
vigente del archivo; en fallos posteriores a mover/renombrar, intenta volver a
la carpeta y nombre originales. Esto es **compensación**, no una transacción
atómica entre Drive y Sheets: una llamada con respuesta perdida debe
conciliarse manualmente. Citas del LLM que no se puedan cotejar con el OCR
deben revisarse en el PDF.

## Actualización de resultados: competencia completa (18/11/2026)

Se evaluaron los nuevos candidatos con tres enfoques: `level` (nivel directo), `delta` (corrección sobre persistencia) y `ratio` (cambio proporcional). Fueron **15 configuraciones nuevas** con folds de validación expansiva y **69 observaciones** del test común noviembre–diciembre de 2025. Archivo reproducible: `reports/experiment_models_20261118_173215.json`.

| Enfoque | Modelo | MAE test (kWh) | MAE validación (kWh) |
|---|---|---:|---:|
| Persistencia | Referencia | **454,74** | — |
| Delta | SVR radial | 468,37 | 361,31 |
| Delta | MLP | 512,29 | 364,50 |
| Delta | RBF, 12 centros | 571,99 | 359,94 |
| Delta | LSTM | 573,61 | 456,87 |
| Delta | RBF, 24 centros | 581,75 | 357,65 |
| Level | RBF, 24 centros | 645,14 | 800,85 |
| Level | LSTM | 818,00 | 520,35 |

La selección estricta por validación, para `delta`, favoreció RBF de 24 centros, cuyo MAE de test es 581,75. Aunque el mejor ranking de test corresponde al SVR delta (468,37), este resultado **no justifica seleccionar por test ni superar la referencia**. No se promovió ningún campeón. Se recomienda ampliar histórico y actualizar el corte temporal antes de presentar mejoras.

El lote `data/llm_proposals/pending_revisado.json` contenía 50 propuestas: 38 marcadas como listas por el procesamiento anterior. Con la comprobación estricta de citas, 15 de esas 38 cumplen la validación automática y 23 deben verificarse visualmente con el PDF. La clasificación no autoriza aplicar sin revisión humana; los archivos reales permanecieron intactos.

## Resultados comparativos publicados en Google Sheets

El módulo `ml/comparisons.py` crea y actualiza dos hojas reservadas:
`Comparativa_OCR_LLM` (resumen, seis campos, tiempos y dos gráficos nativos)
y `Comparativa_Modelos` (102 configuraciones, trece familias, métricas de
validación/test, referencias, challengers v1 y dos gráficos nativos).

Las corridas `experiment_models_20261118_134221.json` (87 configuraciones)
y `experiment_models_20261118_173215.json` (15) comparten las mismas 69
observaciones de prueba. El código bloquea la mezcla de poblaciones distintas.
El informe OCR se basa en `ocr_vs_llm_20261118_153814.json` y distingue
los 15 documentos de la submuestra manual de 4. Sin `--publicar` la
actualización es una vista previa, sin escritura ni nueva ejecución de Gemini.

```bash
uv run python cli.py comparativas
uv run python cli.py comparativas --publicar
```

Ver [COMPARATIVAS_SHEETS.md](COMPARATIVAS_SHEETS.md) para fuentes,
métricas, criterios de comparación y limitaciones metodológicas.
