"""CLI-Skript: stellt eine Frage ans GraphRAG-System und speichert die Antwort als PDF."""
from __future__ import annotations
import sys, os, re, time, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # Ordner, der src/ und scripts/ enthält
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.neo import Neo4jClient
from src.agent import answer_query
from src.pdf_export import write_answer_pdf

def slugify(s: str) -> str:
    """Normalisiert einen String zu einem URL-sicheren Slug für Dateinamen."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:60] or "answer"

def main():
    """Beantwortet eine Frage und exportiert die Antwort mit Belegen als PDF in exports/."""
    if len(sys.argv) < 2:
        print('Usage: python -m scripts.ask_to_pdf "Deine Frage hier"')
        sys.exit(1)
    query = sys.argv[1]
    neo = Neo4jClient()
    res = answer_query(query, neo)
    neo.close()

    # PDF-Dateiname
    base = slugify(query)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path("exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{base}_{ts}.pdf"

    # Inline-Bilder aktiv, kein globales Limit; Fallback: Top-3, falls keine [F…] im Text
    path = write_answer_pdf(
        query, res["answer"], res["supports"], str(out_path),
        inline_figures=True,
        max_inline_figures_total=None,
        fallback_append_top_k_if_no_refs=3
    )
    print("=== MODE:", res["mode"], "===")
    print("Antwort gespeichert als:", path)
    print("\n-- Quellen-IDs --")
    for s in res["supports"]:
        if s["type"] == "paragraph":
            print(f"[P{s['paragraph_id']}] {s.get('paper_title')} (S.{s.get('page')})")
        else:
            print(f"[F{s['figure_id']}] {s.get('paper_title')} (S.{s.get('page')}) -> {s.get('image_uri')}")
    with open(out_dir / f"{base}_{ts}.supports.json", "w", encoding="utf-8") as f:
        json.dump(res["supports"], f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()