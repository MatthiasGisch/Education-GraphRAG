#!/usr/bin/env python
"""Test concept vector search directly."""
from src.neo import Neo4jClient
from src.openai_client import embed_text

neo = Neo4jClient()

# Test query
query = "Was ist künstliche Intelligenz?"
print(f"Query: {query}")

# Get embedding
emb = embed_text(query)
print(f"Embedding size: {len(emb)}")

# Test with different min_score thresholds
for min_score in [0.9, 0.8, 0.7, 0.6, 0.5, 0.0]:
    result = neo.run("""
        CALL db.index.vector.queryNodes('concept_embedding_index', $k, $embedding)
        YIELD node, score
        WHERE score >= $min_score
        RETURN
          node.name AS name,
          toFloat(score) AS score
        ORDER BY score DESC
        LIMIT 5
    """, {"embedding": emb, "k": 10, "min_score": min_score})
    
    print(f"\nmin_score={min_score}: Found {len(result)} concepts")
    for r in result[:3]:
        print(f"  - {r['name']}: {r['score']:.3f}")

neo.close()
