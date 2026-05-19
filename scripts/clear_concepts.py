#!/usr/bin/env python3
"""Löscht alle Concept- und Topic-Knoten aus der Datenbank; Papers und Paragraphen bleiben erhalten."""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.neo import Neo4jClient

def main():
    """Löscht alle Concept- und Topic-Knoten mit zugehörigen Beziehungen aus der Datenbank."""
    neo = Neo4jClient()

    print("Lösche alle Konzepte und zugehörige Beziehungen...")
    
    # Count before deletion
    result = neo.run("MATCH (c:Concept) RETURN count(c) AS count")
    concept_count = result[0]['count'] if result else 0
    
    result = neo.run("MATCH (t:Topic) RETURN count(t) AS count")
    topic_count = result[0]['count'] if result else 0
    
    print(f"\nVor dem Löschen:")
    print(f"  - {concept_count} Konzepte")
    print(f"  - {topic_count} Topics")
    
    # Delete all concepts and their relationships
    neo.run("MATCH (c:Concept) DETACH DELETE c")
    
    # Delete all topics
    neo.run("MATCH (t:Topic) DETACH DELETE t")
    
    # Verify deletion
    result = neo.run("MATCH (c:Concept) RETURN count(c) AS count")
    remaining_concepts = result[0]['count'] if result else 0
    
    result = neo.run("MATCH (t:Topic) RETURN count(t) AS count")
    remaining_topics = result[0]['count'] if result else 0
    
    print(f"\n✅ Erfolgreich gelöscht!")
    print(f"  - {concept_count - remaining_concepts} Konzepte gelöscht")
    print(f"  - {topic_count - remaining_topics} Topics gelöscht")
    
    if remaining_concepts > 0 or remaining_topics > 0:
        print(f"\n⚠️  Noch vorhanden: {remaining_concepts} Konzepte, {remaining_topics} Topics")
    
    print("\nPapers, Paragraphs und Figures bleiben erhalten.")
    print("Nutze 'Konzepte aus bestehenden Papern extrahieren' um neue Konzepte zu erstellen.")

if __name__ == "__main__":
    main()
