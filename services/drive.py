from __future__ import annotations

from io import BytesIO
from collections import deque
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from services.google_auth import get_credentials


FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveService:
    def __init__(self, client_secret_file: str, token_file: str):
        creds = get_credentials(client_secret_file, token_file)
        self.api = build("drive", "v3", credentials=creds, cache_discovery=False)

    def list_one_pdf(self, folder_id: str) -> Optional[dict]:
        """Busca un PDF en la entrada y sus subcarpetas, sin seguir accesos directos."""
        pending = deque([folder_id])
        visited = set()
        while pending:
            current = pending.popleft()
            if current in visited:
                continue
            visited.add(current)
            invoice = self._list_direct_pdf(current)
            if invoice:
                return invoice
            page_token = None
            while True:
                result = self.api.files().list(
                    q=f"'{current}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                    pageSize=1000,
                    pageToken=page_token,
                    orderBy="name",
                    fields="nextPageToken,files(id,name)",
                ).execute()
                pending.extend(folder["id"] for folder in result.get("files", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
        return None

    def iter_pdfs(self, folder_id: str):
        """Recorre el árbol completo una sola vez y produce cada PDF del histórico.

        Pagina, no sigue accesos directos y devuelve metadatos sin descargar. El
        orden es determinista (carpeta, nombre, fecha) para poder reanudar.
        """
        pending = deque([folder_id])
        visited = set()
        while pending:
            current = pending.popleft()
            if current in visited:
                continue
            visited.add(current)
            page_token = None
            while True:
                result = self.api.files().list(
                    q=f"'{current}' in parents and trashed=false",
                    pageSize=1000,
                    pageToken=page_token,
                    orderBy="folder,name,createdTime",
                    fields="nextPageToken,files(id,name,mimeType,parents,createdTime)",
                ).execute()
                for item in result.get("files", []):
                    if item.get("mimeType") == FOLDER_MIME:
                        pending.append(item["id"])
                    elif item.get("mimeType") == "application/pdf":
                        yield item
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

    def _list_direct_pdf(self, folder_id: str) -> Optional[dict]:
        q = (
            f"'{folder_id}' in parents "
            "and mimeType='application/pdf' "
            "and trashed=false"
        )
        result = self.api.files().list(
            q=q,
            pageSize=1,
            orderBy="createdTime asc",
            fields="files(id,name,webViewLink,parents)",
        ).execute()
        files = result.get("files", [])
        return files[0] if files else None

    def remember_source_office(self, metadata: dict, office: str) -> None:
        properties = dict(metadata.get("appProperties", {}))
        properties["sourceOffice"] = office
        self.api.files().update(
            fileId=metadata["id"],
            body={"appProperties": properties},
            fields="id,appProperties",
        ).execute()

    def get_metadata(self, file_id: str) -> dict:
        return self.api.files().get(
            fileId=file_id,
            fields="id,name,webViewLink,parents,mimeType,appProperties",
        ).execute()

    def download(self, file_id: str) -> bytes:
        request = self.api.files().get_media(fileId=file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return fh.getvalue()

    def move(self, file_id: str, destination_folder_id: str) -> dict:
        meta = self.get_metadata(file_id)
        current_parents = ",".join(meta.get("parents", []))
        return self.api.files().update(
            fileId=file_id,
            addParents=destination_folder_id,
            removeParents=current_parents or None,
            fields="id,name,webViewLink,parents",
        ).execute()

    def rename(self, file_id: str, new_name: str) -> dict:
        return self.api.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id,name,webViewLink",
        ).execute()

    def copy(
        self,
        file_id: str,
        new_name: str,
        destination_folder_id: str,
        source_office: str = "",
    ) -> dict:
        return self.api.files().copy(
            fileId=file_id,
            body={
                "name": new_name,
                "parents": [destination_folder_id],
                "appProperties": {"sourceOffice": source_office, "sourceFileId": file_id},
            },
            fields="id,name,webViewLink,parents",
        ).execute()

    def source_office(self, metadata: dict, root_id: str) -> str:
        stored = metadata.get("appProperties", {}).get("sourceOffice")
        if stored:
            return stored
        if not root_id:
            return ""
        parents = metadata.get("parents", [])
        parts = []
        visited = set()
        while len(parents) == 1:
            parent = parents[0]
            if parent == root_id:
                parts.reverse()
                parts = [name for i, name in enumerate(parts) if i == 0 or name != parts[i - 1]]
                return " / ".join(parts)
            if parent in visited:
                break
            visited.add(parent)
            folder = self.get_metadata(parent)
            parts.append(folder["name"].strip())
            parents = folder.get("parents", [])
        return ""

    def find_folder(self, parent_folder_id: str, name: str) -> Optional[dict]:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        q = (
            f"'{parent_folder_id}' in parents "
            "and mimeType='application/vnd.google-apps.folder' "
            f"and name='{escaped}' "
            "and trashed=false"
        )
        result = self.api.files().list(
            q=q,
            pageSize=1,
            fields="files(id,name,webViewLink)",
        ).execute()
        items = result.get("files", [])
        return items[0] if items else None

    def create_folder(self, parent_folder_id: str, name: str) -> dict:
        return self.api.files().create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_folder_id],
            },
            fields="id,name,webViewLink",
        ).execute()

    def ensure_folder(self, parent_folder_id: str, name: str) -> dict:
        folder = self.find_folder(parent_folder_id, name)
        return folder or self.create_folder(parent_folder_id, name)
