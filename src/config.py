import os
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

# Optional Gamma API (falls vorhanden)
GAMMA_API_KEY = os.getenv("GAMMA_API_KEY")
GAMMA_API_URL = os.getenv("GAMMA_API_URL")

# D-ID (talking head / video generation) settings
# Set DID_API_KEY and DID_API_URL in your .env to enable
DID_API_KEY = os.getenv("DID_API_KEY")
DID_API_URL = os.getenv("DID_API_URL", "https://api.d-id.com/talks")

DEFAULT_CHUNK_SIZE = int(os.getenv("DEFAULT_CHUNK_SIZE", "1200"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("DEFAULT_CHUNK_OVERLAP", "150"))

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
IMAGES_DIR = os.path.join(DATA_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)
