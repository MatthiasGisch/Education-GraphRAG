"""Retrieval-Funktionen für Paragraphen, Abbildungen und Konzepte via Vektor- und Graph-Suche."""
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
    """Erzeugt einen Embedding-Vektor für einen Query-Text über embed_text."""
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
    """Vektorsuche im Paragraph-Index; gibt supports-Einträge mit type='paragraph' zurück."""
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
          COALESCE(p.author, p.creator, '') AS authors,
          COALESCE(p.publication_year, p.creationDate, '') AS year,
          COALESCE(p.source, p.subject, '') AS source,
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
    """Vektorsuche im Figure-Index; gibt supports-Einträge mit type='figure' inkl. Bildpfad zurück."""
    rows = neo.run(
        """
        CALL db.index.vector.queryNodes('figure_embedding_index', $k, $embedding)
        YIELD node, score
        WITH node, score
        MATCH (f:Figure) WHERE f = node
        MATCH (p:Paper)-[:HAS_FIGURE]->(f)
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_FIGURE]->(f)
        RETURN
          'figure'                          AS type,
          f.figure_id                       AS figure_id,
          f.caption                         AS caption,
          f.figure_label                    AS figure_label,
          f.page                            AS page,
          coalesce(f.image_uri, f.image_path) AS image_uri,
          f.image_filename                  AS image_filename,
          f.analysis_json                   AS analysis_json,
          coalesce(f.figure_type, 'other')  AS figure_type,
          coalesce(f.entities, [])          AS entities,
          coalesce(f.ocr_hints, [])         AS ocr_hints,
          p.paper_id                        AS paper_id,
          p.title                           AS paper_title,
          p.doi                             AS doi,
          p.url                             AS url,
          COALESCE(p.author, p.creator, '') AS authors,
          COALESCE(p.publication_year, p.creationDate, '') AS year,
          COALESCE(p.source, p.subject, '') AS source,
          sec.section_id                    AS section_id,
          sec.title                         AS section_title,
          toFloat(score)                    AS score
        ORDER BY score DESC
        LIMIT $k
        """,
        {"embedding": embedding, "k": k},
    )
    return rows or []


def _expand_figure_context_with_paragraphs(neo: Neo4jClient, supports: List[Dict[str, Any]], limit: int = 200) -> None:
    """Ergänzt Paragraph-Supports zu gefundenen Figures via REFERS_TO/CAPTIONS in-place."""
    top_figs = [s for s in supports if s.get("type") == "figure" and s.get("figure_id")]
    if not top_figs:
        return

    fig_ids = [f["figure_id"] for f in top_figs]
    rows = neo.run(
        """
        UNWIND $ids AS fid
        MATCH (f:Figure {figure_id: fid})
        OPTIONAL MATCH (f)<-[rel:REFERS_TO|CAPTIONS|NEAR]-(para:Paragraph)
        WITH f, rel, para
        WHERE para IS NOT NULL
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
          COALESCE(p.author, p.creator, '') AS authors,
          COALESCE(p.publication_year, p.creationDate, '') AS year,
          COALESCE(p.source, p.subject, '') AS source,
          sec.section_id     AS section_id,
          sec.title          AS section_title,
          CASE type(rel) WHEN 'NEAR' THEN 0.75 ELSE 0.99 END AS score
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


def _vsearch_concepts(neo: Neo4jClient, embedding: List[float], k: int = 10, min_score: float = 0.6) -> List[Dict[str, Any]]:
    """Vektorsuche im Concept-Index; gibt die relevantesten Konzepte zur Query zurück."""
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
    """Traversiert MENTIONS-Kanten von Konzepten zu Paragraphen; boosted mehrfach erwähnte."""
    if not concept_ids:
        return []

    # --- Primär: Graph-Traversal über MENTIONS ---
    # Aggregation über alle Concept-Treffer pro Paragraph verhindert Duplikate
    rows = neo.run(
        """
        UNWIND $concept_ids AS cid
        MATCH (c:Concept {concept_id: cid})<-[m:MENTIONS]-(para:Paragraph)
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para)
        WITH para, p,
             collect(DISTINCT c.name) AS matched_concepts,
             avg(m.confidence)        AS avg_confidence,
             count(DISTINCT c)        AS concept_hits
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(para)
        WITH para, p, matched_concepts, avg_confidence, concept_hits,
             head(collect(sec)) AS sec
        RETURN
          'paragraph'              AS type,
          para.paragraph_id        AS paragraph_id,
          para.text                AS text,
          para.page                AS page,
          p.paper_id               AS paper_id,
          p.title                  AS paper_title,
          p.doi                    AS doi,
          p.url                    AS url,
          COALESCE(p.author, p.creator, '')             AS authors,
          COALESCE(p.publication_year, p.creationDate, '') AS year,
          COALESCE(p.source, p.subject, '')             AS source,
          sec.section_id           AS section_id,
          sec.title                AS section_title,
          avg_confidence * (1.0 + 0.1 * (concept_hits - 1)) AS score,
          matched_concepts[0]      AS matched_concept
        ORDER BY score DESC
        LIMIT $limit
        """,
        {"concept_ids": concept_ids, "limit": limit},
    ) or []

    # --- Fallback: Vector-Similarity für Concepts ohne MENTIONS-Kanten ---
    if len(rows) < limit // 2:
        matched_cids = {r["cid"] for r in neo.run(
            """
            UNWIND $concept_ids AS cid
            MATCH (:Concept {concept_id: cid})<-[:MENTIONS]-(:Paragraph)
            RETURN DISTINCT cid
            """,
            {"concept_ids": concept_ids},
        ) or []}
        orphan_cids = [cid for cid in concept_ids if cid not in matched_cids]

        if orphan_cids:
            emb_rows = neo.run(
                """
                UNWIND $cids AS cid
                MATCH (c:Concept {concept_id: cid})
                WHERE c.embedding IS NOT NULL
                RETURN c.embedding AS embedding
                """,
                {"cids": orphan_cids},
            ) or []
            embeddings = [r["embedding"] for r in emb_rows if r.get("embedding")]

            if embeddings:
                import numpy as np
                avg_emb = np.mean(embeddings, axis=0).tolist()
                fallback_limit = limit - len(rows)
                existing_ids = {r["paragraph_id"] for r in rows}

                fallback = neo.run(
                    """
                    CALL db.index.vector.queryNodes('paragraph_embedding_index', $limit, $embedding)
                    YIELD node, score
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
                      COALESCE(p.author, p.creator, '')                         AS authors,
                      COALESCE(p.publication_year, p.year, p.creationDate, '')  AS year,
                      COALESCE(p.source, p.subject, '')                         AS source,
                      sec.section_id           AS section_id,
                      sec.title                AS section_title,
                      toFloat(score) * 0.85    AS score,
                      'vector_fallback'        AS matched_concept
                    ORDER BY score DESC
                    LIMIT $limit
                    """,
                    {"embedding": avg_emb, "limit": fallback_limit},
                ) or []

                for r in fallback:
                    if r.get("paragraph_id") not in existing_ids:
                        rows.append(r)
                        existing_ids.add(r["paragraph_id"])

    return rows


def _expand_via_semantic_relations(neo: Neo4jClient, concept_ids: List[str], k: int = 5) -> List[str]:
    """Erweitert die Konzeptliste über SEMANTIC_RELATION-Kanten (IS_A, PART_OF, RELATED_TO)."""
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
    """Kombiniert Paragraph- und Figure-Vektorsuche und ergänzt optional Figure-Kontext."""
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
    min_concept_score: float = 0.6,
) -> Dict[str, Any]:
    """GraphRAG-Retrieval via Konzept-Vektorsuche, Graph-Traversal und direkter Paragraph-/Figure-Suche."""
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

