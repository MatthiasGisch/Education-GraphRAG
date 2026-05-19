"""CLI-Skript: stellt eine Frage ans GraphRAG-System und gibt die Antwort auf der Konsole aus."""
from pathlib import Path
import sys, json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.neo import Neo4jClient
from src.agent import answer_query

def main():
    """Liest eine Frage aus den Kommandozeilenargumenten und gibt die belegte Antwort aus."""
    if len(sys.argv) < 2:
        print('Usage: python scripts/ask.py "Deine Frage hier"')
        sys.exit(1)
    query = sys.argv[1]
    neo = Neo4jClient()
    res = answer_query(query, neo)
    neo.close()
    print("=== MODE:", res["mode"], "===")
    print(res["answer"])
    print("\n-- Belege (detail) --")
    provs = []
    for s in res["supports"]:
        item = {
            "id": f"{'P' if s['type']=='paragraph' else 'F'}{s.get('paragraph_id') or s.get('figure_id')}",
            "type": s["type"],
            "paper_title": s.get("paper_title"),
            "doi": s.get("doi"),
            "url": s.get("url"),
            "page": s.get("page"),
            "section_title": s.get("section_title"),
            "bbox": s.get("bbox"),
            "page_width": s.get("page_width"),
            "page_height": s.get("page_height"),
            "order_in_page": s.get("order_in_page"),
        }
        provs.append(item)
    print(json.dumps(provs, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
