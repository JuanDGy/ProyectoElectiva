# EcoData ECCI — Energía en Python

Esta iteración prepara datos de facturas de energía eléctrica de ENEL para
analizar el histórico de la Universidad ECCI y desarrollar modelos de predicción
por sede. El código se ejecuta en Python con dependencias gestionadas por uv.

El histórico está en Google Drive. La carpeta de trabajo 01A es la bandeja de
entrada del procesador actual; `main.py` procesa un PDF por ejecución por defecto.
Puedes cambiar la cantidad con `--limit N` o procesar todos los pendientes con
`--all`:

```bash
uv run python main.py --limit 5
uv run python main.py --all
```

Sin parámetros procesa uno. Las opciones `--limit`, `--all` y `--file-id`
son excluyentes. El lote se detiene si no quedan PDFs o ante un error técnico;
los documentos enviados a revisión o sin clasificar cuentan como procesados.
Busca también en las subcarpetas de 01A. Por ejemplo, `01A/CAMPUS/factura.pdf`
se procesa con sede `CAMPUS`; las carpetas anidadas conservan su ruta como sede.
La sede se guarda antes de mover el archivo, para conservarla en los reintentos.
El histórico completo se importa con `--history` (ver más abajo) sin mover originales.

## Flujo actual

1. Autorizar Google Drive y Sheets con OAuth del usuario.
2. Tomar un PDF de `01A_LISTAS_PARA_PROCESAR` y moverlo a `01B_PROCESANDO`.
3. Leer el texto con PyMuPDF; usar EasyOCR local en español en las páginas que
   no alcancen `PDF_MIN_TEXT_CHARACTERS` caracteres alfanuméricos. En el formato
   comercial ENEL, recuperar con OCR localizado la etiqueta de pago que es una
   imagen, conservando los dígitos nativos del importe.
4. Identificar facturas de energía mediante términos del texto.
5. Extraer número de cliente, número de factura, consumo, valor total y periodo
   con reglas locales para facturas ENEL, sin catálogo ni API de IA.
6. Archivar resultados completos en `02_FACTURAS_ENERGIA` y `BD_energia`.
   Enviar datos incompletos a `01C_PENDIENTES` y `Ajuste_Energia`.
   Los documentos sin evidencia de energía van a `01D_NO_PROCESABLES`.
7. Ante un error técnico, devolver a 01A el original si sigue en 01B.

La lectura y extracción no consultan APIs de IA. EasyOCR se ejecuta localmente
en CPU. La predicción de consumo utiliza scikit-learn y es independiente del
procesamiento de documentos. Drive y Sheets siguen usando sus APIs con OAuth.

## Instalación con uv

Requiere Python 3.12+ y uv. Desde la raíz del proyecto:

```bash
uv sync --frozen
```

PyMuPDF, EasyOCR y PyTorch para CPU están declarados en `pyproject.toml` y
bloqueados en `uv.lock`. No se necesitan instalaciones de Poppler, Tesseract ni
paquetes de idioma del sistema. `requirements.txt` permite instalar con pip, pero
no reproduce el bloqueo ni la selección explícita de PyTorch CPU de uv.

EasyOCR descarga sus pesos al primer uso y los conserva en `.cache/easyocr/`.
El OCR renderiza a 300 DPI, usa un lienzo de detección de hasta 3500 píxeles y
conserva las lecturas por bloques, sin fusionarlas en párrafos. Esto mejora la
lectura de importes pequeños, con mayor tiempo de procesamiento en CPU. Las
lecturas contradictorias o los campos no reconocidos por las reglas pasan a revisión.

La primera descarga requiere internet; los pesos no están gestionados por uv.lock.
Para prepararlos antes de procesar documentos:

```bash
uv run python -m scripts.prepare_ocr
```

Estos comandos también funcionan en PowerShell; no hace falta activar `.venv`.

## Google y configuración

Habilita Google Drive API y Google Sheets API en tu proyecto de Google Cloud.
Guarda el cliente OAuth de tipo Desktop en `credentials.json`. La autorización
usa los permisos de Drive y Sheets y guarda el token en `token.json`, asociado al
usuario que autoriza. Las carpetas se identifican por ID, incluso si se renombran.

Copia `.env.example` a `.env` y completa:

- `GOOGLE_CLIENT_SECRET_FILE` y `GOOGLE_TOKEN_FILE`.
- `DRIVE_FOLDER_01A`, `DRIVE_FOLDER_01B`, `DRIVE_FOLDER_01C`,
  `DRIVE_FOLDER_01D`, `DRIVE_FOLDER_02_ENERGIA`.
- `GOOGLE_SPREADSHEET_ID` y los nombres de las hojas.
- `DRIVE_HISTORY_ROOT_ID`: carpeta raíz del histórico, para conservar la sede de origen.
- `PDF_MIN_TEXT_CHARACTERS=100`: entero mayor que cero, aplicado por página.

En Linux/macOS: `cp .env.example .env`. En PowerShell:
`Copy-Item .env.example .env`. Conserva tu `.env` existente si ya está configurado.

```bash
uv run python -m scripts.auth_google
uv run python -m scripts.check_setup
uv run python main.py
```

La autenticación no exige configurar antes las carpetas. Si cambias los
permisos autorizados, vuelve a autorizar el token con los permisos de Drive y
Sheets. Nunca publiques `.env`, `credentials.json` ni `token.json`.

## Procesamiento sin catálogo

No se consulta ni se necesita una lista previa de cuentas. Se extraen directamente
el número de cliente ENEL (con cero inicial y dígito de verificación), el número
de factura que aparece debajo, los kWh, el valor total facturado y el periodo.
La cuenta se guarda como texto sin separadores; no se confunde con la factura.
Un consumo o valor de cero es válido. Una cuenta nueva puede procesarse.

La sede se obtiene de los metadatos guardados o del recorrido de carpetas bajo
`DRIVE_HISTORY_ROOT_ID` o bajo 01A.
Para procesar un PDF del histórico conservando el original:

```bash
uv run python main.py --file-id ID_DEL_PDF
```

Se crea una copia en 01A, guardando la sede y el ID del archivo original como
metadatos de Drive. Solo esa copia se mueve y renombra. Sin `--file-id`, el
procesador toma un documento de 01A. Si ya perdió su origen y no conserva esos
metadatos, no se inventa la sede: queda en revisión, aunque su cuenta sea válida.

### Importar el histórico completo

`--history` recorre **todo** el árbol de `DRIVE_HISTORY_ROOT_ID` (carpetas y
subcarpetas, paginado) y procesa los PDFs **en su sitio**: no copia, no mueve y
no renombra los originales.

```bash
uv run python main.py --history --limit 10
uv run python main.py --history --all
```

Antes de escribir, descarta las facturas cuya `Cuenta` + `N° Factura` ya está en
`BD_energia`, así que reimportar el histórico no duplica filas ni siquiera
cuando el mismo documento existe con otro ID. Cada archivo tratado se registra
en `data/history_manifest.jsonl` (local): una nueva ejecución se reanuda donde
quedó. Los errores técnicos no se registran, de modo que el siguiente intento los
reintenta. `--history` no se puede combinar con `--file-id`.

El OCR corre en CPU: cada factura consume varios segundos, por lo que una
importación completa de más de mil PDFs tarda horas. Conviene lanzarla por lotes
y revisar el conteo final (intentados, importados, duplicados, fallidos).

`BD_energia` mantiene las columnas existentes y añade `ValorFacturado` al final:

```text
ID | Código | Oficina | Cuenta | N° Factura | kWh | Año | Mes |
Inicio periodo | Fin periodo | Link factura | Observaciones | ValorFacturado
```

Código queda vacío y no es obligatorio. `Ajuste_Energia` conserva Estado después
de Observaciones y añade ValorFacturado al final. Los encabezados se crean si la
hoja está vacía; si falta solo la nueva columna, se añade. Las filas antiguas sin
encabezado que coinciden con el formato del pipeline se conservan insertando el
encabezado arriba. Un formato desconocido se rechaza sin sobrescribir sus datos.

Se guardan resultados incompletos en Ajuste_Energia. La cuenta, factura, sede,
kWh, valor, año, mes y fechas son obligatorios para marcar un resultado completo.
Los conflictos entre valores repetidos del texto se conservan en Observaciones para revisión.
Las reglas reconocen el formato ENEL; un formato distinto puede necesitar nuevas
reglas. No se infieren importes ni se corrigen dígitos ambiguos automáticamente.

También se reconoce el formato comercial con tabla de lecturas, como las facturas
de Biblioteca. Los kWh se toman de la energía activa facturada, verificando la
diferencia de lecturas, el factor y el valor de la fila. El periodo se obtiene del
rango de fechas de ese formato. El recuadro de pago del pie se lee a 400 DPI para
identificar su etiqueta; el importe se toma del texto nativo de ese mismo recuadro.
Un saldo a crédito puede tener consumo positivo y total a pagar de cero. No se
sustituye ese cero por el subtotal de energía. Si no se identifica la etiqueta o
la tabla no es coherente, la factura sigue pendiente de revisión.

La hoja `LISTA_CUENTAS_ENERGIA` existente no se modifica ni se usa. `Predicciones`
usa: Fecha ejecución, Sede, Año, Mes, Consumo predicho (kWh), Modelo. El pronóstico
**por cuenta** se publica aparte, en `Predicciones_Cuenta` (ver abajo), para no
romper ese contrato de total por sede.

## Modelado y MLOps

La primera versión predice consumo eléctrico a un mes **por cuenta**, conservando la sede como trazabilidad. Usa snapshots versionados, rezagos por calendario, validación temporal y comparación de regresión lineal/Random Forest con referencias simples. No se imputan consumos objetivo ni se usan datos futuros. Los candidatos quedan registrados sin promoción automática.

Consulta [docs/MLOPS.md](docs/MLOPS.md) para las reglas y comandos, [la auditoría](docs/AUDITORIA_MODELO_V1.md) para los problemas de datos y [los resultados](docs/RESULTADOS_MODELO_V1.md) para el entrenamiento real.

```bash
uv run python -m ml.snapshot
uv run python -m ml.train --model both --test-start 2025-11-01 --test-end 2025-12-01
uv run python -m ml.promote --list
uv run mlflow ui --backend-store-uri sqlite:///mlruns/v1_tracking.db
# Sustituir ID_CANDIDATO por el ID del modelo que se quiere probar.
uv run python -m ml.predict --cuenta 04525220 --year 2025 --month 11 \
  --model-path models/candidates/ID_CANDIDATO/model.joblib

# Pronóstico por cuenta: primero en pantalla, luego en la hoja Predicciones_Cuenta.
uv run python -m ml.publish --model-path models/candidates/ID_CANDIDATO/model.joblib
uv run python -m ml.publish --model-path models/candidates/ID_CANDIDATO/model.joblib --write

# Reporte visual (PNG + HTML) del candidato más reciente.
uv run python -m ml.report
```

`ml.publish` pronostica el **primer mes sin ninguna observación** (por defecto) o
el mes indicado con `--year`/`--month`. Publica una fila por cuenta con sede,
modelo, antigüedad del último dato y política de rezago. Solo escribe con
`--write`; la pestaña se crea si no existe y reejecutar no duplica filas (la
identidad es cuenta + año + mes + modelo).

`ml.report` deja `reports/report_FECHA/` con `index.html` y cuatro gráficos:
modelos frente a la persistencia, distribución de errores, cobertura por mes y
series por cuenta con histórico, prueba y pronóstico. Es de solo lectura.

Cada candidato exporta train/test, exclusiones, validaciones y predicciones con hashes de datos y código. La inferencia v1 entrega salida local. Los totales por sede y los costos siguen requiriendo conciliar cuentas/sedes.

## Extracción con LLM (opcional)

El pipeline local no necesita credenciales de IA. Si existe `GEMINI_API_KEY` en `.env`, se puede usar Gemini como extractor alternativo y como auditor de lo ya escrito:

```bash
uv run python scripts/compare_ocr_llm.py --limit 15 --seed 7
```

Compara, sobre las mismas facturas y con referencia revisada, el OCR local contra el LLM (solo y con una regla determinista que respeta el `TOTAL A PAGAR` declarado). Escribe `reports/ocr_vs_llm_FECHA.{json,png}`.

Resultado y límites en [docs/COMPARATIVA_OCR_LLM.md](docs/COMPARATIVA_OCR_LLM.md): el LLM no supera al OCR en facturas con texto nativo, falla en facturas de `$0` si no se valida, y aporta como respaldo en escaneos y como contraste de la hoja. Por eso **propone, no escribe**: cualquier valor sugerido debe pasar la validación determinista y la revisión humana antes de tocar `BD_energia`.

### Revisar la cola de `01C_PENDIENTES`

La revisión es en dos pasos: primero se genera una propuesta, después se aplica.
Sin `--apply` el comando no mueve archivos ni escribe en Sheets.

```bash
# 1) Propuesta. OCR local primero; solo los campos vacíos se le piden al LLM.
uv run python -m pipeline.pending --limit 50

# 2) Leer data/llm_proposals/pending_FECHA.json y decidir.

# 3) Aplicar solo lo que vuelve a validar al momento de aplicar.
uv run python -m pipeline.pending --apply --from data/llm_proposals/pending_FECHA.json
```

Cada fila se contrasta con tres fuentes independientes antes de moverse: el nombre
del archivo (que escribió la propia tubería), el texto del PDF y la etiqueta
`TOTAL A PAGAR`. Un valor propuesto por el LLM sin cita literal, o que contradiga
el documento, deja la factura en `01C` y se lista con su motivo. El total del
documento siempre gana sobre la propuesta del modelo.

Además del contraste, el total tiene que ser un valor a pagar creíble: nunca se
acepta un negativo (es un saldo a favor), ni un cero que el documento no declare
(`TOTAL A PAGAR $0` o `FACTURA SIN COBRO`), ni un total que implique un precio por
kWh imposible. Los casos dudosos —consumo en cero con cobro, precio raro— quedan
como aviso y se ven en el JSON.

```bash
# Recalificar un JSON ya generado tras cambiar una regla: no repite OCR ni LLM.
uv run python -m pipeline.pending --revalidate --from data/llm_proposals/pending_FECHA.json
```

`--apply` mueve el PDF a `02_FACTURAS_ENERGIA/<sede>/`, lo renombra, agrega la fila
a `BD_energia` con `Observaciones` que dice qué completó el LLM, y escribe
`data/llm_proposals/applied_FECHA.jsonl` con la carpeta de origen para poder
revertir. Lo ya registrado (misma cuenta + factura, o mismo ID) se omite como
duplicado. `--to-01d` mueve además los archivos que no son de energía a
`01D_NO_PROCESABLES`.

## Docker

La imagen instala las dependencias bloqueadas y prepara los modelos OCR durante
la construcción. Por defecto procesa una factura y termina.

```bash
docker build -t ecodata-python .
docker run --rm --env-file .env \
  -v "${PWD}/credentials.json:/app/credentials.json:ro" \
  -v "${PWD}/token.json:/app/token.json" \
  ecodata-python
```

En PowerShell, escribe `docker run` en una línea o usa el acento grave como
continuación. Los archivos de credenciales y token deben existir en el equipo,
y sus rutas en `.env` deben corresponder a `credentials.json` y `token.json`
dentro del contenedor. Las credenciales no se incluyen en la imagen.

## Verificación

```bash
uv run python -m pytest -q
```

## Operación unificada y visualización en Google Sheets

La nueva interfaz de consola abre un menú con `uv run python cli.py`. El comando
`uv run python cli.py --help` expone opciones en español para importar el histórico,
examinar pendientes, comparar OCR/Gemini, competir con modelos y publicar visualizaciones.

La competencia ampliada incorpora redes **RBF**, **MLP**, **LSTM** de dos rezagos y
**SVR** radial. Para ejecutarla sobre el mismo test temporal:

```bash
uv run python cli.py competencia --modelos rbf,lstm,mlp,svr --enfoques level
```

Los pronósticos por cuenta se guardan en `Predicciones_Cuenta`; tres gráficos nativos
en `Graficos_Predicciones` muestran una cuenta, el top 15 y la cobertura mensual.
El comando de actualización es:

```bash
uv run python cli.py graficos --publicar
```

El modo sin `--publicar` es de consulta. Para tramitar pendientes sin cambiar archivos,
usar `uv run python cli.py pendientes revisar --limite 10`. Para aplicar un JSON
revisado, usar `uv run python cli.py pendientes aplicar --archivo RUTA`.

Consulta la [guía integral](docs/GUIA_INTEGRAL_PROYECTO.md) como base del informe final.


## Auditoría documental y autorización antes de aplicar

Las propuestas antiguas se deben renovar para incorporar la huella SHA-256. La CLI
de producción bloquea cualquier aplicación de facturas sin revisión humana
individual y huella coherente. Renovar y auditar **no modifican Drive ni Sheets**:

    uv run python cli.py pendientes renovar --archivo data/llm_proposals/pending_revisado.json --limite 38
    uv run python cli.py pendientes auditar --archivo data/llm_proposals/verified_20261118_174637.json --online --verificar-pdf

Para aprobar cada PDF desde una terminal interactiva, verificarlo visualmente y
escribir APROBAR, uno a uno:

    uv run python cli.py pendientes aprobar --archivo data/llm_proposals/verified_20261118_174637.json

Solo después, con el JSON aprobado y respaldado:

    uv run python cli.py pendientes aplicar --archivo data/llm_proposals/approved_FECHA.json

La aplicación usa bitácora y reconciliación conservadora ante resultados
inciertos de Sheets. Consultar [operación y recuperación](docs/OPERACION_Y_RECUPERACION.md)
y [documentación general](docs/GUIA_INTEGRAL_PROYECTO.md).

**Evidencia al cierre:** suite de 170 pruebas correctas; auditoría remota de
50 PDFs (15 verificados, 35 en revisión). Ninguna factura real fue movida en
este cierre. No hay modelo champion promovido por la competencia v2.

## Comparativas OCR/LLM y modelos en Google Sheets

Además de `Predicciones_Cuenta` y `Graficos_Predicciones`, EcoData administra
dos pestañas nuevas: **Comparativa_OCR_LLM** y **Comparativa_Modelos**.

```bash
# Previsualizar tablas sin acceder a Google
uv run python cli.py comparativas

# Publicar ambas y actualizar sus gráficos nativos
uv run python cli.py comparativas --publicar

# Publicar solo una
uv run python cli.py comparativas --solo ocr --publicar
uv run python cli.py comparativas --solo modelos --publicar
```

La comparación OCR utiliza el reporte de 15 facturas y seis campos, con nota
expresa del sesgo de su referencia. La competencia agrupa 102 configuraciones
comparables de 13 familias, seleccionando una por validación **antes de mostrar
sus resultados de test**. Los dos modelos v1 registrados se exponen por separado.
Los gráficos se actualizan por título sin duplicarse.

Detalles, fuentes, metodología y límites:
[Comparativas en Sheets](docs/COMPARATIVAS_SHEETS.md).

## Selector de cuenta en los gráficos de consumo

La pestaña **Graficos_Predicciones** ahora tiene un desplegable en **B4**.
La lista contiene las cuentas del mismo modelo y mes que se está graficando.
Al seleccionar una cuenta, las fórmulas de las columnas **B** (consumo
real) y **C** (tramo hacia el pronóstico) consultan la tabla auxiliar
**Datos_Graficos_Cuenta** y el gráfico de línea se actualiza directamente
desde Google Sheets, sin ejecutar Python.

Se mantienen las cuentas con ceros iniciales, y los meses sin factura se
guardan como SIN_DATO en la fuente para evitar mostrar consumos falsos de
cero kWh. El tramo discontinuo comienza en el último consumo observado y
termina en la estimación. No es una serie de pronósticos mensuales intermedios.

Para reconstruir los datos y mantener la elección de B4 cuando siga siendo
válida:

    uv run python cli.py graficos --publicar

Este selector cambia solamente **la cuenta mostrada en el gráfico de
histórico**, no los resultados de las 45 predicciones ni los gráficos
comparativos de las otras cuentas. La lista se actualiza al publicar otro
periodo o modelo. La hoja técnica Datos_Graficos_Cuenta es un origen
administrado por el programa y no debe editarse a mano.


## Entrenamiento automático y sincronización por cuenta (2026)

La competencia experimental evalúa 13 familias. Para guardar **una
configuración ejecutable por familia**, elegir el menor MAE **de validación**
y registrar el resultado sin promover indebidamente un modelo:

    uv run python cli.py competencia
    uv run python cli.py entrenar --modelos all --seleccion automatica

No se promueve Champion si la opción ganadora no demuestra mejora frente a
persistencia y media móvil en el test, y estabilidad según el bootstrap.

Para publicar el modelo elegido por validación, reemplazando cuentas del mismo
periodo y archivando sus versiones anteriores:

    uv run python cli.py predicciones --modelo auto --anio 2026 --mes 2 --publicar --sincronizar

La hoja Predicciones_Cuenta conserva los resultados vigentes; las versiones
reemplazadas se archivan en Historial_Predicciones. El historial se escribe
**antes** del reemplazo. Las cuentas nuevas se añaden sin duplicados.

Para consultar o actualizar los tres gráficos:

    uv run python cli.py graficos --publicar

En Graficos_Predicciones, **B4** cambia la cuenta y **F4** cambia el modelo
vigente o histórico; los tres gráficos reaccionan directamente en Sheets.
El dashboard no interpreta los datos faltantes como consumo cero.

Estado de la corrida del 19/11/2026: 13 familias entrenadas, SVR radial
(delta) seleccionado por validación, **sin promoción Champion** porque su MAE
de test (463,05 kWh) no supera la persistencia (448,24 kWh). Se publicaron
49 predicciones de febrero 2026, 45 sobreescritas y 4 nuevas, conservando las
45 versiones anteriores de Random Forest. Este es un ejercicio retrospectivo.

Guía completa y recuperación: [Entrenamiento automático y sincronización
versionada](docs/ENTRENAMIENTO_AUTOMATICO_Y_SINCRONIZACION.md).


### Actualizar directamente Predicciones_Cuenta

    # Usar el último modelo seleccionado automáticamente y publicar en Sheets:
    uv run python cli.py actualizar-predicciones --publicar

    # Refrescar snapshot, competir, entrenar, publicar y actualizar gráficos:
    uv run python cli.py actualizar-predicciones --completo --publicar

Para consultar sin escribir omite --publicar. El comando completo exige
tener los datos de BD_energia actualizados; no procesa PDFs por sí solo.
La ejecución imprime cuántas cuentas se actualizaron, agregaron o quedaron
idénticas. Si el histórico no cambió, es correcto obtener cero cambios.
El dashboard se actualiza mostrando el modelo vigente, no el selector de
modelo que estuviera activo en una visualización anterior.
