from app.config import get_settings
from services.drive import DriveService
from services.sheets import SheetsService


def main():
    s = get_settings()

    drive = DriveService(s.google_client_secret_file, s.google_token_file)
    sheets = SheetsService(
        s.google_client_secret_file,
        s.google_token_file,
        s.spreadsheet_id,
    )

    print("Google Drive:")
    print("  01A:", drive.get_metadata(s.drive_01a)["name"])
    print("  01B:", drive.get_metadata(s.drive_01b)["name"])
    print("  01C:", drive.get_metadata(s.drive_01c)["name"])
    print("  01D:", drive.get_metadata(s.drive_01d)["name"])
    print("  02 :", drive.get_metadata(s.drive_02_energy)["name"])


    print("Google Sheets:")
    print("  Registros de energía:", len(sheets.get_rows(s.sheet_bd_energy)))
    print("Setup OK")


if __name__ == "__main__":
    main()
