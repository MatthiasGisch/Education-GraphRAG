"""Manueller Integrationstest für den Graph-Traversal-Retriever: prüft MENTIONS-Kanten und concept_based_retrieve."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.neo import Neo4jClient
from src.retriever import concept_based_retrieve, hybrid_retrieve, _paragraphs_via_concepts, _vsearch_concepts
from src.openai_client import embed_text


def separator(title: str):
    """Gibt einen formatierten Trennbalken mit Titel auf stdout aus."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def main():
    """Führt alle vier Retrieval-Tests sequenziell aus und gibt Diagnosedaten aus."""
    neo = Neo4jClient()

    # ----------------------------------------------------------------
    # 1. Voraussetzung: Sind MENTIONS-Kanten vorhanden?
    # ----------------------------------------------------------------
    separator("1. MENTIONS-Kanten im Graph")

    stats = neo.run("""
        MATCH (c:Concept)<-[m:MENTIONS]-(para:Paragraph)
        RETURN count(m) AS mentions_total,
               count(DISTINCT c) AS concepts_mit_mentions,
               count(DISTINCT para) AS paragraphen_mit_mentions
    """)[0]

    print(f"  MENTIONS-Kanten gesamt:       {stats['mentions_total']}")
    print(f"  Concepts mit MENTIONS:         {stats['concepts_mit_mentions']}")
    print(f"  Paragraphen mit MENTIONS:      {stats['paragraphen_mit_mentions']}")

    if stats["mentions_total"] == 0:
        print("\n  FEHLER: Keine MENTIONS-Kanten vorhanden.")
        print("  -> Neuer Ingest notwendig bevor Graph-Traversal funktioniert.")
        neo.close()
        sys.exit(1)

    # ----------------------------------------------------------------
    # 2. Einen bekannten Concept direkt traversieren
    # ----------------------------------------------------------------
    separator("2. Direkter Graph-Traversal Test")

    sample = neo.run("""
        MATCH (c:Concept)<-[:MENTIONS]-(para:Paragraph)
        RETURN c.concept_id AS cid, c.name AS name, count(para) AS mentions
        ORDER BY mentions DESC
        LIMIT 3
    """)

    print("  Top-3 Concepts nach MENTIONS-Anzahl:")
    for row in sample:
        print(f"  - '{row['name']}' ({row['mentions']} Mentions, id={row['cid']})")

    if sample:
        top_cid = sample[0]["cid"]
        top_name = sample[0]["name"]
        results = _paragraphs_via_concepts(neo, [top_cid], limit=5)
        print(f"\n  Graph-Traversal für '{top_name}': {len(results)} Paragraphen gefunden")
        for r in results[:2]:
            path = "GRAPH" if r.get("matched_concept") != "vector_fallback" else "VECTOR-FALLBACK"
            print(f"  [{path}] Score={r['score']:.3f} | {str(r.get('text',''))[:80]}...")

    # ----------------------------------------------------------------
    # 3. concept_based_retrieve mit einer echten Query
    # ----------------------------------------------------------------
    separator("3. concept_based_retrieve (End-to-End)")

    # Nimm einen Begriff der im Thema vorkommt
    test_queries = [
        "knowledge graph retrieval augmented generation",
        "graph neural network embedding",
    ]

    for query in test_queries:
        print(f"\n  Query: '{query}'")
        result = concept_based_retrieve(neo, query, k_concepts=5, k_paragraphs_direct=5,
                                        k_paragraphs_via_concepts=10)

        concepts = result.get("matched_concepts", [])
        supports = result.get("supports", [])
        debug = result.get("debug", {})

        print(f"  Gefundene Concepts:     {len(concepts)}")
        for c in concepts[:3]:
            print(f"    - {c['concept_name']} (score={c['score']:.3f})")

        graph_paras = [s for s in supports
                       if s.get("type") == "paragraph"
                       and s.get("matched_concept") != "vector_fallback"
                       and s.get("matched_concept") != "vector_similarity"]
        fallback_paras = [s for s in supports
                          if s.get("matched_concept") in ("vector_fallback", "vector_similarity")]
        direct_paras = [s for s in supports
                        if s.get("type") == "paragraph"
                        and s.get("matched_concept") is None]

        print(f"  Paragraphen via Graph-Traversal: {len(graph_paras)}")
        print(f"  Paragraphen via Vector-Fallback: {len(fallback_paras)}")
        print(f"  Paragraphen via direktem Vector: {len(direct_paras)}")
        print(f"  Figures:                         {len([s for s in supports if s.get('type') == 'figure'])}")
        print(f"  Gesamt supports:                 {len(supports)}")

        if graph_paras:
            best = graph_paras[0]
            print(f"\n  Bester Graph-Traversal Treffer (score={best['score']:.3f}):")
            print(f"  Paper: {best.get('paper_title','?')}")
            print(f"  Text:  {str(best.get('text',''))[:120]}...")
        else:
            print("\n  WARNUNG: Keine Paragraphen via Graph-Traversal gefunden!")
            print("  -> Entweder kein Ingest mit dieser Query oder MENTIONS fehlen.")

    # ----------------------------------------------------------------
    # 4. Vergleich: concept_based vs. hybrid (reines Vector)
    # ----------------------------------------------------------------
    separator("4. Vergleich Graph-Traversal vs. reines Vector-RAG")

    q = test_queries[0]
    graph_result = concept_based_retrieve(neo, q, k_paragraphs_via_concepts=15, k_paragraphs_direct=5)
    vector_result = hybrid_retrieve(neo, q, k_paragraphs=20)

    graph_ids = {s["paragraph_id"] for s in graph_result["supports"] if s.get("type") == "paragraph"}
    vector_ids = {s["paragraph_id"] for s in vector_result["supports"] if s.get("type") == "paragraph"}

    overlap = graph_ids & vector_ids
    only_graph = graph_ids - vector_ids
    only_vector = vector_ids - graph_ids

    print(f"  Query: '{q}'")
    print(f"  Graph-RAG Paragraphen:    {len(graph_ids)}")
    print(f"  Vector-RAG Paragraphen:   {len(vector_ids)}")
    print(f"  Überschneidung:           {len(overlap)}")
    print(f"  Nur im Graph gefunden:    {len(only_graph)}  <- GraphRAG-Mehrwert")
    print(f"  Nur im Vector gefunden:   {len(only_vector)}")

    neo.close()
    print("\n" + "="*60)
    print("  Test abgeschlossen.")
    print("="*60)


if __name__ == "__main__":
    main()
