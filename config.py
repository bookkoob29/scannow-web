"""Configuration for SCANNOW Web App."""
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "scannow.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8080/auth/callback")
ALLOWED_EMAIL = "sorlakom.thana@gmail.com,Janney.jee@gmail.com"
SESSION_SECRET = os.environ.get("SESSION_SECRET", "scannow-web-secret")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "")
SCAN_SCRIPT = os.path.expanduser("~/.hermes/scripts/scannow_cron.py") if not USE_POSTGRES else ""
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
TELEGRAM_CHAT_ID = "8969930460"
TELEGRAM_SENDER = os.path.expanduser("~/.hermes/scripts/telegram_sender.py")
