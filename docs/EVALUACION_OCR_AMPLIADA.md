# Evaluación ampliada de extracción de facturas

Fecha: 5 de octubre de 2026. Fuente histórica: Google Sheets leído ese día, contrastado con los PDF originales descargados de Drive. Evaluación terminada: **149 documentos y 208 páginas**.

## Resultado ejecutivo

En una muestra aleatoria de 100 facturas procesadas, **795 de 800 campos coinciden con el documento: precisión, recall y F1 micro de 99,38%**. **95 de las 100 facturas tienen sus ocho campos correctos**. Las cinco discrepancias corresponden a cuatro importes y una fecha de inicio.

Los 49 registros pendientes presentan una situación distinta: **precisión de 99,31%, recall de 72,96% y F1 de 84,12%**. De 392 campos esperados, hay 286 correctos, dos incorrectos y 104 ausentes. No tienen ninguna factura completa con los ocho campos correctos en Sheets. La alta precisión refleja los valores que sí se emitieron; no demuestra extracción completa.

La lectura visual permitió reconstruir los ocho campos de los 49 pendientes. Representan **49 facturas y 49 meses por cuenta que no aparecen en BD_energia**; 16 corresponden a huecos internos previamente detectados. El archivo local de recuperación contiene referencias contrastadas, no registros ya incorporados al entrenamiento.

## Métricas

| Indicador | Procesadas: muestra aleatoria de 100 | Pendientes: censo de 49 |
|---|---:|---:|
| Campos esperados | 800 | 392 |
| Campos correctos — TP | 795 | 286 |
| Valores emitidos incorrectos — FP | 5 | 2 |
| Valores correctos no recuperados — FN | 5 | 106 |
| Campos ausentes | 0 | 104 |
| Precisión micro | 99,38% | 99,31% |
| Recall micro | 99,38% | 72,96% |
| F1 micro | 99,38% | 84,12% |
| Exactitud por campo | 99,38% | 72,96% |
| Facturas con ocho campos correctos | 95/100 — 95,00% | 0/49 — 0,00% |

| Campo | Procesadas: precisión / recall / F1 | Pendientes: precisión / recall / F1 |
|---|---:|---:|
| Cuenta | 100 / 100 / 100% | 100 / 100 / 100% |
| Número de factura | 100 / 100 / 100% | 97,50 / 79,59 / 87,64% |
| Consumo kWh | 100 / 100 / 100% | 100 / 2,04 / 4,00% |
| Total a pagar | 96 / 96 / 96% | 92,86 / 26,53 / 41,27% |
| Inicio del periodo | 99 / 99 / 99% | 100 / 93,88 / 96,84% |
| Fin del periodo | 100 / 100 / 100% | 100 / 93,88 / 96,84% |
| Año del fin del periodo | 100 / 100 / 100% | 100 / 93,88 / 96,84% |
| Mes del fin del periodo | 100 / 100 / 100% | 100 / 93,88 / 96,84% |

Año y mes se derivan de la fecha final y no son dos lecturas independientes. Si se evalúan solamente los seis campos directos, las procesadas tienen precisión/recall/F1 de **99,17%**; los pendientes tienen **98,98% / 65,99% / 79,18%**. Para informar calidad documental, conviene acompañar el F1 de ocho campos con el 95% de facturas completamente correctas y la cobertura de consumo.

## Discrepancias confirmadas en la muestra procesada

| Fila BD_energia | Campo | Guardado | Referencia del PDF |
|---|---|---:|---:|
| 107 | ValorFacturado | 814170 | 14170 |
| 316 | ValorFacturado | 8138,14 | 138140 |
| 412 | ValorFacturado | 8124,12 | 124120 |
| 515 | ValorFacturado | 8127,12 | 127120 |
| 402 | Inicio periodo | 2024-12-20 | 2024-12-29 |

La suma de errores absolutos de importe en las 100 facturas es **COP 1.164.990,62**. Es impacto dentro de la muestra, no una proyección monetaria a toda la base. Las confusiones entre símbolo monetario, dígitos y separadores son una explicación plausible de los importes, sin trazas históricas suficientes para atribuirlos exclusivamente al motor OCR.

Las propuestas anteriores de la revisión dirigida siguen siendo evidencia adicional. No se suman sus 15 documentos a la muestra aleatoria para estimar la exactitud de la población.

## Protocolo y límites

- Universo procesado: 613 filas, deduplicadas por Cuenta + N° Factura a 567 facturas para el muestreo. Se seleccionaron 100 sin reemplazo con semilla 20261005, conservando el primer registro de cada grupo. El resultado corresponde a estas facturas representadas por su primera fila; no verifica todas las copias duplicadas.
- Se revisaron todos los 49 registros con Estado Pendiente del mismo snapshot. Los 12 registros PROCESADA de Ajuste_Energia se excluyeron por aparecer ya en BD_energia.
- Tres asistentes transcribieron visualmente 90 PDF procesados sin consultar sus valores de Sheets. El coordinador leyó los otros diez y ocho pendientes; los asistentes revisaron los otros 41 pendientes. El coordinador comprobó además diez documentos procesados y las cinco discrepancias. Los dos primeros pendientes fueron revisados después de ver sus valores guardados; el resto de sus referencias se obtuvo antes de puntuar.
- Segunda lectura dirigida: 80 comparaciones en diez documentos, con 80 coincidencias entre revisores. Es un control de consistencia realizado por asistentes, **no certificación humana independiente**.
- Se inspeccionaron todas las páginas de los pendientes. Todos contienen una sola factura de energía; los PDF de cuatro/seis páginas agregan cartas o anexos, no facturas diferentes.
- Referencia: Cuenta y factura con dígitos verificadores; consumo activo facturado del periodo; TOTAL A PAGAR, incluyendo aseo y otros cargos. Saldo crédito/a favor se interpreta como importe exigible cero; las notas conservan los valores negativos impresos. Oficina viene del contexto de carga y se excluye del puntaje OCR. Las fechas evaluadas son las del periodo eléctrico, no del aseo ni del pago.
- Normalización permitida: quitar guion de Cuenta, quitar espacios del número de factura, convertir separadores numéricos de Sheets y representar fechas ISO. No se corrigen dígitos de predicciones para mejorar el puntaje. Cero es un valor válido; vacío se cuenta como ausente.
- Coincidencia exacta por campo: un valor incorrecto suma un FP y un FN; un campo vacío suma un FN; un valor correcto suma un TP. P = TP/(TP+FP), R = TP/(TP+FN), F1 = 2TP/(2TP+FP+FN). No hay una clase negativa exhaustiva útil para especificidad o TN en esta evaluación de valores.
- Incertidumbre del resultado procesado: intervalo percentil bootstrap del F1 al 95%, remuestreando **facturas**, no campos correlacionados: **98,75–99,88%**, con 20.000 remuestreos y semilla fija. Intervalo Wilson del porcentaje de facturas correctas: **88,82–97,85%**. No se aplicó corrección por población finita. Para cada campo con 100/100 correctos, el límite inferior Wilson es aproximadamente 96,30%; observar 100% no garantiza ausencia de errores en las otras facturas.
- No se combinan las 100 procesadas y los 49 pendientes en una tasa poblacional: sus fracciones de muestreo son distintas y sus unidades también incluyen una regla distinta sobre duplicación.
- Se evalúa **la salida histórica conservada en Sheets**, que puede incluir OCR, extracción por reglas/otros modelos y correcciones posteriores. No se ejecutó EasyOCR de nuevo ni se midió su rendimiento aislado. No hay transcripción completa para calcular CER/WER de páginas enteras.
- El resultado anterior de 95% F1 provenía de una muestra dirigida de 15 PDF con riesgo conocido. El nuevo 99,38% es una estimación sobre muestra aleatoria de procesadas, no evidencia de que el OCR haya mejorado entre mediciones.

## Implicación para el modelo predictivo

El consumo de las 100 procesadas coincide en 100/100 documentos, lo que respalda el objetivo de entrenamiento dentro de esa muestra. En cambio, **48 de los 49 pendientes carecen de consumo en Sheets**, aunque su consumo se pudo leer en el documento. Recuperar estos registros podría ampliar el historial y reducir algunos huecos; su efecto sobre MAE/RMSE debe evaluarse con reentrenamiento y validación temporal. Algunas facturas usan lectura promediada o consumos en revisión: copiar correctamente el PDF no convierte esos valores en mediciones reales.

Los importes incorrectos deben corregirse para análisis financiero o futuros modelos de valor facturado. En la versión predictiva de kWh, estos importes no son una característica del modelo; su corrección no implica por sí sola una mejora de precisión.

## Evidencia reproducible

- [Fuente Sheets](https://docs.google.com/spreadsheets/d/1f80WzlO96Bx5eQ-2BVUVpmDJfTb1druMnsOR-dmlcsQ/edit), snapshot `data/audit/sheets_read_20261005.json`, SHA256 `30f9c34fde25a95c344b8534cfbf58ace519e096e511d6d75e871cd1595fdb5a`.
- `data/audit/extended_ocr/manifest.json`: selección, filas y enlaces originales; cada documento tiene PDF, renderizados, texto nativo y recibo SHA256.
- `reference_labels.json`, `second_review.json`: referencias visuales y control de segunda lectura.
- `metrics.json`, `field_scores.csv`, `discrepancies.csv`: métricas y detalle campo por campo.
- `accepted_correction_proposals.csv`: cinco cambios propuestos sobre procesadas.
- `pending_reference_recovery.csv`: reconstrucción de 49 pendientes; Oficina se conserva de la fuente, sin certificarla mediante el PDF.
- Recalcular desde referencias locales: `.venv/bin/python data/audit/extended_ocr/secondary_checks.py`.

La revisión no modificó Google Sheets, no movió archivos de Drive y no cambió los modelos entrenados. Los CSV contienen propuestas y referencias locales, no cambios ya aplicados a la fuente.
