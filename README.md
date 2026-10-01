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
Todavía no recorre automáticamente todas las subcarpetas del histórico 2025.

## Flujo actual

1. Autorizar Google Drive y Sheets con OAuth del usuario.
2. Tomar un PDF de `01A_LISTAS_PARA_PROCESAR` y moverlo a `01B_PROCESANDO`.
3. Leer el texto con PyMuPDF; usar EasyOCR local en español en las páginas que
   no alcancen `PDF_MIN_TEXT_CHARACTERS` caracteres alfanuméricos.
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

La hoja `LISTA_CUENTAS_ENERGIA` existente no se modifica ni se usa. `Predicciones`
usa: Fecha ejecución, Sede, Año, Mes, Consumo predicho (kWh), Modelo.

## Modelado: estado actual y siguiente etapa

`ml/train.py` lee únicamente `BD_energia`, construye rezagos y promedios móviles,
y permite entrenar Regresión Lineal o Random Forest. Guarda modelos y métricas
MAE, RMSE y R² en `models/`.

```bash
uv run python -m ml.train --model linear
uv run python -m ml.train --model random_forest
uv run python -m ml.predict --help
```

La implementación de ML sigue siendo una base experimental: falta validar la
cobertura mensual, duplicados, varias cuentas por sede y la separación temporal
de entrenamiento/evaluación. El objetivo actual es la siguiente observación de
consumo, que solo equivale al mes siguiente cuando no hay huecos. La predicción
requiere suministrar los rezagos manualmente. Los modelos anteriores que incluían
el tipo de servicio deben reentrenarse con las nuevas variables.

Antes de evaluar el modelo con datos reales, hay que estructurar el histórico
de Drive y guardar una copia reproducible del dataset. La extracción del valor
facturado está implementada; la predicción de costos todavía no.

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

## MLOps

Ciclo de vida del modelo (tracking MLflow local, registro champion/challenger,
snapshots versionados con MD5, monitoreo de drift): ver `docs/MLOPS.md`.

```bash
uv run python -m ml.snapshot                 # respaldar BD_energia
uv run python -m ml.train --model linear     # entrenar + tracking
uv run mlflow ui                             # comparar experimentos
uv run python -m ml.promote --list           # comparar con el champion
uv run python -m ml.predict --sede CAMPUS --year 2026 --month 4 --no-log
uv run python -m ml.monitor                  # exit 2 => reentrenar
```
