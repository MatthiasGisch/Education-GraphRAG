#!/usr/bin/env python3
"""Diagnose-Skript: prüft den Datenbestand und die Graph-Struktur der Neo4j-Datenbank."""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.neo import Neo4jClient

def print_section(title: str):
    """Gibt einen formatierten Abschnittsheader auf der Konsole aus."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")

def main():
    """Verbindet sich mit Neo4j und gibt einen vollständigen Diagnosebericht aus."""
    neo = Neo4jClient()

    # 1. Alle Knoten-Typen zählen
    print_section("1. ALLE KNOTEN-TYPEN ZÄHLEN")
    result = neo.run("""
        MATCH (n)
        RETURN labels(n) AS NodeType, count(n) AS Count
        ORDER BY Count DESC
    """)
    for row in result:
        print(f"  {row['NodeType']}: {row['Count']}")
    
    # 2. Topics und ihre Konzepte
    print_section("2. TOPICS UND IHRE KONZEPTE")
    result = neo.run("""
        MATCH (t:Topic)
        OPTIONAL MATCH (t)-[:HAS_CONCEPT]->(c:Concept)
        RETURN t.name AS Topic, count(c) AS ConceptCount
    """)
    if result:
        for row in result:
            print(f"  Topic: {row['Topic']} → {row['ConceptCount']} Konzepte")
    else:
        print("  ⚠️  KEINE TOPICS GEFUNDEN!")
    
    # 3. Papers und ihre Komponenten
    print_section("3. PAPERS UND IHRE KOMPONENTEN")
    result = neo.run("""
        MATCH (p:Paper)
        OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        OPTIONAL MATCH (p)-[:HAS_FIGURE]->(fig:Figure)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
        RETURN p.title AS Paper, 
               count(DISTINCT para) AS Paragraphs,
               count(DISTINCT fig) AS Figures,
               count(DISTINCT sec) AS Sections
        LIMIT 10
    """)
    if result:
        for row in result:
            print(f"  📄 {row['Paper']}")
            print(f"     └─ Paragraphs: {row['Paragraphs']}, Figures: {row['Figures']}, Sections: {row['Sections']}")
    else:
        print("  ⚠️  KEINE PAPERS GEFUNDEN!")
    
    # 4. Konzept-Paragraph Verknüpfungen prüfen
    print_section("4. KONZEPT-PARAGRAPH VERKNÜPFUNGEN (MENTIONS)")
    result = neo.run("""
        MATCH (para:Paragraph)-[:MENTIONS]->(c:Concept)
        RETURN count(*) AS MentionsCount
    """)
    if result:
        mentions_count = result[0]['MentionsCount']
        if mentions_count > 0:
            print(f"  ✅ {mentions_count} MENTIONS-Verknüpfungen gefunden")
        else:
            print(f"  ⚠️  KEINE MENTIONS-Verknüpfungen gefunden!")
            print(f"  → Konzepte wurden beim Ingest nicht extrahiert/verknüpft")
            print(f"  → Nutze 'Konzepte aus bestehenden Papern extrahieren' in der GUI")
    
    # 5. Semantische Relationen prüfen (Hybrid-Modus)
    print_section("5. SEMANTISCHE RELATIONEN (HYBRID-MODUS)")
    result = neo.run("""
        MATCH (c1:Concept)-[r:SEMANTIC_RELATION]->(c2:Concept)
        RETURN c1.name AS From, r.relation_type AS RelationType, c2.name AS To, r.confidence AS Confidence
        LIMIT 20
    """)
    if result and len(result) > 0:
        print(f"  ✅ {len(result)} Semantische Relationen gefunden (erste 20):\n")
        for row in result:
            print(f"  {row['From']} --[{row['RelationType']}]--> {row['To']} (conf: {row['Confidence']:.2f})")
    else:
        print("  ℹ️  Keine semantischen Relationen gefunden")
        print("  → Normal für LLM-only Modus")
        print("  → Für Relationen: 'Hybrid (NER + LLM + Relationen)' Modus nutzen")
    
    # 6. Ko-Okkurrenz Relationen prüfen
    print_section("6. KO-OKKURRENZ RELATIONEN")
    result = neo.run("""
        MATCH (c1:Concept)-[r:CO_OCCURS_WITH]->(c2:Concept)
        RETURN c1.name AS Concept1, c2.name AS Concept2, r.count AS Count, r.strength AS Strength
        ORDER BY r.strength DESC
        LIMIT 10
    """)
    if result and len(result) > 0:
        print(f"  ✅ {len(result)} Ko-Okkurrenz Relationen gefunden (Top 10):\n")
        for row in result:
            print(f"  {row['Concept1']} <--> {row['Concept2']} (count: {row['Count']}, strength: {row['Strength']:.2f})")
    else:
        print("  ℹ️  Keine Ko-Okkurrenz Relationen gefunden")
    
    # 7. Zusammenfassung und Empfehlungen
    print_section("ZUSAMMENFASSUNG & EMPFEHLUNGEN")
    
    # Check if we have the basic structure
    node_counts = neo.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count")
    node_dict = {row['label']: row['count'] for row in node_counts}
    
    has_papers = node_dict.get('Paper', 0) > 0
    has_paragraphs = node_dict.get('Paragraph', 0) > 0
    has_concepts = node_dict.get('Concept', 0) > 0
    has_topics = node_dict.get('Topic', 0) > 0
    
    mentions_result = neo.run("MATCH (para:Paragraph)-[:MENTIONS]->(c:Concept) RETURN count(*) AS count")
    has_mentions = mentions_result[0]['count'] > 0 if mentions_result else False
    
    print("  Status:")
    print(f"  {'✅' if has_papers else '❌'} Papers vorhanden: {node_dict.get('Paper', 0)}")
    print(f"  {'✅' if has_paragraphs else '❌'} Paragraphs vorhanden: {node_dict.get('Paragraph', 0)}")
    print(f"  {'✅' if has_concepts else '❌'} Concepts vorhanden: {node_dict.get('Concept', 0)}")
    print(f"  {'✅' if has_topics else '❌'} Topics vorhanden: {node_dict.get('Topic', 0)}")
    print(f"  {'✅' if has_mentions else '❌'} MENTIONS Verknüpfungen vorhanden")
    
    print("\n  Empfehlungen:")
    if not has_papers:
        print("  ⚠️  Keine Papers gefunden → Führe PDF Ingest aus")
    elif not has_concepts or not has_mentions:
        print("  ⚠️  Keine Konzepte/Verknüpfungen → Nutze den Button:")
        print("     'Konzepte aus bestehenden Papern extrahieren' in der GUI")
        print("     (Tab: Konzepte → Nachträgliche Verarbeitung)")
    elif not has_topics:
        print("  ⚠️  Keine Topics → Wird beim Konzept-Extrakt erstellt")
    else:
        print("  ✅ Datenbank sieht gut aus!")
        print("  → Graph-Queries sollten jetzt Ergebnisse liefern")

if __name__ == "__main__":
    main()
