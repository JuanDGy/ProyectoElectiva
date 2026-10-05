# Resultados reales — modelo de consumo v1

Fecha: 2026-10-05. Primera versión implementada, entrenada y verificada sobre el snapshot auditado. Los dos modelos quedan como candidatos experimentales; ninguno fue promovido porque no supera la referencia de persistencia por MAE.

## Evaluación

Unidad: consumo eléctrico por cuenta/mes. Etiquetas de entrenamiento hasta octubre de 2025; test común noviembre y diciembre de 2025. Son predicciones sucesivas de un mes: para diciembre se conoce el noviembre observado. No son predicciones para octubre de 2026 ni un pronóstico de dos meses emitido desde octubre de 2025.

Cada modelo usa 412 filas de entrenamiento y 69 de test común. El test común tiene 65 objetivos positivos y 4 ceros. Las cuentas no vistas en entrenamiento y las filas sin mes anterior observado se excluyen de la comparación principal. El hash de la población de evaluación es `638643d74d506e8ec6d994fde46c04a199aa294db08651dca11ebbfbd85e78e1`.

| Modelo | MAE (kWh) | RMSE (kWh) | WAPE (%) | MAPE positivos (%) |
|---|---:|---:|---:|---:|
| Persistencia del mes anterior | 454.74 | 1490.23 | 24.78 | 85.73 |
| Media móvil histórica | 474.23 | 1534.44 | 25.85 | 92.88 |
| Regresión lineal | 507.29 | 1591.63 | 27.65 | 99.48 |
| Random Forest | 462.45 | 1150.18 | 25.21 | 137.35 |

WAPE es suma de errores absolutos dividida por suma del consumo real; no es una tasa de exactitud ni el error de una cuenta individual. MAPE omite ceros y es muy sensible a consumos pequeños; se muestra con su denominador de 65 positivos. El RF reduce RMSE frente a persistencia, pero su MAE es peor: no cumple la regla de promoción predefinida. No se ajustaron parámetros después de mirar el test.

## Parámetros elegidos antes del test

| Modelo | MAE validación (kWh) | Política de huecos | Estacionalidad | Incluir consumo cuestionado | RF |
|---|---:|---|---|---|---|
| linear | 558.53 | causal | False | False | — |
| random_forest | 445.27 | causal | False | False | 300 árboles, profundidad 8, mínimo 3 por hoja |

Validación expansiva julio/octubre: cuatro folds y 167 filas de evaluación común. Se ensayaron 8 configuraciones lineales y 32 de RF: estacionalidad seno/coseno o ninguna, política strict/causal e inclusión/exclusión de CAMPUS junio; para RF además profundidad 4/8 y mínimo de hoja 3/5. La selección se hizo solo con MAE de esa validación.

Ambos eligieron no usar estacionalidad y excluir el consumo que ENEL marcó en revisión. Es el resultado de esta validación corta; no demuestra ausencia de estacionalidad anual. Ninguna cuenta tiene dos años de datos.

## Calidad y cobertura

El snapshot contiene 613 facturas. Se aceptaron 562 registros para consumo; exclusiones: repeated_invoice: 46, different_invoices_same_period: 4, questionable_consumption: 1.

Las cuatro filas excluidas por facturas distintas en un mismo periodo corresponden a dos grupos de la cuenta 04667290. No se eligió un número ni se sumaron valores sin conciliación. Los seis errores de importe confirmados siguen documentados en la auditoría; costo no forma parte del modelo y Sheets no fue alterado.

Hay 88 objetivos observados en noviembre/diciembre. La comparación común cubre 69 (78.41%). La política causal seleccionada puede generar 80 predicciones (90.91%), con 8 abstenciones. Su cobertura adicional no se mezcla con las métricas comunes al escoger un modelo.

| Modelo | MAE de filas adicionales (kWh) | MAE de todas las predicciones de su política (kWh) |
|---|---:|---:|
| linear | 1849.26 | 691.81 |
| random_forest | 146.05 | 418.94 |

La política causal usa el último consumo observado, hasta tres meses antes, con indicadores de ausencia y antigüedad. No fabrica objetivos ni usa un mes futuro para rellenar el pasado. Hay predicciones y métricas por cuenta en los artefactos.

## Artefactos y reproducción

- Snapshot: [`data/raw/bd_energia_20261005_110906.csv`](../data/raw/bd_energia_20261005_110906.csv). SHA-256: `213a4ba04a291d230d10a0a43c21ed840c6cb533b971e0308178a4ccf2754fe3`.
- Comparación: [`comparison_20261005_111637.json`](../reports/comparison_20261005_111637.json).
- [Auditoría y facturas revisadas](AUDITORIA_MODELO_V1.md).
- [Reglas y comandos MLOps](MLOPS.md).

### linear

ID: `linear_20261005_111447_c3f191`.

- [Modelo entrenado](../models/candidates/linear_20261005_111447_c3f191/model.joblib).
- [Dataset de entrenamiento](../models/candidates/linear_20261005_111447_c3f191/train.csv) y [dataset de prueba](../models/candidates/linear_20261005_111447_c3f191/test.csv).
- [Parámetros, hashes y métricas](../models/candidates/linear_20261005_111447_c3f191/metadata.json).
- [Validaciones por configuración](../models/candidates/linear_20261005_111447_c3f191/validation.json).
- [Decisiones de calidad](../models/candidates/linear_20261005_111447_c3f191/excluded_rows.csv) y [cobertura](../models/candidates/linear_20261005_111447_c3f191/quality_report.json).
- [Predicciones del test común](../models/candidates/linear_20261005_111447_c3f191/test_predictions.csv) y [predicciones adicionales](../models/candidates/linear_20261005_111447_c3f191/additional_gap_predictions.csv).

### random_forest

ID: `random_forest_20261005_111636_d338fa`.

- [Modelo entrenado](../models/candidates/random_forest_20261005_111636_d338fa/model.joblib).
- [Dataset de entrenamiento](../models/candidates/random_forest_20261005_111636_d338fa/train.csv) y [dataset de prueba](../models/candidates/random_forest_20261005_111636_d338fa/test.csv).
- [Parámetros, hashes y métricas](../models/candidates/random_forest_20261005_111636_d338fa/metadata.json).
- [Validaciones por configuración](../models/candidates/random_forest_20261005_111636_d338fa/validation.json).
- [Decisiones de calidad](../models/candidates/random_forest_20261005_111636_d338fa/excluded_rows.csv) y [cobertura](../models/candidates/random_forest_20261005_111636_d338fa/quality_report.json).
- [Predicciones del test común](../models/candidates/random_forest_20261005_111636_d338fa/test_predictions.csv) y [predicciones adicionales](../models/candidates/random_forest_20261005_111636_d338fa/additional_gap_predictions.csv).

## Verificación y límites

- Suite completa: 95 pruebas aprobadas; MLflow emitió una advertencia de deprecación de SQLAlchemy, sin fallos.
- Revisión independiente: corregidos y comprobados el manejo de conflictos futuros de factura y el reporte separado de cobertura causal.
- Se comprobaron hashes de código/datos y se reprodujeron por la ruta de inferencia las predicciones comunes y adicionales de ambos modelos; evidencia en `reports/verification_model_v1.json`.
- Los candidatos se entrenaron hasta octubre de 2025; no se reajustaron después con el test. El registro no tiene champion. Para probarlos, hay que indicar su ruta explícita.
- El mapa de cuenta/sede continúa ambiguo en 16 cuentas. La v1 no publica totales por sede ni escribe predicciones en Sheets.
- El histórico termina principalmente en diciembre de 2025; enero de 2026 solo aporta dos cuentas. Actualizar facturas y conciliar los problemas pendientes es necesario antes de validar un pronóstico reciente.

```bash
uv run python -m ml.train --model both --test-start 2025-11-01 --test-end 2025-12-01
uv run python -m ml.predict --cuenta 04525220 --year 2025 --month 11 --model-path models/candidates/random_forest_20261005_111636_d338fa/model.joblib
```
