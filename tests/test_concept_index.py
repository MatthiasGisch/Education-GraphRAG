#!/usr/bin/env python
"""Prüft ob der Konzept-Vektorindex in Neo4j existiert und Daten mit Embeddings enthält."""
from src.neo import Neo4jClient

neo = Neo4jClient()

# Check all vector indexes
print("=== Vector Indexes ===")
indexes = neo.run("SHOW INDEXES")
for idx in indexes:
    if 'vector' in str(idx.get('type', '')).lower():
        print(f"  {idx.get('name')}: {idx.get('labelsOrTypes')} - {idx.get('properties')}")

# Check concepts
print("\n=== Concepts ===")
result = neo.run("MATCH (c:Concept) RETURN count(c) AS cnt")
concept_count = result[0]["cnt"]
print(f"Total concepts: {concept_count}")

if concept_count > 0:
    # Check embeddings
    result = neo.run("""
        MATCH (c:Concept) 
        WHERE c.embedding IS NOT NULL 
        RETURN count(c) AS cnt
    """)
    emb_count = result[0]["cnt"]
    print(f"Concepts with embeddings: {emb_count}")
    
    # Get sample concept
    result = neo.run("""
        MATCH (c:Concept)
        WHERE c.embedding IS NOT NULL
        RETURN c.name AS name, size(c.embedding) AS emb_size
        LIMIT 1
    """)
    if result:
        sample = result[0]
        print(f"Sample concept: '{sample['name']}' (embedding size: {sample['emb_size']})")
    
    # Get all concept names
    result = neo.run("MATCH (c:Concept) RETURN c.name AS name ORDER BY c.name")
    print(f"\nAll concept names:")
    for r in result:
        print(f"  - {r['name']}")

neo.close()
