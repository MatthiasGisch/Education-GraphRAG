#!/usr/bin/env python
"""Testet das Konzept-Matching direkt über Vektorsuche mit einer Beispielanfrage."""
from src.neo import KnowledgeGraph
from src.openai_client import get_embedding

kg = KnowledgeGraph()
query = "Was ist künstliche Intelligenz?"
emb = get_embedding(query)

print(f"Query embedding length: {len(emb)}")

# Test concept matching
concepts = kg.find_concepts(emb, 10)
print(f"\nFound concepts: {len(concepts)}")

if concepts:
    print("\nTop 3 concepts:")
    for c in concepts[:3]:
        print(f"  - {c['name']}: score={c['score']:.3f}")
else:
    print("\nNo concepts found!")
    # Check if concepts exist at all
    with kg.driver.session() as session:
        result = session.run("MATCH (c:Concept) RETURN count(c) AS cnt")
        count = result.single()["cnt"]
        print(f"Total concepts in database: {count}")
        
        # Check if embeddings exist
        result = session.run("""
            MATCH (c:Concept) 
            WHERE c.embedding IS NOT NULL 
            RETURN count(c) AS cnt
        """)
        emb_count = result.single()["cnt"]
        print(f"Concepts with embeddings: {emb_count}")
