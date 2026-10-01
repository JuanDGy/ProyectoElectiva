import os
from pathlib import Path
import sys

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.google_auth import get_credentials


if __name__ == "__main__":
    load_dotenv(PROJECT_ROOT / ".env")
    client_secret_file = PROJECT_ROOT / os.getenv("GOOGLE_CLIENT_SECRET_FILE", "credentials.json")
    token_file = PROJECT_ROOT / os.getenv("GOOGLE_TOKEN_FILE", "token.json")
    creds = get_credentials(
        str(client_secret_file),
        str(token_file),
    )
    print("OAuth OK")
    print("Token válido:", bool(creds and creds.valid))
    print("Token guardado en:", token_file)
