#!/usr/bin/env python3
"""Prüft die Vollständigkeit der Paper-Paragraph-Verknüpfungen in der Neo4j-Datenbank."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.neo import Neo4jClient

def main():
    """Gibt Statistiken über Paper-Paragraph-Verknüpfungen und verwaiste Paragraphen aus."""
    neo = Neo4jClient()
    
    print("=== Paper-Paragraph Verknüpfungen ===\n")
    
    # Check Paper-Paragraph relationships
    result = neo.run("""
        MATCH (p:Paper)
        OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        RETURN p.paper_id AS paper_id, p.title AS title, count(para) AS para_count
        ORDER BY title
    """)
    
    if result:
        for row in result:
            print(f"📄 {row['title']}")
            print(f"   Paper ID: {row['paper_id']}")
            print(f"   Paragraphs: {row['para_count']}")
            print()
    
    # Check total paragraphs
    total_paras = neo.run("MATCH (para:Paragraph) RETURN count(para) AS count")
    total_count = total_paras[0]['count'] if total_paras else 0
    print(f"Gesamt Paragraphs in DB: {total_count}")
    
    # Check paragraphs without paper
    orphan_paras = neo.run("""
        MATCH (para:Paragraph)
        WHERE NOT (para)<-[:HAS_PARAGRAPH]-(:Paper)
        RETURN count(para) AS count
    """)
    orphan_count = orphan_paras[0]['count'] if orphan_paras else 0
    print(f"Paragraphs OHNE Paper-Verknüpfung: {orphan_count}")
    
    if orphan_count > 0:
        print("\n⚠️ Problem: Paragraphs sind nicht mit Papers verknüpft!")
        print("Dies passiert wenn paper_id nicht korrekt gesetzt wurde beim Ingest.")
        
        # Show sample orphan paragraphs
        samples = neo.run("""
            MATCH (para:Paragraph)
            WHERE NOT (para)<-[:HAS_PARAGRAPH]-(:Paper)
            RETURN para.paragraph_id, para.paper_id, para.text
            LIMIT 3
        """)
        
        print("\nBeispiele (erste 3 verwaiste Paragraphs):")
        for s in samples:
            print(f"  - Paragraph ID: {s['para.paragraph_id']}")
            print(f"    paper_id Property: {s.get('para.paper_id', 'NICHT GESETZT')}")
            print(f"    Text: {s['para.text'][:100]}...")
            print()

if __name__ == "__main__":
    main()
