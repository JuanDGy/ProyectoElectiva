# Conciliación local — 2026-10-01

Destino: `/home/steven/Projects/ecodata_python`.
Origen: `/home/steven/Projects/ProyectoElectiva` (conservado intacto y verificado por SHA-256).
Respaldo privado de ambas versiones: `/home/steven/Projects/ecodata_conciliacion_respaldo_20261001_155847`.

## Decisiones

| Componente | Versión conservada |
| --- | --- |
| Ingesta, Drive, Sheets y ejecución por lotes | ecodata_python |
| OCR localizado y reglas ENEL comercial/Biblioteca | ecodata_python |
| Pruebas de extracción y Biblioteca | ecodata_python |
| Dataset, snapshots, tracking, registro, promoción, predicción y monitoreo | ProyectoElectiva |
| Configuración ML y Docker | ProyectoElectiva |
| Dependencias | ProyectoElectiva; uv.lock resuelto y validado |
| README | Combinado: extracción vigente y ciclo MLOps actualizado |
| Credenciales, token y .env del destino | Preservados; fuera de Git |
| Registro de modelos | ProyectoElectiva, vacío (sin champion) |
| Historial MLflow | Copia consistente de SQLite: dos experimentos y dos ejecuciones |

El origen no incluye los directorios de artefactos de esas dos ejecuciones de
MLflow. Se conservan sus metadatos, pero los modelos históricos no pueden
recuperarse solo desde esta copia. No se generó un modelo real para sustituirlos.

## Git

Se inicializó un repositorio local. El commit `f2c5be1` guarda el código anterior;
la etiqueta `respaldo/antes-mlops` permite localizarlo. La rama
`integracion/mlops` registra la conciliación y se integra en `main` tras validar.
No se ha configurado un remoto.

## Validación

- `uv sync --frozen`: dependencias instaladas desde el bloqueo actualizado.
- `uv run --frozen python -m pytest -q`: 77 pruebas correctas.
- Prueba con datos sintéticos en un directorio temporal: entrenamiento de
  regresión lineal y Random Forest, tracking local, registro, selección inicial
  de champion, predicción sin escritura en Sheets y monitoreo.
- Ingesta y correcciones de Biblioteca idénticas al commit de respaldo.
- Integridad SQLite correcta; mismos conteos que la base de MLflow del origen.
- Hashes de los archivos originales de ProyectoElectiva sin cambios.

La prueba de integración comprueba funcionamiento técnico. El modelado conserva
las decisiones del origen: última fila por sede/mes, rezagos por observación y
objetivo siguiente observación. Hace falta validar su uso con varias cuentas por
sede y meses faltantes antes de considerar confiable un pronóstico real.
