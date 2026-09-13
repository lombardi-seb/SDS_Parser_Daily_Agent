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

# --- EUH statements (EU CLP, Annexes II/III) ---
EUH_TSV_PATH = os.environ.get("EUH_TSV_PATH", "euh_codes_clp.txt")
# "off"   : no detection (pre-EUH behaviour)
# "audit" : detect + log only, labelCodes unchanged  <-- default
# "on"    : detect + add to labelCodes
EUH_MODE = os.environ.get("EUH_MODE", "audit").strip().lower()

# --- API Endpoints ---
LOGIN_URL             = os.environ["LOGIN_URL"]
LOGOUT_URL            = os.environ["LOGOUT_URL"]
DELETE_URL            = os.environ["DELETE_URL"]
UPLOAD_URL            = os.environ["UPLOAD_URL"]
UPLOAD_ADDITIONAL_URL = os.environ["UPLOAD_ADDITIONAL_URL"]

# --- Tesseract Path ---
# Customize with your configuration
TESSERACT_PATH = os.environ["TESSERACT_PATH"]
 
# --- Ollama Config (LLM interne) ---
# URL base Ollama internal server (without required final slash).
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# Context length : default Ollama (2048) is too short.
OLLAMA_NUM_CTX  = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
# Max Context length
OLLAMA_MAX_NUM_CTX  = int(os.environ.get("OLLAMA_MAX_NUM_CTX", "32768"))
# CPU-only : slow inference.
OLLAMA_TIMEOUT  = int(os.environ.get("OLLAMA_TIMEOUT", "900"))
# Number of CPU threads. 0 = let Ollama decide (recommended default).
# NB: num_thread is configured only by request (or via a Modelfile)
# See README.
OLLAMA_NUM_THREAD = int(os.environ.get("OLLAMA_NUM_THREAD", "0"))

# --- OpenRouter Config : deprecated ---
#OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
