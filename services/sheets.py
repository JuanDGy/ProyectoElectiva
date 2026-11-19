from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from googleapiclient.discovery import build

from services.google_auth import get_credentials


def _norm_header(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.lower().strip()
    return re.sub(r"[^a-z0-9]+", "", value)


class SheetsService:
    def __init__(self, client_secret_file: str, token_file: str, spreadsheet_id: str):
        creds = get_credentials(client_secret_file, token_file)
        self.api = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.spreadsheet_id = spreadsheet_id

    def get_rows(self, sheet_name: str) -> list[dict]:
        result = self.api.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'",
        ).execute()
        values = result.get("values", [])
        if not values:
            return []

        headers = values[0]
        rows = []
        for raw in values[1:]:
            padded = list(raw) + [""] * (len(headers) - len(raw))
            rows.append(dict(zip(headers, padded)))
        return rows

    def sheet_names(self) -> list[str]:
        info = self.api.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id, fields="sheets(properties(title))"
        ).execute()
        return [item["properties"]["title"] for item in info.get("sheets", [])]

    def ensure_sheet(self, sheet_name: str) -> bool:
        """Crea la pestaña si no existe; devuelve True cuando la creó."""
        if sheet_name in self.sheet_names():
            return False
        self.api.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        ).execute()
        return True

    def ensure_headers(self, sheet_name: str, headers: list[str]):
        values = self.api.spreadsheets().values()
        first = values.get(spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!1:1").execute().get("values", [])
        if first:
            current = first[0]
            compatible = len(current) <= len(headers) and all(
                _norm_header(a) == _norm_header(b) for a, b in zip(current, headers)
            )
            if not compatible:
                # Older runs appended invoice rows to a sheet without headers.
                legacy_invoice = len(current) >= 12 and bool(re.fullmatch(r"[\w-]{20,}", str(current[0]))) and "drive.google.com" in str(current[10])
                if not legacy_invoice:
                    raise RuntimeError(f"Encabezados incompatibles en {sheet_name}; no se modificó la hoja")
                info = self.api.spreadsheets().get(spreadsheetId=self.spreadsheet_id, fields="sheets(properties(sheetId,title))").execute()
                sheet_id = next(s["properties"]["sheetId"] for s in info["sheets"] if s["properties"]["title"] == sheet_name)
                self.api.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": [{"insertDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1}}}]}).execute()
        values.update(spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A1", valueInputOption="RAW", body={"values": [headers]}).execute()

    def append(self, sheet_name: str, values: Iterable, headers: list[str] | None = None) -> dict:
        if headers:
            self.ensure_headers(sheet_name, headers)
        return self.api.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [list(values)]},
        ).execute()

    def sheet_chart_info(self, sheet_name: str) -> tuple[int, dict[str, int]]:
        """Identificador de pestaña y gráficos existentes indexados por título."""
        result = self.api.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets(properties(sheetId,title),charts(chartId,spec(title)))",
        ).execute()
        for item in result.get("sheets", []):
            if item["properties"]["title"] == sheet_name:
                return item["properties"]["sheetId"], {
                    chart["spec"]["title"]: chart["chartId"]
                    for chart in item.get("charts", [])
                    if chart.get("spec", {}).get("title")
                }
        raise KeyError(f"No existe la pestaña {sheet_name}")

    def replace_dashboard(self, sheet_name: str, rows: list[list], charts: list[dict],
                          *, account_data_rows: list[list] | None = None,
                          accounts: list[str] | None = None,
                          account_sheet_name: str = "Datos_Graficos_Cuenta") -> dict:
        """Sincroniza la pestaña reservada a gráficos nativos, sin duplicarlos.

        Solo se llama después de confirmación --write; NO usar en hojas del usuario.
        """
        self.ensure_sheet(sheet_name)
        sheet_id, existing = self.sheet_chart_info(sheet_name)
        self.api.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A:K", body={},
        ).execute()
        self.api.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id, range=f"'{sheet_name}'!A1",
            valueInputOption="RAW", body={"values": rows},
        ).execute()
        if account_data_rows is not None:
            if not accounts or sheet_name != "Graficos_Predicciones":
                raise ValueError("La selección de cuenta solo está permitida en Graficos_Predicciones")
            if account_sheet_name != "Datos_Graficos_Cuenta":
                raise ValueError("Nombre de hoja auxiliar no permitido")
            self.ensure_sheet(account_sheet_name)
            self.api.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{account_sheet_name}'!A:F", body={},
            ).execute()
            self.api.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{account_sheet_name}'!A1", valueInputOption="RAW",
                body={"values": account_data_rows},
            ).execute()
            # es_ES utiliza ; como separador entre argumentos.
            # SIN_DATO evita confundir meses sin factura con consumo cero.
            lookup = f"'{account_sheet_name}'!$A$2:$E$" + str(len(account_data_rows))
            formulas = []
            for row_index in range(6, 15):
                key = f'$B$4&"|"&$A{row_index}'
                def formula(column):
                    call = f"VLOOKUP({key};{lookup};{column};FALSE)"
                    return f'=IFERROR(IF({call}="SIN_DATO";"";{call});"")'
                formulas.append([formula(4), formula(5)])
            self.api.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!B6:C14", valueInputOption="USER_ENTERED",
                body={"values": formulas},
            ).execute()
        requests = [
            {"updateSheetProperties": {"properties": {
                "sheetId": sheet_id, "gridProperties": {"frozenRowCount": 4}},
                "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": sheet_id,
                                     "startRowIndex": 4, "endRowIndex": 5,
                                     "startColumnIndex": 0, "endColumnIndex": 10},
                            "cell": {"userEnteredFormat": {
                                "backgroundColor": {"red": 0.14, "green": 0.24, "blue": 0.36},
                                "textFormat": {"bold": True, "foregroundColor": {
                                    "red": 1, "green": 1, "blue": 1}}}},
                            "fields": "userEnteredFormat"}},
        ]
        for col in range(10):
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": col, "endIndex": col + 1},
                "properties": {"pixelSize": 175 if col in (0, 4, 7) else 155},
                "fields": "pixelSize"}})
        if account_data_rows is not None:
            requests.extend([
                {"repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 3,
                              "endRowIndex": 4, "startColumnIndex": 1,
                              "endColumnIndex": 2},
                    "cell": {"userEnteredFormat": {
                        "numberFormat": {"type": "TEXT"},
                        "backgroundColor": {"red": .86, "green": .94, "blue": 1},
                        "textFormat": {"bold": True, "fontSize": 12},
                    }},
                    "fields": "userEnteredFormat",
                }},
                {"setDataValidation": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 3,
                              "endRowIndex": 4, "startColumnIndex": 1,
                              "endColumnIndex": 2},
                    "rule": {
                        "condition": {"type": "ONE_OF_RANGE", "values": [{
                            "userEnteredValue":
                                f"='{account_sheet_name}'!$F$2:$F$" + str(len(accounts) + 1),
                        }]},
                        "showCustomUi": True, "strict": True,
                        "inputMessage": "Escoge una de las cuentas con predicción.",
                    },
                }},
            ])
        desired = {item["spec"]["title"] for item in charts}
        for title, chart_id in existing.items():
            if title.startswith("EcoData |") and title not in desired:
                requests.append({"deleteEmbeddedObject": {"objectId": chart_id}})
        for chart in charts:
            spec = chart["spec"]
            anchor = chart["anchor"]
            overlay = {"anchorCell": {"sheetId": sheet_id, "rowIndex": anchor,
                                      "columnIndex": 0},
                       "widthPixels": 1020, "heightPixels": 410,
                       "offsetXPixels": 0, "offsetYPixels": 0}
            if spec["title"] in existing:
                chart_id = existing[spec["title"]]
                requests.append({"updateChartSpec": {"chartId": chart_id, "spec": spec}})
                requests.append({"updateEmbeddedObjectPosition": {
                    "objectId": chart_id, "newPosition": {"overlayPosition": overlay},
                    "fields": "anchorCell,widthPixels,heightPixels,offsetXPixels,offsetYPixels",
                }})
            else:
                requests.append({"addChart": {"chart": {
                    "spec": spec, "position": {"overlayPosition": overlay},
                }}})
        self.api.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id, body={"requests": requests},
        ).execute()
        return {"sheet_id": sheet_id, "charts": len(charts), "rows": len(rows)}

    def replace_comparison(self, sheet_name: str, rows: list[list],
                           charts: list[dict], header_rows: list[int]) -> dict:
        """Publica una comparativa en SU pestaña administrada, con gráficos nativos.

        Los índices de header_rows son de base cero. No modifica ninguna
        pestaña de facturación, pronósticos ni dashboard existente.
        """
        if sheet_name not in {"Comparativa_OCR_LLM", "Comparativa_Modelos"}:
            raise ValueError("Solo se permiten las dos pestañas comparativas reservadas")
        if not rows or not all(isinstance(row, list) for row in rows):
            raise ValueError("Se requieren filas tabulares")
        self.ensure_sheet(sheet_name)
        sheet_id, existing = self.sheet_chart_info(sheet_name)
        # Solo reescribe esta pestaña y las columnas administradas A:P.
        self.api.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'!A:P", body={},
        ).execute()
        display_rows = []
        for i, row in enumerate(rows):
            display = list(row)
            section = bool(row) and str(row[0]).startswith((
                "GRÁFICOS |", "Detalle completo:", "Desglose por campo |",
                "Submuestra cotejada", "Candidatos registrados v1",
                "EXCLUSIONES |"))
            if display and (i < 4 or section):
                label = ("ECCI" if i == 0 else "Fuente" if i == 1 else
                         "Nota" if i == 2 else "Resumen" if i == 3 else "SECCIÓN")
                display = [label, str(display[0])]
            display_rows.append(display)
        self.api.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'!A1", valueInputOption="RAW",
            body={"values": display_rows},
        ).execute()

        # Formato por sección. Sin crear reglas/objetos nuevos duplicables.
        is_ocr = sheet_name == "Comparativa_OCR_LLM"
        widths = ((193, 115, 157, 178, 177, 215, 255, 115)
                  if is_ocr else
                  (222, 193, 107, 125, 130, 138, 124, 90, 85, 145,
                   174, 125, 245))
        requests = [
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id,
                               "gridProperties": {
                                   "frozenRowCount": 6,
                                   "frozenColumnCount": 1}},
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }},
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS",
                          "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 46},
                "fields": "pixelSize",
            }},
        ]

        def paint(start, end, column_start=0, column_end=13, **cell_format):
            requests.append({"repeatCell": {
                "range": {"sheetId": sheet_id,
                          "startRowIndex": start, "endRowIndex": end,
                          "startColumnIndex": column_start,
                          "endColumnIndex": column_end},
                "cell": {"userEnteredFormat": cell_format},
                "fields": "userEnteredFormat",
            }})

        navy = {"red": 0.11, "green": 0.21, "blue": 0.34}
        slate = {"red": 0.17, "green": 0.29, "blue": 0.43}
        pale = {"red": 0.94, "green": 0.96, "blue": 0.98}
        white = {"red": 1, "green": 1, "blue": 1}
        for col, width in enumerate(widths):
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": col, "endIndex": col + 1},
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }})
        paint(0, 1, backgroundColor=navy,
              textFormat={"bold": True, "fontSize": 18, "foregroundColor": white},
              verticalAlignment="MIDDLE")
        # Metadatos y advertencias: texto legible, sin falsa apariencia de KPI.
        for r in range(1, 4):
            paint(r, r+1, backgroundColor=pale,
                  textFormat={"fontSize": 10},
                  wrapStrategy="WRAP")

        for i, row in enumerate(rows):
            if not row:
                continue
            title = str(row[0]).strip()
            if title.startswith(("GRÁFICOS |", "Detalle completo:",
                                "Desglose por campo |",
                                "Submuestra cotejada",
                                "Candidatos registrados v1",
                                "EXCLUSIONES |")):
                paint(i, i+1, backgroundColor=slate,
                      textFormat={"bold": True, "foregroundColor": white,
                                  "fontSize": 11})
                requests.append({"updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "ROWS",
                              "startIndex": i, "endIndex": i+1},
                    "properties": {"pixelSize": 32}, "fields": "pixelSize",
                }})
        for index in sorted(set(header_rows)):
            paint(index, index+1, backgroundColor=navy,
                  textFormat={"bold": True, "foregroundColor": white,
                              "fontSize": 10},
                  wrapStrategy="WRAP", verticalAlignment="MIDDLE")
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS",
                          "startIndex": index, "endIndex": index+1},
                "properties": {"pixelSize": 50}, "fields": "pixelSize",
            }})
        # Ayudas visibles al pasar el cursor por cada encabezado.
        notes = {
            "Método": "Estrategia de extracción evaluada frente a la referencia documental.",
            "Facturas": "Cantidad de documentos procesados en el experimento.",
            "Filas completas": "Documentos en los que coincidieron los seis campos evaluados.",
            "Filas completas (%)": "Porcentaje de documentos con los seis campos coincidentes. Referencia preliminar con sesgo hacia OCR.",
            "Tiempo promedio (s/factura)": "Segundos promedio medidos por método en esta muestra; no es rendimiento garantizado.",
            "Errores/faltantes por campo": "Suma de discrepancias y ausencias en los seis campos de la muestra.",
            "Campo": "Campo de la factura cotejado con los valores de referencia.",
            "Evaluaciones": "Total de comparaciones válidas del campo.",
            "OCR (%)": "Porcentaje de aciertos del OCR en este campo.",
            "Gemini (%)": "Porcentaje de aciertos del LLM en este campo.",
            "Gemini + reglas (%)": "Porcentaje de aciertos del LLM más las reglas deterministas.",
            "Facturas manuales": "Documentos con una referencia cotejada a mano. La submuestra es pequeña.",
            "Modelo / referencia": "Familia del algoritmo o método de referencia.",
            "Selección": "Hiperparámetros de la configuración elegida por el MAE de validación, no por el resultado de test.",
            "Enfoque": "Level predice kWh, delta predice diferencia y ratio predice proporción.",
            "CV MAE (kWh)": "MAE medio de validación temporal: menor es mejor. Se usa para seleccionar la configuración.",
            "Test MAE (kWh)": "Error absoluto medio de la prueba fuera de entrenamiento: menor es mejor.",
            "Test RMSE (kWh)": "Raíz del error cuadrático medio. Penaliza los errores grandes: menor es mejor.",
            "Test WAPE (%)": "Suma de errores absolutos dividida por la suma de consumos reales. Menor es mejor.",
            "Test R²": "Coeficiente de determinación. Puede ser negativo; mayor suele ser mejor.",
            "N test": "Número de observaciones de prueba. La comparación principal usa 69.",
            "P(mejor persistencia)": "Frecuencia de mejora frente a persistencia en el bootstrap; NO es probabilidad calibrada de éxito futuro.",
            "Supera ref. con evidencia": "Sí únicamente si MAE de test es menor al de persistencia y p_better >= 0.95.",
            "Tipo": "Referencia o configuración elegida por validación.",
            "Reporte": "Archivo JSON de procedencia, para trazabilidad.",
            "Familia": "Familia del algoritmo. Cada fila del detalle representa una configuración.",
            "Estacionalidad": "Indica el uso de las variables estacionales del mes.",
            "Hiperparámetros": "Ajustes usados para entrenar la configuración, en formato JSON.",
            "Significativo": "Veredicto del criterio implementado frente a persistencia, no certeza absoluta.",
            "Fuente": "Reporte JSON original con las métricas.",
            "Estado": "Challenger significa candidato registrado, no modelo promovido.",
            "Motivo de exclusión": "La evaluación histórica no comparte N o persistencia con el test actual. No mezclar sus métricas.",
        }
        for idx in sorted(set(header_rows)):
            cell_notes = [{"note": notes.get(str(h), "")}
                          for h in rows[idx]]
            requests.append({"updateCells": {
                "start": {"sheetId": sheet_id, "rowIndex": idx, "columnIndex": 0},
                "rows": [{"values": cell_notes}], "fields": "note",
            }})
        # Diferencia suave de la referencia frente a los modelos.
        if not is_ocr:
            paint(6, 8, backgroundColor={
                "red": 1, "green": .97, "blue": .88},
                textFormat={"bold": True})
        # El gráfico se alimenta de valores numéricos, formateados a la vista.
        def numeric(col, begin, finish, pattern):
            if begin >= finish:
                return
            requests.append({"repeatCell": {
                "range": {"sheetId": sheet_id,
                          "startRowIndex": begin, "endRowIndex": finish,
                          "startColumnIndex": col, "endColumnIndex": col+1},
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat",
            }})
        if is_ocr:
            numeric(3, 6, 9, '0.00" %"')
            numeric(4, 6, 9, '0.00')
            for col in (2, 4, 6):
                numeric(col, 12, 18, '0.00" %"')
            numeric(2, 21, 24, '0.00" %"')
        else:
            for col in (3, 4, 5):
                numeric(col, 6, 21, '0.00')
            numeric(6, 6, 21, '0.00" %"')
            numeric(7, 6, 21, '0.0000')
            numeric(9, 8, 21, '0.00%')
            # Filtrar todas las configuraciones sin afectar el resumen.
            detail_header = next(
                i for i, row in enumerate(rows)
                if row and row[0] == "Familia" and len(row) > 6)
            detail_end = detail_header+1
            while detail_end < len(rows) and rows[detail_end]:
                detail_end += 1
            for col in (4, 5, 6):
                numeric(col, detail_header+1, detail_end, '0.00')
            numeric(7, detail_header+1, detail_end, '0.00" %"')
            numeric(8, detail_header+1, detail_end, '0.0000')
            numeric(10, detail_header+1, detail_end, '0.00%')
            requests.append({"setBasicFilter": {"filter": {"range": {
                "sheetId": sheet_id, "startRowIndex": detail_header,
                "endRowIndex": detail_end,
                "startColumnIndex": 0, "endColumnIndex": 13}}}})
        # Fusionar únicamente las filas narrativas (no encabezados ni datos).
        # Al consultar las fusiones existentes, republicar no duplica reglas.
        merge_info = self.api.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            ranges=[sheet_name],
            fields="sheets(properties(sheetId),merges)",
        ).execute()
        matches = [it for it in merge_info.get("sheets", [])
                   if it.get("properties", {}).get("sheetId") == sheet_id]
        merged = matches[0].get("merges", []) if matches else []
        narrative_rows = [0, 1, 2, 3]
        narrative_rows += [i for i, row in enumerate(rows) if row and
                           str(row[0]).startswith((
                               "GRÁFICOS |", "Detalle completo:",
                               "Desglose por campo |", "Submuestra cotejada",
                               "Candidatos registrados v1", "EXCLUSIONES |"))]
        for row_idx in sorted(set(narrative_rows)):
            rng = {"sheetId": sheet_id, "startRowIndex": row_idx,
                   "endRowIndex": row_idx + 1, "startColumnIndex": 1,
                   "endColumnIndex": 13}
            if any(m.get("startRowIndex") == row_idx and
                   m.get("endRowIndex") == row_idx + 1 and
                   m.get("startColumnIndex", 0) <= 1 and
                   m.get("endColumnIndex", 13) >= 13 for m in merged):
                continue
            if any(m.get("startRowIndex", 0) <= row_idx <
                   m.get("endRowIndex", 0) for m in merged):
                continue  # No interferir con una fusión manual distinta.
            requests.append({"mergeCells": {"range": rng,
                                            "mergeType": "MERGE_ALL"}})
        desired = {x["spec"]["title"] for x in charts}
        for title, chart_id in existing.items():
            if title.startswith("EcoData | Comparativa ") and title not in desired:
                requests.append({"deleteEmbeddedObject": {"objectId": chart_id}})
        for chart in charts:
            spec = chart["spec"]
            overlay = {
                "anchorCell": {"sheetId": sheet_id,
                               "rowIndex": chart["anchor_row"],
                               "columnIndex": 0},
                "widthPixels": 1040,
                "heightPixels": chart.get("height", 450),
                "offsetXPixels": 0, "offsetYPixels": 0,
            }
            if spec["title"] in existing:
                chart_id = existing[spec["title"]]
                requests.append({"updateChartSpec": {
                    "chartId": chart_id, "spec": spec,
                }})
                requests.append({"updateEmbeddedObjectPosition": {
                    "objectId": chart_id,
                    "newPosition": {"overlayPosition": overlay},
                    "fields": "anchorCell,widthPixels,heightPixels,offsetXPixels,offsetYPixels",
                }})
            else:
                requests.append({"addChart": {"chart": {
                    "spec": spec, "position": {"overlayPosition": overlay},
                }}})
        self.api.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id, body={"requests": requests},
        ).execute()
        return {"sheet_id": sheet_id, "rows": len(rows), "charts": len(charts)}

    def append_rows(self, sheet_name: str, rows: Iterable[Iterable], headers: list[str] | None = None) -> dict:
        """Inserta varias filas en una sola llamada; no escribe si no hay filas."""
        values = [list(row) for row in rows]
        if not values:
            return {}
        if headers:
            self.ensure_headers(sheet_name, headers)
        return self.api.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()

