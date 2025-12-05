import argparse
import csv
from pathlib import Path
from typing import Dict, Any, Optional

from src.neo import Neo4jClient

FIELDS = [
    "author",
    "publication_year",
    "doi",
    "url",
    "source",
    "publisher",
]

def fetch_paper(neo: Neo4jClient, paper_id: Optional[str], title: Optional[str]) -> Optional[Dict[str, Any]]:
    if paper_id:
        res = neo.run(
            """
            MATCH (p:Paper {paper_id:$pid})
            RETURN p.paper_id AS paper_id, p.title AS title,
                   p.author AS author, p.publication_year AS publication_year,
                   p.doi AS doi, p.url AS url, p.source AS source, p.publisher AS publisher
            """,
            {"pid": paper_id},
        )
        if res:
            return res[0]
    if title:
        res = neo.run(
            """
            MATCH (p:Paper)
            WHERE p.title = $title
            RETURN p.paper_id AS paper_id, p.title AS title,
                   p.author AS author, p.publication_year AS publication_year,
                   p.doi AS doi, p.url AS url, p.source AS source, p.publisher AS publisher
            LIMIT 1
            """,
            {"title": title},
        )
        if res:
            return res[0]
    return None

def update_paper(neo: Neo4jClient, pid: str, updates: Dict[str, Any]) -> None:
    if not updates:
        return
    set_parts = []
    params = {"pid": pid}
    for field, value in updates.items():
        params[field] = value
        set_parts.append(f"p.{field} = ${field}")
    cypher = "MATCH (p:Paper {paper_id:$pid}) SET " + ", ".join(set_parts)
    neo.run(cypher, params)

def main():
    parser = argparse.ArgumentParser(description="Update missing Paper metadata from CSV")
    parser.add_argument("csv", type=Path, help="CSV with columns: paper_id,title,author,publication_year,doi,url,source,publisher")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would change")
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    neo = Neo4jClient()

    with args.csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            paper_id = (row.get("paper_id") or "").strip() or None
            title = (row.get("title") or "").strip() or None
            if not paper_id and not title:
                print("Skip row without paper_id/title")
                continue

            existing = fetch_paper(neo, paper_id, title)
            if not existing:
                print(f"Not found: pid={paper_id or '-'} title={title or '-'}")
                continue

            pid = existing["paper_id"]
            to_update: Dict[str, Any] = {}
            for field in FIELDS:
                new_val = (row.get(field) or "").strip()
                cur_val = existing.get(field)
                if new_val and (cur_val is None or str(cur_val).strip() == ""):
                    to_update[field] = new_val

            print(f"Paper {pid}: {existing.get('title')}")
            print(f"  Current: {{" + ", ".join([f"{k}={existing.get(k) or ''}" for k in FIELDS]) + "}}")
            if to_update:
                print(f"  Will set: {{" + ", ".join([f"{k}={v}" for k,v in to_update.items()]) + "}}")
                if not args.dry_run:
                    update_paper(neo, pid, to_update)
            else:
                print("  Nothing to update (all present)")

if __name__ == "__main__":
    main()
