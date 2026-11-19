# EcoData ECCI — guía integral del proyecto

Fecha de actualización: 18 de noviembre de 2026. Documento técnico base para el informe final académico. Se distinguen resultados comprobados, implementación y trabajo pendiente.

## 1. Propósito

EcoData ECCI automatiza la lectura, clasificación, extracción y trazabilidad de facturas de energía eléctrica, sincronizando Google Drive y Google Sheets. Con los consumos mensuales por cuenta desarrolla y contrasta pronósticos de un mes, informes y gráficos. El objetivo es reducir captura manual y mejorar la calidad documental sin permitir que una respuesta de IA modifique datos sin control.

## 2. Arquitectura

Entrada: servicios de Google Drive en services/drive.py, con búsqueda recursiva y paginada en subcarpetas, conservando identificadores y carpeta original. Extracción: services/pdf_text.py, PyMuPDF, EasyOCR y reglas específicas en pipeline/invoice_rules.py. Clasificación y registro: pipeline/triage.py, pipeline/energy.py y services/sheets.py. Pendientes: pipeline/pending.py y carpeta 01C; no procesables: 01D; procesados: 02_FACTURAS_ENERGIA, organizados por sede. Aprendizaje: ml/dataset.py, ml/train.py, ml/predict.py, ml/registry.py, ml/publish.py y ml/neural_models.py. Visualización: ml/report.py (HTML y PNG), ml/sheets_dashboard.py (gráficos nativos de Sheets). CLI única: cli.py, sin eliminar comandos originales.

## 3. Guía de operación

Iniciar la CLI con: uv run python cli.py. Mostrar ayuda con: uv run python cli.py --help.

Comandos principales:

    uv run python cli.py estado
    uv run python cli.py procesar --limite 1
    uv run python cli.py historico --limite 10
    uv run python cli.py comparar-ocr --limite 15 --semilla 7
    uv run python cli.py pendientes revisar --limite 10
    uv run python cli.py pendientes aplicar --archivo data/llm_proposals/pending_FECHA.json
    uv run python cli.py competencia --modelos rbf,lstm,mlp,svr --enfoques level
    uv run python cli.py predicciones --modelo models/candidates/ID/model.joblib
    uv run python cli.py predicciones --modelo models/candidates/ID/model.joblib --publicar
    uv run python cli.py graficos
    uv run python cli.py graficos --publicar

Los comandos de predicción funcionan a un mes. Sin un campeón promovido, se debe indicar una ruta de candidato entrenado. Publicar predicciones crea registros en Predicciones_Cuenta; publicar gráficos actualiza la pestaña reservada Graficos_Predicciones. En modo lectura no se altera Sheets.

## 4. Datos y controles

La unidad de modelado es la pareja cuenta/mes de finalización del periodo facturado. La identidad de factura se basa en cuenta + número de factura; se evitan duplicados y se excluyen registros con inconsistencias, fechas imposibles, importes conflictivos o consumos negativos. Los consumos de cero son válidos. Los identificadores con ceros iniciales se conservan como texto.

Los snapshots versionados permiten repetir experimentos. Los predictores utilizan rezagos t−1 y t−2, media móvil, indicadores de faltantes, antigüedad y, cuando proceda, estacionalidad senoidal. No se usa consumo real del mes objetivo. Los folds de validación son expansivos; un conjunto posterior se reserva para test.

## 5. Competencia de modelos y criterios de aceptación

Ya existían referencias de persistencia y media móvil, regresión lineal, Ridge, Huber, Random Forest, Extra Trees y métodos de boosting. La competencia incorpora:

- RBF: red radial de una capa, centros obtenidos por K-Means, activaciones gaussianas y salida Ridge.
- MLP: red neuronal densa de scikit-learn.
- SVR: regresión de vectores soporte con kernel radial.
- LSTM: recurrencia en PyTorch con una secuencia causal de solo dos consumos previos, más covariables estáticas.

Las familias se someten a las mismas ventanas de validación y prueba. El reporte incluye MAE, RMSE, WAPE, cobertura y bootstrap pareado frente a persistencia. Las configuraciones se eligen según validación, no por la mejor puntuación en el test. El ranking del test es diagnóstico.

Los datos actuales incluyen menos de dos años por cuenta. Por eso no se ha demostrado que la LSTM capture patrones de largo plazo, y no se debe promover ningún modelo si no mejora significativamente las referencias. El estado anterior era 0 de 87 configuraciones superiores con evidencia suficiente y ningún campeón. Esta cifra corresponde al experimento antiguo, no incluye los modelos nuevos hasta ejecutar la nueva competencia.

## 6. Predicciones en Google Sheets

La pestaña Predicciones_Cuenta almacena predicción, cuenta, sede, año, mes, modelo, fecha y antigüedad de la observación. La llave cuenta + año + mes + modelo evita duplicaciones.

Graficos_Predicciones tiene tres gráficos NATIVOS que pueden editarse desde Google Sheets:
1. Evolución temporal de una cuenta destacada, con valores reales y pronóstico.
2. Ranking de 15 cuentas con mayor consumo pronosticado.
3. Cobertura de datos: cuentas con observación real por mes frente a cuentas pronosticadas.

Se usa una cohorte de cuentas y un único modelo/mes para cada tablero. Las cifras del histórico con faltantes no deben presentarse como consumo agregado de toda la institución. Los gráficos se actualizan por título para evitar duplicados. La publicación requiere permiso de escritura y una ejecución explícita de --publicar.

## 7. Comparativa OCR y Gemini

La prueba documentada sobre 15 facturas ENEL, semilla 7, comparó seis campos. Frente a la hoja usada como referencia, el OCR alcanzó 100% de facturas completas; Gemini 73,3%; Gemini con regla del TOTAL A PAGAR 93,3%. El OCR produjo la mayor parte de la referencia y la comparación favorece su método. Cuatro discrepancias se revisaron manualmente, pero no son una muestra independiente.

Se detectó que Gemini confundía el total de energía con TOTAL A PAGAR en facturas de cero pesos. La regla determinista y la auditoría de citas deben prevalecer sobre la inferencia del modelo. Los modelos comerciales y su disponibilidad pueden cambiar; no se incorporan credenciales en la documentación.

## 8. Gestión segura de pendientes

El OCR local corre primero; Gemini intenta completar los campos que falten. Los resultados se guardan como propuestas JSON con identificador del archivo, nombre, origen, campos, citas y errores. Solo una segunda orden de aplicación puede mover/renombrar PDF y registrar en Sheets.

Antes de aplicar hay que cotejar con el PDF original, la carpeta actual, el nombre del archivo, el periodo, el total a pagar y la evidencia de cada campo. Drive y Sheets no ofrecen una transacción compartida: cualquier fallo parcial exige bitácora de auditoría y reconciliación operativa. No confundir 140 pruebas unitarias correctas con validación transaccional real.

## 9. Seguridad y pruebas

Mantener .env, token.json y credentials.json fuera del repositorio. Ejecutar:

    uv run python -m pytest -q
    uv run python -m compileall -q cli.py ml services pipeline scripts tests

Reproducir experimentos con semilla, dataset y cortes de evaluación publicados. Comprobar permisos OAuth antes de publicar. Revisar calidad del histórico, duplicados, cuentas ambiguas, coberturas, fugas de información temporal y concordancia entre documentos y datos escritos.

## 10. Estructura del informe académico final

Portada y resumen; introducción y problema; objetivos; alcance; fundamentos conceptuales; levantamiento de requisitos; arquitectura y diagramas; diseño de procesamiento y validación de facturas; metodología experimental y modelos; comparación OCR-LLM; resultados y visualizaciones; pruebas y riesgos; conclusiones; trabajo futuro; referencias y anexos.

Para construir el informe final deben añadirse capturas verificadas de Google Sheets, artefactos JSON/CSV de la competencia ampliada, tablas de métricas, diagramas del flujo completo y procedimientos de restauración. No deben inventarse mejoras de precisión que no hayan ocurrido.

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


## 11. Auditoría documental, aprobación humana y recuperación

El pipeline incorpora huellas SHA-256 por PDF, renovaciones sin consumo de tokens,
auditoría de propuestas contra Drive y Sheets en modo lectura, y aprobación
individual vinculada a la huella. La aplicación real exige JSON aprobado,
revalidación del documento y registro de las etapas PREPARADA, APLICADA,
APLICADA_RECONCILIADA, ERROR_APLICACION e INDETERMINADA.

En la revisión real de 50 facturas realizada el 18 de noviembre de 2026 se
identificaron 15 propuestas verificadas con OCR y SHA-256, y 35 en revisión.
No se aplicaron movimientos. La suite completa registró 170 pruebas correctas,
incluida una integración simulada de extremo a extremo.

El procedimiento operacional y las limitaciones de las compensaciones están
documentados en [OPERACION_Y_RECUPERACION.md](OPERACION_Y_RECUPERACION.md).
La aprobación manual corresponde al responsable del dato; el algoritmo no
puede certificar por sí solo que una factura es exacta.

## 12. Exclusión mutua durante la aplicación

El comando de aplicación utiliza un bloqueo exclusivo local para evitar dos
aplicaciones concurrentes en el mismo directorio. No protege frente a
operadores en máquinas distintas. Ver el procedimiento de recuperación en
[OPERACION_Y_RECUPERACION.md](OPERACION_Y_RECUPERACION.md).

## 13. Pestañas comparativas de resultados experimentales

Se integraron dos nuevas pestañas con tablas editables y gráficos nativos:
`Comparativa_OCR_LLM`, con los resultados de tres estrategias de extracción,
seis campos y referencias manuales, y `Comparativa_Modelos`, con 102
configuraciones de 13 familias, líneas base y dos candidatos registrados.

La elección de configuraciones para los gráficos predictivos se realiza por
validación temporal expansiva, nunca por el mejor test. La comparación
se limita al conjunto común de 69 observaciones; la corrida antigua de 71
observaciones no se fusiona con la actual. Los resultados OCR se califican
como preliminares por depender de una referencia derivada del OCR local.

Las actualizaciones se hacen con `uv run python cli.py comparativas --publicar`.
Consultar [COMPARATIVAS_SHEETS.md](COMPARATIVAS_SHEETS.md).
