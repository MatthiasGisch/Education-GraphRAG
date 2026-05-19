"""Liest graph_schema.cypher und legt das Neo4j-Datenbankschema an (Indizes, Constraints)."""
from pathlib import Path
import sys

# Projekt-Root (Ordner, der 'src' und 'scripts' enthält) ins sys.path aufnehmen
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.neo import Neo4jClient  # jetzt auffindbar

def main():
    """Liest graph_schema.cypher und wendet das Schema via ensure_schema auf die Datenbank an."""
    schema_path = ROOT / "src" / "graph_schema.cypher"
    cypher = schema_path.read_text(encoding="utf-8")
    neo = Neo4jClient()
    neo.ensure_schema(cypher)
    neo.close()
    print("Schema created / verified.")

if __name__ == "__main__":
    main()
