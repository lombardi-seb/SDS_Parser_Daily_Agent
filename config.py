import os
from dotenv import load_dotenv

load_dotenv()

# --- Oracle Connection ---
DB_USERNAME = os.environ["DB_USERNAME"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_HOST     = os.environ["DB_HOST"]
DB_PORT     = os.environ.get("DB_PORT", "1521")
DB_SERVICE  = os.environ.get("DB_SERVICE", "BIOVIA")

# --- SDS Dictionary ---
TSV_PATH = os.environ.get("TSV_PATH", "ghscode_10.txt")

# --- API Endpoints ---
LOGIN_URL             = os.environ["LOGIN_URL"]
LOGOUT_URL            = os.environ["LOGOUT_URL"]
DELETE_URL            = os.environ["DELETE_URL"]
UPLOAD_URL            = os.environ["UPLOAD_URL"]
UPLOAD_ADDITIONAL_URL = os.environ["UPLOAD_ADDITIONAL_URL"]

# --- Tesseract Path ---
# Customize with your configuration
TESSERACT_PATH = os.environ["TESSERACT_PATH"]

# --- OpenRouter Config ---
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
