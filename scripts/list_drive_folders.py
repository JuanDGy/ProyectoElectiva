# scripts/list_drive_folders.py

from app.config import get_settings
from services.drive import DriveService

settings = get_settings()

drive = DriveService(
    settings.google_client_secret_file,
    settings.google_token_file,
)

result = drive.api.files().list(
    q=(
        "mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    ),
    pageSize=1000,
    fields="files(id,name,parents)",
    orderBy="name",
).execute()

folders = result.get("files", [])

for folder in folders:
    print("=" * 70)
    print("Nombre :", folder["name"])
    print("ID     :", folder["id"])
    print("Parents:", folder.get("parents", []))
