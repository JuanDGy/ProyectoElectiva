# EcoData ECCI: entrenamiento automático y sincronización versionada

**Implementado:** 19 de noviembre de 2026

## Punto de recuperación

Commit de Git: **e707254**, en rama develop, anterior a todas las mejoras de esta sesión. Este commit solo cubre archivos versionados. Los modelos .joblib, los reportes locales y Google Sheets NO se restauran automáticamente mediante Git.

Respaldos privados locales, con permisos 0600, ignorados por Git:
- reports/backup_predicciones_cuenta_20261119_170347.json
- reports/backup_historial_predicciones_prelimpieza_20261119_170740.json

Para explorar el código antiguo sin modificar la carpeta activa:

    git worktree add ../ProyectoElectiva_checkpoint e707254

No ejecutar reset --hard sin conservar las modificaciones posteriores; no restaura Google Sheets. No subir respaldos ni credenciales a un repositorio público.

## Selección automática y reentrenamiento

Módulo: ml/train_auto.py. Adaptador de inferencia serializable: ml/forecast_estimator.py.

El sistema consume un reporte experimental compatible con el snapshot local actual. Cada familia aporta la configuración de menor MAE de validación temporal. Después se entrena un artefacto operativo por familia; los modelos level/delta/ratio generan resultados finales expresados en kWh. No se utiliza el test para elegir el ganador.

Hay 13 familias disponibles: Dummy(media), lineal, Ridge, Huber, Random Forest, Random Forest MAE, Extra Trees, Gradient Boosting, HistGradientBoosting, LSTM, RBF, MLP y SVR.

    uv run python cli.py competencia
    uv run python cli.py entrenar --modelos all --seleccion automatica

Si quieres repetir el benchmark antes del entrenamiento:

    uv run python cli.py entrenar --modelos all --evaluar

Se permite entrenar solamente algunas familias (si están evaluadas en el reporte):

    uv run python cli.py entrenar --modelos svr,rbf,lstm

Los modelos operativos y metadatos se escriben en models/candidates/. El elegido queda registrado en models/auto_selection.json y en reports/auto_selection_latest.json.

### Seguridad de la selección

El ganador por validación NO se convierte en Champion automáticamente solo por ganar el ranking. Para intentar la promoción automática debe también mejorar en test frente a persistencia y media móvil, con frecuencia bootstrap de mejoría frente a persistencia >= 0,95, y cumplir los controles del registro ml.registry. En caso contrario queda como seleccionado por CV (experimental).

Resultado verificado del 19/11/2026: SVR-Delta, MAE de validación 436,27 kWh, MAE de test 463,05 kWh y persistencia de test 448,24 kWh. **No se promovió Champion**. El snapshot usado tiene 633 filas, competencia con 13 familias/102 configuraciones y test de n=70.

Los reportes de test corresponden al ajuste experimental previo al refit operativo sobre todos los datos observados. No atribuir indebidamente esos errores de test al artefacto refit como una medición independiente.

## Sincronización y versiones en Google Sheets

La nueva modalidad --sincronizar exige --publicar. La clave de la vista vigente es Cuenta + Año + Mes.

    # Leer sin escritura
    uv run python cli.py predicciones --modelo auto --anio 2026 --mes 2

    # Sobrescribir cuentas ya publicadas y agregar cuentas nuevas
    uv run python cli.py predicciones --modelo auto --anio 2026 --mes 2 --publicar --sincronizar

Se conserva primero la versión anterior en Historial_Predicciones. Las combinaciones nuevas se insertan; las idénticas no se reescriben ni se archivan. Las claves duplicadas se rechazan para conciliación manual. No es una transacción atómica entre hojas; en caso de fallo es necesario revisar el estado de ambas antes de reintentar.

Predicciones_Cuenta añade Snapshot SHA-256 y Estado modelo. Las cuentas son texto (se conservan los ceros a la izquierda); los consumos son numéricos, aunque el formato visible agregue kWh. El lector solicita UNFORMATTED_VALUE a Google Sheets para evitar duplicados por cambios de presentación regional.

La tabla tiene filtro, encabezados inmovilizados, colores por antigüedad y resumen lateral. El periodo febrero 2026 es retrospectivo en noviembre de 2026: NO describirlo como pronóstico futuro.

Resultado comprobado: 49 predicciones vigentes de SVR (experimental): 45 registros reemplazados y 4 cuentas nuevas. Se preservaron las 45 predicciones anteriores de Random Forest en Historial_Predicciones. La reejecución idéntica arrojó 0 actualizaciones, 0 inserciones y 0 nuevos archivos.

## Visualización por cuenta y modelo

    uv run python cli.py graficos --publicar

En Graficos_Predicciones:
- B4: selecciona la cuenta.
- F4: selecciona el modelo vigente o histórico.

Los tres gráficos se actualizan directamente: histórico vs estimación, Top 15 y cobertura. No es necesario ejecutar Python cada vez que se cambia B4 o F4.

La hoja auxiliar Datos_Graficos_Cuenta almacena claves MODELO|CUENTA|MES y también el ranking y cobertura de cada modelo. Se preservan los meses sin factura como SIN_DATO y el cero válido como 0. Seleccionar una combinación cuenta/modelo no disponible produce un vacío, nunca una estimación inventada.

El sistema comprueba que al cambiar de SVR a Random Forest el Top 15 y la cobertura cambien de modelo. Al volver a publicar conserva los selectores cuando son compatibles con el periodo.

## Comandos de una operación completa

    cd /home/steven/Projects/ProyectoElectiva_0.1

    # Si hay facturas nuevas y ya fueron verificadas:
    uv run python -m ml.snapshot

    # Para datos actualizados, renovar evidencia experimental:
    uv run python cli.py competencia

    # Reentrenar, seleccionar y verificar las 13 familias:
    uv run python cli.py entrenar --modelos all --seleccion automatica

    # Ver predicciones sin escribir:
    uv run python cli.py predicciones --modelo auto

    # Sincronizar y conservar historial:
    uv run python cli.py predicciones --modelo auto --publicar --sincronizar

    # Actualizar todos los gráficos interactivos:
    uv run python cli.py graficos --publicar

    # Actualizar comparativas OCR y ML:
    uv run python cli.py comparativas --publicar

Los modelos y respaldos son locales y están fuera de Git. La selección experimental no implica superioridad respecto de persistencia. Los resultados y cambios de datos deben ser explicados con esa restricción en el informe académico.


## Actualización operativa integrada (19/11/2026)

Para actualizar la hoja Predicciones_Cuenta tras un entrenamiento ya ejecutado:

    uv run python cli.py actualizar-predicciones --publicar

Este comando usa el modelo auto registrado, verifica que su SHA-256 coincida
con el snapshot vigente, sincroniza las filas en Predicciones_Cuenta y publica
los tres gráficos. Las versiones anteriores quedan en Historial_Predicciones.

Si quieres ejecutar el recorrido completo de ML con nuevas facturas ya
registradas en BD_energia:

    uv run python cli.py actualizar-predicciones --completo --publicar

En ese orden: snapshot, competencia, reentrenamiento y selección automática,
sincronización de Predicciones_Cuenta y actualización de los gráficos.
Reevaluar y reentrenar es más costoso y crea archivos locales nuevos.

Si no deseas escribir en Sheets, omite --publicar para ver la predicción.
Si no especificas --anio/--mes, el periodo elegido es el mes que sigue al
último consumo del histórico; puede ser retrospectivo. Para febrero de 2026:

    uv run python cli.py actualizar-predicciones --anio 2026 --mes 2 --publicar

Al republicar Graficos_Predicciones se selecciona por defecto el modelo
VIGENTE de Predicciones_Cuenta, evitando que una elección antigua de F4 haga
parecer que no se publicaron las predicciones recientes. El usuario puede
volver a cambiar F4 después de refrescar.

El resumen lateral M2:N12 de Predicciones_Cuenta distingue la última
ejecución del modelo y la última verificación de Sheets, además de mostrar
el identificador de modelo y SHA-256 del snapshot. Si la sincronización
informa 0 actualizaciones y 0 inserciones, los valores eran idénticos:
NO es un fallo. La verificación se registra aunque no cambien las cifras.
