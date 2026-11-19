# Comparativas experimentales en Google Sheets — EcoData ECCI

**Actualizado:** 18 de noviembre de 2026  
**Estado:** implementado y publicado mediante la API oficial de Google Sheets.  
**Módulo:** ml/comparisons.py  
**Integración:** services/sheets.py, método replace_comparison.  
**Interfaz:** cli.py, comando comparativas.

## Objetivo

Exponer resultados reproducibles del procesamiento de facturas y de la competencia
de modelos predictivos directamente en Google Sheets. Las visualizaciones son
gráficos nativos editables, no imágenes. El proceso es independiente de la
lectura de Drive, el entrenamiento, Gemini y la ingesta de facturas.

Las únicas pestañas administradas son **Comparativa_OCR_LLM** y
**Comparativa_Modelos**. El módulo no altera BD_energia, Predicciones,
Predicciones_Cuenta ni Graficos_Predicciones.

## Comparativa_OCR_LLM

**Fuente reproducible:** reports/ocr_vs_llm_20261118_153814.json  
**Experimento:** 15 facturas, semilla 7; cuatro referencias verificadas a mano.

| Método | Filas completas | Porcentaje |
|---|---:|---:|
| OCR local | 15 de 15 | 100,00 % |
| Gemini solo | 11 de 15 | 73,33 % |
| Gemini más reglas | 14 de 15 | 93,33 % |

El tiempo medio medido en este reporte fue aproximadamente 8,08 s para OCR
y 3,67 s para Gemini. Estos tiempos corresponden a la muestra específica,
no a una garantía de desempeño en producción.

Se presentan dos gráficos nativos: porcentaje de facturas completas y
comparación de la concordancia por cada uno de seis campos (cuenta, factura,
kWh, importe total, inicio y fin del periodo). La tabla muestra aciertos,
errores/faltantes y la submuestra de cuatro documentos cotejados manualmente.

**Limitación fundamental:** los datos de referencia proceden principalmente
del OCR que originó la hoja BD_energia, por lo que sus resultados están sesgados
a favor del método local. El 100 % NO significa precisión externa absoluta.
La submuestra manual de cuatro facturas tampoco es representativa por sí sola.

## Comparativa_Modelos

**Fuentes reproducibles:**

- reports/experiment_models_20261118_134221.json:
  87 configuraciones clásicas y líneas base.
- reports/experiment_models_20261118_173215.json:
  15 configuraciones adicionales, incluidas LSTM, RBF, MLP y SVR.
- reports/comparison_20261118_130631.json:
  dos candidatos entrenados y registrados por el flujo v1.

Las dos corridas experimentales principales comparten un conjunto de prueba
de 69 observaciones y los mismos cuatro meses de validación temporal:
julio, agosto, septiembre y octubre de 2025. El programa comprueba origen,
periodos, cantidad de filas y métrica de persistencia antes de combinar
sus resultados. Una corrida anterior de 71 filas permanece excluida para
evitar comparaciones con distinta cohorte.

Se visualizan 102 configuraciones experimentales de 13 familias, más
las dos referencias (persistencia y media móvil), y los dos candidatos
registrados en una sección adicional.

Para los gráficos principales se selecciona **una sola configuración de cada
familia según el mínimo MAE de validación**, sin consultar el resultado de test
para elegirla. Los dos gráficos nativos exponen MAE y WAPE en el test común,
y la tabla completa conserva hiperparámetros, enfoque level/delta/ratio,
estacionalidad, MAE de validación, MAE/RMSE/WAPE/R² de test, tamaño de prueba,
probabilidad pareada de mejora y significancia.

**Referencia:** persistencia con MAE de 454,74 kWh en el test de 69 filas.
El proyecto no tiene un champion promovido. Los resultados son exploratorios;
un mejor ranking individual de test NO justifica promocionar un modelo.

Los modelos v1 entrenados se presentan en sección separada porque su esquema
de validación es distinto al de la competencia extensiva, aunque sus métricas
de test se calcularon sobre la cohorte de 69 filas.

## Ejecución

Vista previa sin acceso a Google ni escritura:

    uv run python cli.py comparativas

Actualizar ambas pestañas y sus gráficos:

    uv run python cli.py comparativas --publicar

Actualizar únicamente OCR/LLM:

    uv run python cli.py comparativas --solo ocr --publicar

Actualizar únicamente modelos:

    uv run python cli.py comparativas --solo modelos --publicar

Interfaz de módulo equivalente:

    uv run python -m ml.comparisons --only all --write

Para fijar reportes específicos reproducibles se pueden pasar opciones
--ocr-report, --classic-report, --neural-report y --trained-report
al módulo. El modo predeterminado selecciona automáticamente las corridas
completas más recientes que cumplan sus criterios de compatibilidad.

## Actualización de gráficos

La función replace_comparison prepara valores, aplica formato al encabezado
y crea o actualiza gráficos con addChart / updateChartSpec en la pestaña
correspondiente. Conserva los títulos y actualiza los mismos objetos, evitando
duplicarlos en ejecuciones posteriores.

Las tablas solo cambian cuando se vuelve a ejecutar el comando de publicación:
no se actualizan por sí solas al ingresar una nueva factura. Se deben ejecutar
primero las evaluaciones que produzcan los nuevos reportes JSON.

## Pruebas y limitaciones

tests/test_comparisons.py valida los porcentajes, los seis campos, la
selección por validación antes de test, la protección contra comparaciones
incompatibles, la vista previa sin conexión y el uso idempotente de gráficos.

Pendientes metodológicos: referencia independiente y más diversa para OCR;
más meses de histórico, validación fuera de distribución y repetición
experimental para ML. Ninguna cifra debe presentarse como promesa de
exactitud futura.


## Revisión de presentación de las comparativas (noviembre de 2026)

Se inspeccionaron ambas pestañas directamente mediante la API de Google Sheets
y se corrigió la disposición de los gráficos originales (anclados en columna R).
Ahora se encuentran debajo del resumen y se ubican en la columna A:

- **Comparativa_OCR_LLM**: resumen de tres métodos (filas 6 a 9), seis campos
  evaluados (filas 12 a 18), cuatro referencias cotejadas manualmente (filas
  21 a 24) y una galería de dos gráficos: extracción completa y concordancia
  por campo. Las filas libres evitan que los gráficos tapen las tablas.
- **Comparativa_Modelos**: resumen de dos referencias y trece familias (filas
  6 a 21), gráficos MAE y WAPE en test común, detalle de 102 configuraciones
  con filtros nativos (encabezado en la fila 92) y dos modelos registrados v1.

Se ajustaron anchos de columna, porcentajes, decimales, subtítulos y avisos
metodológicos. La primera columna permanece inmovilizada. Los encabezados
tienen notas explicativas sobre CV MAE, Test MAE, WAPE, R², P(mejor persistencia)
y el sesgo de la referencia OCR.

Los gráficos se actualizan por título, preservan su ID y se reposicionan
incluso si existían anteriormente; las republicaciones no crean duplicados.

Vista previa sin Google: uv run python cli.py comparativas
Publicación: uv run python cli.py comparativas --publicar

El proceso valida ambos reportes antes de escribir en Sheets. Las escrituras
en pestañas diferentes no son una transacción única, por lo que, si una
operación falla, podría requerirse repetir la publicación.

Se conservan todas las cifras originales, sus fuentes y limitaciones:
15 facturas evaluadas, 4 referencias manuales, 102 configuraciones
predictivas y 69 observaciones de test. El dashboard no entrena modelos
ni invoca Gemini al ser actualizado.

## Cambio de cohorte y selección automática de experimentos (19/11/2026)

Al publicar una nueva competencia completa (13 familias, 102 configuraciones)
sobre el snapshot bd_energia_20261119_161533.csv, el test pasó de 69 a
70 observaciones y la persistencia de 454,74 a 448,24 kWh de MAE.
El comando de comparación inicialmente falló porque intentó incluir dos
candidatos v1 entrenados con la cohorte antigua de 69 observaciones.

La selección automática en ml/comparisons.py prioriza ahora el reporte
completo compatible más reciente y cuenta cada configuración una sola vez.
Los candidatos de otro test o referencia **no se mezclan**: se muestran
solo sus identificadores y los motivos de exclusión, sin compararlos
numéricamente. La ejecución del 19/11 generó 102 configuraciones de 13
familias (n=70), excluyó del ranking los dos candidatos v1 antiguos
(n=69) y publicó correctamente las dos pestañas.

Para actualizar:
uv run python cli.py comparativas --publicar

Los reportes de 18/11 permanecen como evidencia histórica; la actualización
no los elimina y sus resultados no son directamente comparables con el
snapshot nuevo sin evaluación común.
