# Auditoría y propuesta para la primera versión predictiva

Fecha: 2026-10-05. Estado: auditoría realizada; diseño aprobado e implementado. Consultar [resultados reales](RESULTADOS_MODELO_V1.md) para los candidatos entrenados y la validación final. Las secciones de diseño conservan la propuesta original.

## Resultado principal

Los datos permiten preparar un prototipo de consumo eléctrico a un mes, pero el flujo ML vigente pierde cuentas, confunde observaciones con meses y desalineó entrenamiento e inferencia. Antes de medir precisión hay que corregir esa preparación. No hay dos años por cuenta para validar estacionalidad anual. El campo de costo tiene errores confirmados y requiere una revisión adicional antes de modelarse.

Se revisaron los documentos de `docs`, el código de ingesta y ML, las hojas reales de Google Sheets y una muestra dirigida de 15 PDF de Drive. No se escribió en Sheets, no se movieron documentos ni se promocionó un modelo. Los resultados son locales.

## Fuentes y reproducibilidad

- [Google Sheets del proyecto](https://docs.google.com/spreadsheets/d/1f80WzlO96Bx5eQ-2BVUVpmDJfTb1druMnsOR-dmlcsQ/edit): pestañas `BD_energia` y `Ajuste_Energia`, leídas con `values.batchGet`.
- Snapshot original: [sheets_read_20261005.json](../data/audit/sheets_read_20261005.json). SHA-256: `30f9c34fde25a95c344b8534cfbf58ace519e096e511d6d75e871cd1595fdb5a`.
- Recibo de fuente, fechas de extracción y ejecución: [source_receipt.json](../data/audit/source_receipt.json).
- Cálculos y resultados: [auditoria_modelo_v1.ipynb](../data/audit/auditoria_modelo_v1.ipynb), ejecutado secuencialmente con Python 3.12, sin kernel Jupyter; conserva outputs y figura. Se validó su estructura básica y se inspeccionó la figura PNG. No se verificó su renderizado en una interfaz Jupyter.
- Revisión visual de los PDF y hashes: [pdf_visual_review.json](../data/audit/pdf_visual_review.json). Los PDF están conservados en `data/audit/pdf_sample/`.
- Parámetros propuestos, sin aplicar: [proposed_training_config.json](../data/audit/proposed_training_config.json).

El PDF del segundo corte describe un alcance futuro de energía, agua y costo; el código y las hojas inspeccionadas implementan energía. La presentación y los Markdown reflejan una versión Python posterior. Esta primera propuesta se centra en consumo eléctrico, que ya tiene un flujo ML implementado.

## Perfil de la base

| Comprobación | Resultado | Implicación |
|---|---:|---|
| Filas de BD_energia | 613 | Todas tienen los campos obligatorios de la factura poblados |
| Cuentas / etiquetas de sede | 60 / 30 | Una sede puede tener varias cuentas |
| Facturas únicas por cuenta + número | 567 | 46 filas adicionales repiten facturas, en 44 grupos |
| Cuenta/mes únicos | 565 | Hay dos grupos con números de factura distintos para el mismo periodo |
| Cuentas asociadas a varias sedes | 16 | No asignar automáticamente una sede canónica |
| Huecos internos de cuenta/mes | 58 en 27 cuentas | Calcular rezagos sobre calendario, no sobre filas |
| Consumos registrados en cero | 40 filas | Conservar ceros; no convertirlos en faltantes |
| Valores facturados en cero | 91 filas | Un saldo a favor puede dar total cero y consumo positivo |
| Fechas inválidas, invertidas o incoherentes con año/mes | 0 | Coherencia interna correcta, no equivale a verificar todos los PDF |
| Cuentas con al menos 24 meses observados | 0 | No se ha validado un patrón anual repetido |
| Ajustes pendientes / historial PROCESADA | 49 / 12 | Los 12 IDs procesados ya están en BD; no concatenar ambas hojas |

Los 46 duplicados coinciden en consumo, importe y periodo; algunos discrepan en sede. La llave `ID` identifica el archivo de Drive, por eso no detecta copias de una misma factura. [Evidencia de duplicados](../data/audit/repeated_invoices.csv).

Después de quitar solo la repetición cuenta/número, la cuenta `04667290` todavía tiene dos números de factura para marzo y dos para abril de 2025, con el mismo consumo y periodo. Se requiere identificar si son documentos sustituidos o copias antes de sumarlos o eliminarlos en un dataset final. Para el perfil de cobertura se cuentan solo una vez, sin aprobar una transformación de entrenamiento. [Evidencia](../data/audit/different_invoices_same_period.csv).

La cuenta `07527596`, por ejemplo, aparece en CAMPUS y Reserva Danubio-Frailejonal con la misma factura de diciembre. `04676538` aparece en CUENTAS CONTRATO SIN DIRECCION y PLANTA PILOTO. La sede procede de carpetas/metadatos; las facturas exprés no muestran dirección suficiente para verificarla. [Mapa de conflictos](../data/audit/ambiguous_account_offices.csv).

## Contraste con las facturas

Se descargaron y revisaron visualmente 15 documentos: casos normales, Biblioteca, consumos grandes, ceros, duplicación entre sedes y los cinco importes con coma. La muestra se escogió por riesgo, no al azar. No se puede interpretar su proporción de fallos como tasa de error de toda la base. Los consumos, cuentas, números de factura y periodos de esta muestra coinciden con los PDF. Se confirmaron estos errores de importe:

| Fila de BD_energia | Sede registrada | Importe guardado (COP) | TOTAL A PAGAR del PDF (COP) |
|---|---|---:|---:|
| 90 | BODEGAS | 8.269,53 | 269.530 |
| 316 | EDIFICIO G | 8.138,14 | 138.140 |
| 396 | I | 876.060 | 76.060 |
| 412 | I | 8.124,12 | 124.120 |
| 474 | KR 19 NO 48 41 | 5.157,93 | 157.930 |
| 515 | L | 8.127,12 | 127.120 |

Se guardó una [propuesta de corrección](../data/audit/proposed_amount_corrections.csv); no fue aplicada a Sheets. Sustituir coma por punto solo resuelve el formato y no estos errores de extracción. La causa exacta por ejecución no se puede reconstruir sin el texto OCR y versión originales de cada carga.

Los `59.410 kWh` de CAMPUS en junio de 2025 sí aparecen en el PDF. ENEL lo marca como `Tipo de lectura: Promedio` y `Consumo en Revisión`: requiere revisión de negocio y análisis de sensibilidad, no corrección automática por ser grande. Biblioteca registra `586 kWh` y total cero con saldo a favor; ese cero también se confirmó en el soporte.

El parser ejecutado solo sobre texto nativo dejó muchos campos sin extraer porque sus etiquetas y valores están en imágenes. Se completó el contraste mirando las páginas renderizadas. No se reejecutó el OCR completo ni se certificaron los 613 documentos.

## Cobertura temporal

![Cobertura mensual de cuentas con factura registrada](../data/audit/monthly_coverage.png)

Hay un registro cuenta/mes en diciembre de 2024, 562 durante 2025 y dos en enero de 2026. La mayoría del histórico termina en diciembre de 2025. No hay datos recientes suficientes para afirmar precisión en octubre de 2026. Los meses anteriores a la primera factura o posteriores a la última no se cuentan como huecos internos: falta el catálogo de cuentas activas por periodo para afirmar que debían estar presentes.

CAMPUS falta en abril y agosto a noviembre de 2025 según su etiqueta de sede; parte del histórico también está fragmentado entre etiquetas. Biblioteca falta en octubre. Para seguimiento por cuenta se conservan [cobertura](../data/audit/account_coverage.csv) y [huecos internos](../data/audit/missing_account_months.csv). Ninguna cuenta supera 12 meses observados.

## Defectos del flujo ML actual

1. `ml/dataset.py` conserva la última fila por sede/año/mes. Descarta 351 de las 613 filas antes de formar features; entre ellas hay cuentas distintas que representan consumo real, además de duplicados. No produce el total de la sede.
2. Los `shift` trabajan por sede y posición de fila. Si falta abril, marzo puede quedar como anterior a mayo. El target también pasa a ser la siguiente observación disponible, aunque transcurran varios meses.
3. Entrena con el calendario de t y target de la siguiente observación. La inferencia recibe el calendario del mes pedido y rezagos hasta la última factura. Hay desalineación del calendario y de la información disponible; además, el consumo del mes t no se usa como lag inmediato del target siguiente.
4. El split se hace por fecha de las features, no por fecha del objetivo. Puede asignar al entrenamiento una etiqueta de un mes que debería pertenecer a prueba.
5. `compute_lags` toma las últimas filas sin limitarse a fechas anteriores al mes solicitado ni deduplicar/armonizar cuentas. Puede usar datos posteriores en una predicción histórica.
6. El modelo RF tiene 300 árboles sin limitar profundidad ni tamaño mínimo de hoja, y no hay selección con validación temporal ni comparación con una referencia simple.
7. La promoción inicial se hace por ausencia de champion; no exige demostrar mejora frente a una referencia. MAPE omite ceros, que son relevantes en esta base.

Ejecutando las funciones vigentes, el resultado fue 179 filas de features, 161 de entrenamiento y 18 de test. Esas métricas evaluarían la representación incorrecta descrita, no todos los consumos por cuenta. No se entrenaron modelos para presentar una precisión engañosa.

## Diseño acotado propuesto para implementar

**Objetivo y unidad:** pronosticar consumo eléctrico a un mes por cuenta. Conservar la sede como trazabilidad; la agregación por sede requiere un mapa aprobado de cuenta/sede y cobertura de todas sus cuentas. Una suma parcial se identifica como parcial. El campo de importe no se usará como predictor de un consumo futuro, pues el importe del mes futuro no está disponible al predecirlo.

**Limpieza:** conservar el snapshot original y emitir filas aceptadas, excluidas y motivos. Deduplicar facturas con campos coincidentes por cuenta/número; conflictos de medidas se excluyen para revisión. Los dos grupos de números distintos en un mismo periodo quedan pendientes de conciliación, sin sumarse automáticamente. `Ajuste_Energia` pendiente no se usa como fuente de objetivos. Los importes defectuosos no requieren perder consumos verificados para un modelo exclusivamente de consumo.

**Calendario y features:** target = consumo observado del mes t. Crear calendario mensual por cuenta antes de calcular `lag_1`, `lag_2`, media de los tres meses anteriores, cantidad de observaciones disponibles en esa ventana e indicadores de faltantes. Todas las features usan fechas estrictamente anteriores a t. Representar cuenta con one-hot y ensayar mes seno/coseno frente a la misma configuración sin estacionalidad. No activar `lag_12`: solo tres objetivos de todo el histórico tienen ese rezago disponible.

**Huecos:** nunca imputar el objetivo ni usar interpolación con meses posteriores. Las features faltantes se completan con estadísticas históricas de la cuenta disponibles al origen de predicción; si hace falta un respaldo global, se ajusta solo dentro del entrenamiento de cada fold y se registra el indicador de imputación. La opción conservadora es abstenerse cuando falta el mes inmediatamente anterior; una variante con imputación se compara por separado y reporta cobertura. Los targets cuestionados, como CAMPUS junio, se evalúan con una variante excluida y otra incluida sin borrar el dato crudo. Esta decisión se toma con validación previa al test.

**Entrenamiento/prueba:** dos datasets derivados del mismo histórico, separados por fecha objetivo; no hace falta conseguir una segunda fuente. Como propuesta de partida, etiquetas hasta octubre 2025 para entrenamiento y noviembre/diciembre para test. Hay 475 y 88 cuenta/mes observados respectivamente antes de exclusiones y requisitos de rezagos; no son los conteos finales de entrenamiento. Enero 2026 queda como diagnóstico adicional de dos cuentas. Validar parámetros con ventanas expansivas que pronostiquen julio, agosto, septiembre y octubre, usando únicamente fechas anteriores a cada mes. Los splits se construyen por meses globales, no por posiciones de filas de un panel con cuentas desiguales. La documentación de [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) explica por qué hay que respetar el orden temporal y la separación comparable de las muestras.

El test es de **predicciones sucesivas de un mes**: noviembre usa historia hasta octubre y diciembre puede usar noviembre observado. No se presentará como pronóstico de dos meses emitido desde octubre. Para ese horizonte hace falta una evaluación adicional sin acceder al noviembre real.

**Modelos:** `LinearRegression` y `RandomForestRegressor`, comparados con persistencia del mes anterior y media histórica reciente. Para RF: 300 árboles, semilla 42 y búsqueda pequeña de `max_depth ∈ {4, 8}`, `min_samples_leaf ∈ {3, 5}`. Son valores iniciales por validar, no parámetros óptimos. Estas restricciones limitan el tamaño de los árboles; ver [documentación de RandomForestRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html). La codificación, escalado e imputación se ajustan dentro del fold; [Pipeline y prevención de fuga](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

**Evaluación y registro:** MAE, RMSE y WAPE globales y por cuenta, cobertura de predicciones y error promedio por cuenta para que las cuentas grandes no oculten otras. MAPE se informa solo para consumos positivos con su denominador; WAPE es nulo si toda la suma real es cero. Comparar candidatos en exactamente las mismas filas y periodos, con hash del dataset y predicciones por fila. No promocionar automáticamente por ser el primero ni elegir hiperparámetros mirando el test.

**Archivos y validación:** ajustar `ml/dataset.py`, `ml/train.py`, `ml/predict.py` y los metadatos/criterios de registro afectados. Exportar datasets de entrenamiento y prueba con sus decisiones de calidad y mantener las features compartidas entre entrenamiento e inferencia. Probar meses faltantes, ceros, varias cuentas por sede, duplicados, cruce de año y rechazo de historia futura. Entrenar ambos candidatos en el mismo snapshot y entregar comparación y artefactos locales; actualizar `docs/MLOPS.md`.

## Estado y decisiones pendientes

La implementación fue autorizada y completada. La conciliación de cuenta/sede y las facturas distintas del mismo periodo siguen pendientes; el entrenamiento las excluye explícitamente. Ambos modelos quedaron como candidatos porque ninguno mejora MAE frente a persistencia. Consultar [resultados y artefactos](RESULTADOS_MODELO_V1.md). No se validaron costos, estacionalidad anual ni precisión para una fecha actual.
