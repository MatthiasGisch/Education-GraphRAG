#!/usr/bin/env python3
"""
Repariert fehlende paper_id Properties UND HAS_PARAGRAPH Beziehungen bei Paragraphs.
Nutzt die paragraph_id Namenskonvention (enthält paper_id als Präfix).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.neo import Neo4jClient

def main():
    neo = Neo4jClient()
    
    print("=== Repariere Paragraph-Paper Verknüpfungen ===\n")
    
    # Get all papers
    papers = neo.run("MATCH (p:Paper) RETURN p.paper_id AS paper_id")
    
    if not papers:
        print("❌ Keine Papers gefunden!")
        return
    
    print(f"Gefundene Papers: {len(papers)}")
    for p in papers:
        print(f"  - {p['paper_id']}")
    
    print("\n" + "="*80)
    
    # For each paper, find orphan paragraphs and connect them
    total_fixed = 0
    for paper in papers:
        paper_id = paper['paper_id']
        
        # Find paragraphs that belong to this paper but aren't connected
        # Strategy: paragraph_id often contains paper_id, OR use text matching
        print(f"\nPrüfe Paper: {paper_id[:40]}...")
        
        # First: Try to find paragraphs by paper_id prefix in paragraph_id
        result = neo.run("""
            MATCH (p:Paper {paper_id: $paper_id})
            MATCH (para:Paragraph)
            WHERE para.paragraph_id STARTS WITH $paper_id
              AND NOT (p)-[:HAS_PARAGRAPH]->(para)
            WITH p, para
            MERGE (p)-[:HAS_PARAGRAPH]->(para)
            SET para.paper_id = $paper_id
            RETURN count(para) AS fixed_count
        """, {"paper_id": paper_id})
        
        fixed_count = result[0]['fixed_count'] if result else 0
        
        if fixed_count > 0:
            print(f"  ✅ {fixed_count} Paragraphs verknüpft (via paragraph_id)")
            total_fixed += fixed_count
    
    print("\n" + "="*80)
    print(f"\n✅ Insgesamt {total_fixed} Paragraphs repariert!")
    
    # Verify
    print("\n=== Verifizierung ===")
    result = neo.run("""
        MATCH (p:Paper)
        OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        RETURN p.title AS title, p.paper_id AS paper_id, count(para) AS para_count
        ORDER BY title
    """)
    
    for row in result:
        print(f"  📄 {row['title']}")
        print(f"     Paper ID: {row['paper_id']}")
        print(f"     Paragraphs: {row['para_count']}")
    
    # Check remaining orphans
    orphans = neo.run("""
        MATCH (para:Paragraph)
        WHERE NOT (para)<-[:HAS_PARAGRAPH]-(:Paper)
        RETURN count(para) AS count
    """)
    orphan_count = orphans[0]['count'] if orphans else 0
    
    if orphan_count > 0:
        print(f"\n⚠️  Noch {orphan_count} verwaiste Paragraphs")
        print("   Diese könnten nicht automatisch zugeordnet werden.")
    else:
        print("\n✅ Alle Paragraphs sind jetzt mit Papers verknüpft!")
        print("   'Konzepte aus bestehenden Papern extrahieren' sollte jetzt funktionieren.")

if __name__ == "__main__":
    main()
