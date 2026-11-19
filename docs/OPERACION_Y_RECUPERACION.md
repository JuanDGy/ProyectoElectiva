# Operación segura y recuperación — EcoData ECCI

Fecha: 18 de noviembre de 2026. Documento para entregar y como base del informe académico.
Alcance: 01C_PENDIENTES, Drive, Gemini y BD_energia en Google Sheets.

## 1. Separación de las etapas

1. **Proponer:** OCR local primero, Gemini solo si faltan campos; guarda texto, citas, valores, ID del PDF y huella SHA-256.
2. **Renovar:** verifica PDF actual y texto OCR de una propuesta antigua; añade SHA-256 sin consumir tokens de Gemini ni escribir en Google.
3. **Auditar:** compara campos, duplicados en BD_energia, metadatos Drive y opcionalmente SHA-256. Es lectura exclusivamente.
4. **Aprobar:** una persona abre el PDF y escribe la palabra APROBAR de manera individual en la terminal. La autorización queda vinculada a la huella.
5. **Aplicar:** vuelve a validar, comprueba aprobación, SHA, nombre, ubicación y duplicados antes de mover, renombrar y escribir en BD_energia.

No existe una transacción distribuida entre Drive y Sheets. Los eventos y compensaciones son mecanismos de recuperación, no garantías ACID.

## 2. Comandos

Ejecutar desde la raíz del proyecto:

    uv run python cli.py estado
    uv run python -m pytest -q
    uv run python cli.py pendientes revisar --limite 10

Para renovar propuestas antiguas sin invocar Gemini:

    uv run python cli.py pendientes renovar --archivo data/llm_proposals/pending_revisado.json --limite 38

El lote renovado de la auditoría del 18/11/2026 se guardó en:

    data/llm_proposals/verified_20261118_174637.json

Auditoría de solo lectura contra los sistemas reales:

    uv run python cli.py pendientes auditar --archivo data/llm_proposals/verified_20261118_174637.json --online --verificar-pdf

Resultado observado: 50 documentos, 15 VERIFICADA y 35 REVISAR. Esto acredita concordancia con reglas y contenido comprobado, pero no sustituye revisión humana del valor y el consumo.

Una persona autorizada debe abrir los PDF y aprobar individualmente desde una terminal interactiva:

    uv run python cli.py pendientes aprobar --archivo data/llm_proposals/verified_20261118_174637.json

El comando genera un archivo nuevo con nombre approved_FECHA.json y no mueve nada. Solo cuando esté revisado y respaldado, el paso independiente de aplicación es:

    uv run python cli.py pendientes aplicar --archivo data/llm_proposals/approved_FECHA.json

NO se ejecutó este último comando sobre facturas reales en la revisión de noviembre de 2026.

## 3. Matriz de riesgos

| Situación | Control disponible | Riesgo residual |
|---|---|---|
| La IA confunde TOTAL ENERGÍA y TOTAL A PAGAR | Validación determinista y revisión humana | Documentos sin etiqueta literal requieren cotejo |
| PDF sustituido manteniendo ID/nombre | SHA-256 recalculado antes de mover | Posibles carreras entre comprobación y actualización |
| Factura ya registrada | Comparación ID y cuenta + factura | Concurrencia entre dos instancias sin bloqueo |
| Error de Sheets antes de guardar | Intento de reversión y bitácora | Falla adicional en la reversión |
| Sheets guardó pero devolvió timeout | Consulta de fila y conciliación por ID/clave | Una nueva falla impide conocer el resultado |
| Aprobación no existente o PDF diferente | Se bloquea aplicación | Necesidad de revisión por persona responsable |

La tarifa COP/kWh funciona como validación de plausibilidad, no como regla general que sustituya la factura. Los documentos con consumo nulo y cobro fijo no deben descartarse automáticamente.

## 4. Bitácora de aplicación

El programa guarda eventos en data/llm_proposals/applied_FECHA.jsonl:

- SIN_APROBACION_MANUAL: falta autorización individual válida.
- SIN_HUELLA o PDF_CAMBIADO: documento sin firma de integridad o contenido distinto.
- DESACTUALIZADA: ubicación o nombre diferente de la propuesta.
- DUPLICADA: identificador o clave de factura ya presentes.
- PREPARADA: inicio de una operación, NO confirma éxito.
- APLICADA: Drive y Sheets confirmaron el resultado.
- APLICADA_RECONCILIADA: tras error de respuesta, la fila se comprobó por ID y cuenta/factura.
- ERROR_APLICACION: fallo y resultado de rollback.
- INDETERMINADA: Sheets no pudo comprobarse después del error. Requiere intervención manual.

## 5. Procedimiento ante INDETERMINADA

1. Suspender las nuevas aplicaciones del lote, respaldar propuesta y bitácora.
2. Consultar en lectura la fila del ID en BD_energia y el nombre/carpeta del PDF en Drive.
3. Si la fila existe y el PDF está en 02, documentar éxito sin insertar una segunda fila.
4. Si la fila no existe y el PDF permanece en 02, comprobar la ausencia y acordar si restaurar o completar el registro. No ejecutar reintentos a ciegas.
5. Si los servicios siguen indisponibles o hay conflicto, registrar una incidencia pendiente de conciliación con evidencia y responsable.

## 6. Pruebas y trazabilidad

Las pruebas de extremo a extremo usan servicios simulados para recorrer Drive, OCR, Gemini, propuesta, renovación, auditoría y registro. Otras pruebas cubren cambio de PDF, duplicados, error de escritura y rollback, éxito con respuesta perdida, resultado indeterminado y aprobación humana.

La suite consolidada del 18/11/2026 tiene 170 pruebas aprobadas, dos advertencias no bloqueantes. Compilación y Git diff --check correctos. La prueba simulada no equivale a ejecutar la operación con archivos reales; esta se reserva para el lote aprobado.

## 7. Entrega y próximos controles

Antes de habilitar operaciones masivas, se requiere: revisión real de las 15 propuestas verificadas; lote piloto aprobado; comparación posterior entre Drive y BD_energia; prevención de doble ejecución concurrente; y conservación de informes de auditoría, sin publicar credenciales ni facturas personales.

Para informe académico: añadir diagramas, metodología experimental, resultados OCR/LLM con sesgos, métricas de los modelos v2, capturas de Google Sheets, conclusiones y anexos de pruebas documentadas.

## 8. Bloqueo de concurrencia local

Al ejecutar la aplicación, el programa crea de forma exclusiva
data/llm_proposals/pending_apply.lock y libera ese archivo al terminar. Si otro
proceso local pretende aplicar simultáneamente, la CLI lo rechaza. Si el proceso
muere abruptamente, el archivo puede persistir; nunca se debe borrar sin
revisar primero que no haya una ejecución activa y contrastar la bitácora.
El bloqueo es **local** al directorio del proyecto; no impide que otro equipo
escriba en el mismo Google Sheet. Para operación distribuida hará falta un
mecanismo remoto de coordinación o serializar la ingesta.
