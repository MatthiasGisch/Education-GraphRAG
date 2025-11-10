#!/usr/bin/env python3
"""
Löscht ALLE Daten aus der Datenbank (Papers, Paragraphs, Figures, Sections, Concepts, Topics).
Nutze dies um mit sauberer Datenbank neu zu starten.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.neo import Neo4jClient

def main():
    neo = Neo4jClient()
    
    print("⚠️  WARNUNG: Dieses Skript löscht ALLE Daten aus der Datenbank!")
    print("=" * 80)
    
    # Count all nodes
    result = neo.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count")
    
    print("\nAktueller Datenbestand:")
    total = 0
    for row in result:
        count = row['count']
        total += count
        print(f"  - {row['label']}: {count}")
    
    print(f"\nGesamt: {total} Knoten")
    print("\n" + "=" * 80)
    
    response = input("\nMöchtest du wirklich ALLE Daten löschen? (yes/no): ")
    
    if response.lower() != "yes":
        print("\n❌ Abgebrochen.")
        return
    
    print("\nLösche alle Knoten und Beziehungen...")
    
    # Delete everything
    neo.run("MATCH (n) DETACH DELETE n")
    
    # Verify
    result = neo.run("MATCH (n) RETURN count(n) AS count")
    remaining = result[0]['count'] if result else 0
    
    if remaining == 0:
        print("\n✅ Alle Daten erfolgreich gelöscht!")
        print("\nNächste Schritte:")
        print("  1. Schema neu anlegen: python scripts/create_schema.py")
        print("  2. PDFs neu ingestieren in der GUI oder via: python scripts/ingest.py")
    else:
        print(f"\n⚠️  {remaining} Knoten verbleiben in der Datenbank")

if __name__ == "__main__":
    main()
