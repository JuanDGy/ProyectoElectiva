# Comparativa: OCR local vs. LLM (Gemini)

Fecha: 18 de noviembre de 2026. Muestra aleatoria de **15 facturas** ya procesadas,
elegidas con semilla fija sobre las 613 de `BD_energia` que tienen PDF en
`02_FACTURAS_ENERGIA`. Se comparan los seis campos directos: cuenta, número de
factura, kWh, total a pagar, inicio y fin del período.

Script: `scripts/compare_ocr_llm.py` · Resultados:
`reports/ocr_vs_llm_20261118_153814.{json,png}`.

## Cómo se midió

- **OCR local**: `services/pdf_text.extract_text` (texto nativo + EasyOCR si hace
  falta) y `pipeline/invoice_rules.extract_invoice`, es decir, el pipeline real.
- **LLM**: Gemini `gemini-3.1-flash-lite` con el PDF completo, salida JSON con
  esquema, temperatura 0 y razonamiento desactivado (`thinkingBudget: 0`).
- **LLM + regla**: el LLM propone y una regla determinista aplica el número que el
  propio documento declara como `TOTAL A PAGAR`. No usa la extracción OCR.
- **Referencia**: los campos revisados de `BD_energia`. Como esa base la produjo
  el propio OCR, la comparación **favorece al OCR**. Para reducir ese sesgo, las
  cuatro facturas donde OCR y LLM discreparon se verificaron a mano contra la
  imagen renderizada del PDF (`data/eval/gold_ocr_llm.json`).

## Resultados

Precisión por campo sobre los evaluables (15 facturas, 90 campos):

| Campo | OCR | LLM solo | LLM + regla |
|---|---:|---:|---:|
| Cuenta | 100% | 100% | 100% |
| Número de factura | 100% | 93,3% | 93,3% |
| Consumo kWh | 100% | 100% | 100% |
| Total a pagar | 100% | 80,0% | **100%** |
| Inicio del período | 100% | 100% | 100% |
| Fin del período | 100% | 100% | 100% |
| **Facturas completas** | **100%** | **73,3%** | **93,3%** |

Tiempo medio por factura: **OCR 8,1 s** (0,01–0,9 s con texto nativo; 38–43 s en
las 4 que necesitaron OCR de imagen) · **LLM 3,7 s** (2,2–16,7 s). Consumo:
15.690 tokens de entrada y 4.906 de salida para 15 facturas, es decir ~1.046 y
~327 por factura.

En las 4 facturas verificadas a mano: OCR 4/4 completas, LLM solo 0/4, LLM + regla
3/4.

## Los tres errores del LLM, y por qué son sistemáticos

1. **Total a pagar en facturas de $0** (3 de 15). La factura trae `TOTAL A PAGAR $0`
   pero también `TOTAL ENERGÍA $430.930`. El modelo elige el número grande y
   naranja. Es el mismo error en las tres. La regla determinista lo corrige porque
   el texto del PDF dice literalmente `TOTAL A PAGAR $0`.
2. **Dígito 6/8 en el número de factura** (1 de 15). El documento dice
   `361491624-8`; el modelo devolvió `361491624-6`. El OCR local acertó.
3. **Cuenta padre** (corregido con el prompt). Las facturas ENEL muestran
   `NÚMERO DE CLIENTE` (la cuenta real) y, en el resumen ejecutivo, `Cuenta padre
   No.`. El primer prompt tomaba la segunda. Al nombrar explícitamente el recuadro
   correcto y prohibir el señuelo, el error desapareció en las 15.

El prompt final exige además **evidencia literal por campo**, y el modelo la
entrega: por ejemplo `{"campo": "valor_facturado", "cita": "TOTAL A PAGAR $142.110"}`.
Eso permite auditar cada valor sin volver a abrir el PDF.

## Qué significa para el proyecto

- **El OCR local sigue siendo el motor principal.** Sobre facturas ENEL con texto
  nativo es más rápido (centésimas de segundo) y más preciso que el LLM. La
  evaluación de octubre ya lo situaba en 99,38% de campos correctos sobre 100
  facturas procesadas (`docs/EVALUACION_OCR_AMPLIADA.md`).
- **El LLM no reemplaza al OCR; sirve de respaldo y de auditor.** Aporta en dos
  escenarios concretos: PDFs escaneados o de layout desconocido, y contraste de
  los valores ya escritos. En esta muestra encontró una factura mal transcrita en
  la hoja (`3614916246` en lugar de `3614916248`), que el OCR sí había leído bien.
- **Nunca debe escribir solo.** El error del total en facturas de $0 es silencioso
  y plausible: sin la regla, tres filas habrían entrado con un valor 400 veces
  mayor. Cualquier uso del LLM sobre datos reales necesita validación determinista
  y revisión antes de aplicar.

## Reproducir

```bash
# Requiere GEMINI_API_KEY en .env
uv run python scripts/compare_ocr_llm.py --limit 15 --seed 7
```

Tarifa y disponibilidad cambian: los modelos `gemini-3.8-flash` y `gemini-3.5-flash`
respondieron 429/503 o agotaron el tiempo durante esta prueba; `gemini-3.1-flash-lite`
fue el único estable. El modelo se puede cambiar con `GEMINI_MODEL` o `--model`.
