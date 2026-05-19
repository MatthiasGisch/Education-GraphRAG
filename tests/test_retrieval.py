"""Schnelltest für den vektorbasierten Konzept-Retriever mit einer Beispielanfrage."""
from src.neo import Neo4jClient
from src.retriever import concept_based_retrieve

neo = Neo4jClient()

print("Testing concept-based retrieval with vector similarity...")
print("=" * 60)

query = "Was ist künstliche Intelligenz?"
result = concept_based_retrieve(
    neo, 
    query, 
    k_concepts=5, 
    k_paragraphs_via_concepts=10
)

print(f"\nQuery: {query}")
print(f"\nMatched concepts: {result['debug']['matched_concepts_count']}")
print(f"Concept names: {result['debug'].get('matched_concepts', [])}")
print(f"Paragraphs via concepts: {result['debug'].get('paragraphs_via_concepts', 0)}")
print(f"Paragraphs direct: {result['debug'].get('paragraphs_direct', 0)}")
print(f"Total supports: {len(result['supports'])}")

if result['supports']:
    print(f"\nFirst support example:")
    first = result['supports'][0]
    print(f"  Type: {first['type']}")
    print(f"  Paper: {first.get('paper_title', 'N/A')}")
    print(f"  Score: {first.get('score', 0):.3f}")
    print(f"  Text: {first.get('text', 'N/A')[:200]}...")

print("\n" + "=" * 60)
print("✓ Vector-based concept retrieval works!")
