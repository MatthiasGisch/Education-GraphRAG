import os
import logging
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# LLM-Betriebsmodus: "cloud" (OpenAI) oder "local" (LM Studio)
LLM_MODE = os.getenv("LLM_MODE", "cloud")

# LM Studio Konfiguration
LMSTUDIO_BASE_URL   = os.getenv("LMSTUDIO_BASE_URL",   "http://localhost:1234/v1")
LMSTUDIO_CHAT_MODEL    = os.getenv("LMSTUDIO_CHAT_MODEL",    "local-model")
LMSTUDIO_VISION_MODEL  = os.getenv("LMSTUDIO_VISION_MODEL",  "local-vision-model")
LMSTUDIO_EMBED_MODEL   = os.getenv("LMSTUDIO_EMBED_MODEL",   "nomic-embed-text")
LMSTUDIO_EMBED_DIM     = int(os.getenv("LMSTUDIO_EMBED_DIM", "768"))

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

# Optional Gamma API (falls vorhanden)
GAMMA_API_KEY = os.getenv("GAMMA_API_KEY")
GAMMA_API_URL = os.getenv("GAMMA_API_URL")

# Synthesia (alternativer Video-Provider)
# Set SYNTHESIA_API_KEY and optionally SYNTHESIA_API_BASE in your .env
SYNTHESIA_API_KEY = os.getenv("SYNTHESIA_API_KEY")
SYNTHESIA_API_BASE = os.getenv("SYNTHESIA_API_BASE", "https://api.synthesia.io/v1")

DEFAULT_CHUNK_SIZE = int(os.getenv("DEFAULT_CHUNK_SIZE", "1200"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("DEFAULT_CHUNK_OVERLAP", "150"))

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
IMAGES_DIR = os.path.join(DATA_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)


NEO4J_VECTOR_DIM = 3072  # Dimension der Neo4j-Vektorindizes (graph_schema.cypher)

# Frühzeitige Warnung: lokaler Embedding-Dim muss mit Neo4j-Index übereinstimmen
if LLM_MODE == "local" and LMSTUDIO_EMBED_DIM != NEO4J_VECTOR_DIM:
    log.error(
        "KONFIGURATIONSFEHLER: LLM_MODE=local, aber LMSTUDIO_EMBED_DIM=%d "
        "stimmt nicht mit dem Neo4j-Vektorindex (%d Dimensionen) überein. "
        "Vektorsuchen werden fehlschlagen. "
        "Lösung: LMSTUDIO_EMBED_DIM=%d in .env setzen (oder ein Embedding-Modell "
        "mit %d Dimensionen in LM Studio laden) ODER das Datenbankschema mit dem "
        "neuen Dim-Wert neu anlegen (scripts/create_schema.py).",
        LMSTUDIO_EMBED_DIM, NEO4J_VECTOR_DIM, NEO4J_VECTOR_DIM, NEO4J_VECTOR_DIM,
    )


def resolve_image_path(uri: str) -> str:
    """
    Gibt den absoluten Pfad zu einer Bilddatei zurück.

    Strategie (in dieser Reihenfolge):
    1. Falls ``uri`` bereits ein existierender absoluter Pfad ist → direkt zurückgeben.
    2. Falls ``uri`` nur ein Dateiname ist → mit IMAGES_DIR kombinieren.
    3. Sonst → ``uri`` unverändert zurückgeben (Caller muss Fehler behandeln).

    Damit ist der Code portierbar: Wenn das Projekt umgezogen wird, reicht es,
    IMAGES_DIR in der .env anzupassen; bestehende absolute Pfade bleiben als
    Fallback erhalten.
    """
    if os.path.isabs(uri) and os.path.exists(uri):
        return uri
    candidate = os.path.join(IMAGES_DIR, os.path.basename(uri))
    if os.path.exists(candidate):
        return candidate
    return uri
