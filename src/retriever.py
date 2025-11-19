# src/retriever.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
import os
import logging

from .neo import Neo4jClient
from .openai_client import embed_text

log = logging.getLogger(__name__)

# =========================
# Embedding-Konfiguration
# =========================

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")  # 3072-D


def _embed_query(text: str) -> List[float]:
    """Erzeugt eine Query-Embedding-Vektorrepräsentation.

    Diese Funktion verwendet die zentrale `embed_text`-Hilfe aus `openai_client.py`.
    Dadurch vermeiden wir, beim Import bereits einen OpenAI-Client zu instanziieren
    und erhalten konsistente Fehlerbehandlung (z. B. wenn OPENAI_API_KEY fehlt).
    """
    try:
        emb = embed_text(text, model=EMBED_MODEL)
    except Exception as e:
        log.error("Embedding fehlgeschlagen: %s", e)
        raise
    return emb


# =========================
# Neo4j Vector-Suche (Paragraphs / Figures)
# =========================
def _vsearch_paragraphs(neo: Neo4jClient, embedding: List[float], k: int = 24) -> List[Dict[str, Any]]:
    """
    Vector-Suche über Paragraph-Index. Liefert direkt verwertbare 'supports'-Einträge (type='paragraph').
    """
    rows = neo.run(
        """
        CALL db.index.vector.queryNodes('paragraph_embedding_index', $k, $embedding)
        YIELD node, score
        WITH node, score
        MATCH (para:Paragraph) WHERE para = node
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
        RETURN
          'paragraph'              AS type,
          para.paragraph_id        AS paragraph_id,
          para.text                AS text,
          para.page                AS page,
          p.paper_id               AS paper_id,
          p.title                  AS paper_title,
          p.doi                    AS doi,
          p.url                    AS url,
          p.pdf_author             AS authors,
          p.pdf_creation_date      AS year,
          p.pdf_subject            AS source,
          sec.section_id           AS section_id,
          sec.title                AS section_title,
          toFloat(score)           AS score
        ORDER BY score DESC
        LIMIT $k
        """,
        {"embedding": embedding, "k": k},
    )
    return rows or []


def _vsearch_figures(neo: Neo4jClient, embedding: List[float], k: int = 8) -> List[Dict[str, Any]]:
    """
    Vector-Suche über Figure-Index. Liefert 'supports'-Einträge (type='figure') inkl. image_uri/caption.
    """
    rows = neo.run(
        """
        CALL db.index.vector.queryNodes('figure_embedding_index', $k, $embedding)
        YIELD node, score
        WITH node, score
        MATCH (f:Figure) WHERE f = node
        MATCH (p:Paper)-[:HAS_FIGURE]->(f)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_FIGURE]->(f)
        RETURN
          'figure'                 AS type,
          f.figure_id              AS figure_id,
          f.caption                AS caption,
          f.figure_label           AS figure_label,
          f.page                   AS page,
          coalesce(f.image_uri, f.image_path) AS image_uri,
          f.analysis_json          AS analysis_json,
          p.paper_id               AS paper_id,
          p.title                  AS paper_title,
          p.doi                    AS doi,
          p.url                    AS url,
          p.pdf_author             AS authors,
          p.pdf_creation_date      AS year,
          p.pdf_subject            AS source,
          sec.section_id           AS section_id,
          sec.title                AS section_title,
          toFloat(score)           AS score
        ORDER BY score DESC
        LIMIT $k
        """,
        {"embedding": embedding, "k": k},
    )
    return rows or []


def _expand_figure_context_with_paragraphs(neo: Neo4jClient, supports: List[Dict[str, Any]], limit: int = 200) -> None:
    """
    Ergänzt zu bereits gefundenen Figure-Supports passende Paragraph-Supports über REFERS_TO/CAPTIONS.
    Dedupliziert gegen bereits vorhandene Paragraphs. Modifiziert 'supports' IN PLACE.
    """
    top_figs = [s for s in supports if s.get("type") == "figure" and s.get("figure_id")]
    if not top_figs:
        return

    fig_ids = [f["figure_id"] for f in top_figs]
    rows = neo.run(
        """
        UNWIND $ids AS fid
        MATCH (f:Figure {figure_id: fid})<-[:REFERS_TO|:CAPTIONS]-(para:Paragraph)
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
        RETURN
          'paragraph'        AS type,
          para.paragraph_id  AS paragraph_id,
          para.text          AS text,
          para.page          AS page,
          p.paper_id         AS paper_id,
          p.title            AS paper_title,
          p.doi              AS doi,
          p.url              AS url,
          p.pdf_author       AS authors,
          p.pdf_creation_date AS year,
          p.pdf_subject      AS source,
          sec.section_id     AS section_id,
          sec.title          AS section_title,
          0.99               AS score
        LIMIT $limit
        """,
        {"ids": fig_ids, "limit": limit},
    ) or []

    existing_para_ids = {s.get("paragraph_id") for s in supports if s.get("type") == "paragraph"}
    for r in rows:
        pid = r.get("paragraph_id")
        if pid and pid not in existing_para_ids:
            supports.append(r)
            existing_para_ids.add(pid)


def _vsearch_concepts(neo: Neo4jClient, embedding: List[float], k: int = 10, min_score: float = 0.7) -> List[Dict[str, Any]]:
    """
    Vector-Suche über Concept-Index. Findet die relevantesten Konzepte zur Query.
    """
    rows = neo.run(
        """
        CALL db.index.vector.queryNodes('concept_embedding_index', $k, $embedding)
        YIELD node, score
        WHERE score >= $min_score
        RETURN
          node.concept_id   AS concept_id,
          node.name         AS concept_name,
          node.description  AS description,
          toFloat(score)    AS score
        ORDER BY score DESC
        LIMIT $k
        """,
        {"embedding": embedding, "k": k, "min_score": min_score},
    )
    return rows or []


def _paragraphs_via_concepts(neo: Neo4jClient, concept_ids: List[str], limit: int = 20) -> List[Dict[str, Any]]:
    """
    Graph-Traversierung: Von Concepts zu Paragraphs über MENTIONS-Beziehung.
    Holt Paragraphs die relevante Concepts erwähnen.
    """
    if not concept_ids:
        return []
    
    rows = neo.run(
        """
        UNWIND $concept_ids AS cid
        MATCH (c:Concept {concept_id: cid})<-[m:MENTIONS]-(para:Paragraph)
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
        WITH DISTINCT para, p, sec, m, c
        RETURN
          'paragraph'              AS type,
          para.paragraph_id        AS paragraph_id,
          para.text                AS text,
          para.page                AS page,
          p.paper_id               AS paper_id,
          p.title                  AS paper_title,
          p.doi                    AS doi,
          p.url                    AS url,
          p.pdf_author             AS authors,
          p.pdf_creation_date      AS year,
          p.pdf_subject            AS source,
          sec.section_id           AS section_id,
          sec.title                AS section_title,
          coalesce(m.confidence, 0.8) AS score,
          c.name                   AS matched_concept
        ORDER BY score DESC
        LIMIT $limit
        """,
        {"concept_ids": concept_ids, "limit": limit},
    )
    return rows or []


def _expand_via_semantic_relations(neo: Neo4jClient, concept_ids: List[str], k: int = 5) -> List[str]:
    """
    Erweitert Concept-Liste über SEMANTIC_RELATION (IS_A, PART_OF, etc.).
    Findet verwandte Concepts die ebenfalls relevant sein könnten.
    """
    if not concept_ids:
        return []
    
    rows = neo.run(
        """
        UNWIND $concept_ids AS cid
        MATCH (c1:Concept {concept_id: cid})-[r:SEMANTIC_RELATION]-(c2:Concept)
        WHERE r.relation_type IN ['IS_A', 'PART_OF', 'RELATED_TO']
        RETURN DISTINCT c2.concept_id AS concept_id
        LIMIT $k
        """,
        {"concept_ids": concept_ids, "k": k},
    )
    return [r["concept_id"] for r in rows if r.get("concept_id")]


# =========================
# Öffentliche API
# =========================
def hybrid_retrieve(
    neo: Neo4jClient,
    query: str,
    *,
    k_paragraphs: int = 18,
    k_figures: int = 6,
    add_figure_context: bool = True,
    use_concept_based: bool = False,
) -> Dict[str, Any]:
    """
    Hybrid-Retrieval:
      1) Embed Query
      2) Vector-Suche Paragraphs & Figures (getrennte Indizes)
      3) (optional) Figure-Kontext aus Paragraphen via REFERS_TO/CAPTIONS nachziehen
      4) Supports zusammenführen

    Rückgabe:
      {"supports": [ {type: 'paragraph'| 'figure', ...}, ... ]}
    """
    emb = _embed_query(query)

    paras = _vsearch_paragraphs(neo, emb, k=k_paragraphs)
    figs  = _vsearch_figures(neo, emb, k=k_figures)

    # Mergen: Paragraphs zuerst, dann Figures
    supports: List[Dict[str, Any]] = []
    supports.extend(paras)
    supports.extend(figs)

    # Figure-Kontext (Absätze) ergänzen
    if add_figure_context:
        try:
            _expand_figure_context_with_paragraphs(neo, supports, limit=200)
        except Exception as e:
            log.warning("Figure-Kontext konnte nicht erweitert werden: %s", e)

    return {"supports": supports}


def concept_based_retrieve(
    neo: Neo4jClient,
    query: str,
    *,
    k_concepts: int = 10,
    k_paragraphs_direct: int = 15,
    k_paragraphs_via_concepts: int = 20,
    k_figures: int = 6,
    expand_semantic: bool = True,
    add_figure_context: bool = True,
    min_concept_score: float = 0.7,
) -> Dict[str, Any]:
    """
    Intelligentes Concept-basiertes Retrieval:
      1) Embed Query
      2) Vector-Suche auf Concepts → findet relevante Konzepte
      3) Graph-Traversierung: Concepts → MENTIONS ← Paragraphs
      4) (optional) Semantic Relations: erweitere Concepts über IS_A/PART_OF/RELATED_TO
      5) Vector-Suche Paragraphs (direkter Match als Fallback)
      6) Vector-Suche Figures
      7) Deduplizierung & Merge
      
    Rückgabe:
      {"supports": [...], "matched_concepts": [...], "debug": {...}}
    """
    emb = _embed_query(query)
    debug: Dict[str, Any] = {}
    
    # 1) Finde relevante Concepts via Vector-Suche
    concepts = _vsearch_concepts(neo, emb, k=k_concepts, min_score=min_concept_score)
    concept_ids = [c["concept_id"] for c in concepts]
    debug["matched_concepts_count"] = len(concept_ids)
    debug["matched_concepts"] = [c["concept_name"] for c in concepts[:5]]  # Top 5 für Debug
    
    # 2) Erweitere über semantische Relationen
    if expand_semantic and concept_ids:
        related_ids = _expand_via_semantic_relations(neo, concept_ids, k=5)
        if related_ids:
            concept_ids.extend(related_ids)
            concept_ids = list(set(concept_ids))  # Deduplizieren
            debug["expanded_via_relations"] = len(related_ids)
    
    # 3) Hole Paragraphs über Concept-MENTIONS-Beziehung
    paras_via_concepts = []
    if concept_ids:
        paras_via_concepts = _paragraphs_via_concepts(neo, concept_ids, limit=k_paragraphs_via_concepts)
        debug["paragraphs_via_concepts"] = len(paras_via_concepts)
    
    # 4) Direkte Vector-Suche Paragraphs (als Ergänzung)
    paras_direct = _vsearch_paragraphs(neo, emb, k=k_paragraphs_direct)
    debug["paragraphs_direct"] = len(paras_direct)
    
    # 5) Vector-Suche Figures
    figs = _vsearch_figures(neo, emb, k=k_figures)
    debug["figures"] = len(figs)
    
    # 6) Merge & Deduplizierung
    supports: List[Dict[str, Any]] = []
    seen_para_ids = set()
    
    # Concept-basierte Paragraphs zuerst (höhere Priorität)
    for p in paras_via_concepts:
        pid = p.get("paragraph_id")
        if pid and pid not in seen_para_ids:
            supports.append(p)
            seen_para_ids.add(pid)
    
    # Dann direkte Paragraph-Matches
    for p in paras_direct:
        pid = p.get("paragraph_id")
        if pid and pid not in seen_para_ids:
            supports.append(p)
            seen_para_ids.add(pid)
    
    # Figures hinzufügen
    supports.extend(figs)
    
    # 7) Figure-Kontext ergänzen
    if add_figure_context:
        try:
            _expand_figure_context_with_paragraphs(neo, supports, limit=200)
        except Exception as e:
            log.warning("Figure-Kontext konnte nicht erweitert werden: %s", e)
    
    debug["total_supports"] = len(supports)
    
    return {
        "supports": supports,
        "matched_concepts": concepts,
        "debug": debug
    }

