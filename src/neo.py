# src/neo.py
from __future__ import annotations
from typing import Any, Dict, List
from neo4j import GraphDatabase
from .config import NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD


class Neo4jClient:
    def __init__(self) -> None:
        if not (NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD):
            raise RuntimeError("Neo4j credentials missing. Check .env")
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(cypher, params or {})
            return [r.data() for r in result]

    # --- WICHTIG: robuste Schema-Anlage (Kommentare rausfiltern, Semikolons splitten)
    def ensure_schema(self, schema_cypher: str) -> None:
        # Kommentare und leere Zeilen entfernen
        lines: List[str] = []
        for line in schema_cypher.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            lines.append(line)
        cleaned = "\n".join(lines)

        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        with self.driver.session() as session:
            for stmt in statements:
                session.run(stmt)

    # --- Upserts / Inserts ---
    def upsert_paper(self, paper_id: str, title: str | None, meta: Dict[str, Any]) -> None:
        self.run(
            """
            MERGE (p:Paper {paper_id:$paper_id})
            SET p.title = coalesce($title, p.title),
                p += $meta
            """,
            {"paper_id": paper_id, "title": title, "meta": meta},
        )

    def add_sections(self, paper_id: str, sections: list[dict]) -> None:
        self.run(
            """
            UNWIND $sections AS s
            MERGE (p:Paper {paper_id:$paper_id})
            MERGE (sec:Section {section_id: s.section_id})
            SET sec.title = s.title,
                sec.level = s.level,
                sec.page_start = s.page_start,
                sec.page_end = s.page_end,
                sec.`order` = s.order
            MERGE (p)-[:HAS_SECTION]->(sec)
            """,
            {"paper_id": paper_id, "sections": sections},
        )

    def add_paragraphs(self, paper_id: str, paragraphs: list[dict]) -> None:
        self.run(
            """
            UNWIND $paragraphs AS row
            MATCH (p:Paper {paper_id:$paper_id})
            MERGE (para:Paragraph {paragraph_id: row.paragraph_id})
            SET para.text = row.text,
                para.page = row.page,
                para.order_in_page = row.order_in_page,
                para.char_start = row.char_start,
                para.char_end = row.char_end,
                para.bbox = row.bbox,
                para.page_width = row.page_width,
                para.page_height = row.page_height,
                para.sha256 = row.sha256,
                para.embedding = row.embedding
            WITH p, para, row
            OPTIONAL MATCH (sec:Section {section_id: row.section_id_ref})
            FOREACH (_ IN CASE WHEN sec IS NULL THEN [] ELSE [1] END |
                MERGE (sec)-[:HAS_PARAGRAPH]->(para)
            )
            FOREACH (_ IN CASE WHEN sec IS NULL THEN [1] ELSE [] END |
                MERGE (p)-[:HAS_PARAGRAPH]->(para)  // Fallback, falls keine Section erkannt
            )
            """,
            {"paper_id": paper_id, "paragraphs": paragraphs},
        )

    def add_figures(self, paper_id: str, figures: list[dict]) -> None:
        self.run(
            """
            UNWIND $figures AS row
            MATCH (p:Paper {paper_id:$paper_id})
            MERGE (f:Figure {figure_id: row.figure_id})
            SET f.caption = row.caption,
                f.page = row.page,
                f.image_uri = row.image_uri,
                f.image_path = coalesce(row.image_path, row.image_uri),
                f.analysis_json = row.analysis_json,
                f.embedding = row.embedding,
                f.bbox = row.bbox,
                f.page_width = row.page_width,
                f.page_height = row.page_height,
                f.figure_label = row.figure_label
            MERGE (p)-[:HAS_FIGURE]->(f)
            """,
            {"paper_id": paper_id, "figures": figures},
        )

    # --- Vector-Retrieval ---
    def vector_search_paragraphs(self, embedding: List[float], k: int = 12) -> List[Dict[str, Any]]:
        return self.run(
            """
            CALL db.index.vector.queryNodes('paragraph_embedding_index', $k, $embedding)
            YIELD node, score
            MATCH (paper:Paper)-[:HAS_SECTION]->(sec)-[:HAS_PARAGRAPH]->(node)
            RETURN
                node.paragraph_id AS paragraph_id,
                node.text         AS text,
                node.page         AS page,
                node.order_in_page AS order_in_page,
                node.bbox         AS bbox,
                node.page_width   AS page_width,
                node.page_height  AS page_height,
                paper.paper_id    AS paper_id,
                paper.title       AS paper_title,
                paper.doi         AS doi,
                paper.url         AS url,
                sec.section_id    AS section_id,
                sec.title         AS section_title,
                sec.level         AS section_level,
                score
            ORDER BY score DESC
            """,
            {"embedding": embedding, "k": k},
        )

    def vector_search_figures(self, embedding: List[float], k: int = 6) -> List[Dict[str, Any]]:
        return self.run(
            """
            CALL db.index.vector.queryNodes('figure_embedding_index', $k, $embedding)
            YIELD node, score
            MATCH (paper:Paper)-[:HAS_FIGURE]->(node)
            OPTIONAL MATCH (paper)-[:HAS_SECTION]->(sec)
              WHERE node.page >= coalesce(sec.page_start, -1)
                AND node.page <= coalesce(sec.page_end,  999999)
            RETURN
                node.figure_id    AS figure_id,
                node.caption      AS caption,
                node.page         AS page,
                node.image_uri    AS image_uri,
                node.bbox         AS bbox,
                node.page_width   AS page_width,
                node.page_height  AS page_height,
                node.figure_label AS figure_label,
                paper.paper_id    AS paper_id,
                paper.title       AS paper_title,
                paper.doi         AS doi,
                paper.url         AS url,
                sec.section_id    AS section_id,
                sec.title         AS section_title,
                sec.level         AS section_level,
                score
            ORDER BY score DESC
            """,
            {"embedding": embedding, "k": k},
        )

    # --- Concepts & Topics ---

    def upsert_topic(self, name: str) -> None:
        self.run("MERGE (t:Topic {name:$name})", {"name": name})

    def add_concepts(self, topic_name: str, concepts: list[dict]) -> None:
        """
        concepts: [{concept_id, name, alt_labels, description, embedding}]
        """
        self.run(
            """
            MERGE (t:Topic {name:$topic})
            WITH t, $concepts AS concepts
            UNWIND concepts AS c
            MERGE (co:Concept {concept_id: c.concept_id})
            SET co.name        = c.name,
                co.alt_labels  = c.alt_labels,
                co.description = c.description,
                co.embedding   = c.embedding
            MERGE (t)-[:HAS_CONCEPT]->(co)
            """,
            {"topic": topic_name, "concepts": concepts},
        )

    def link_paragraphs_to_concepts(self, paper_id: str, links: list[dict]) -> None:
        """
        links: [{paragraph_id, concept_id, confidence}]
        - Hängt para->MENTIONS->concept
        - Aggregiert zusätzlich paper->ABOUT->concept (gewichtete Summe)
        """
        self.run(
            """
            UNWIND $links AS L
            MATCH (p:Paper {paper_id:$paper_id})-[:HAS_PARAGRAPH]->(para:Paragraph {paragraph_id: L.paragraph_id})
            MATCH (c:Concept {concept_id: L.concept_id})
            MERGE (para)-[r:MENTIONS]->(c)
            SET r.confidence = coalesce(L.confidence, 0.7)
            WITH p, c, r
            MERGE (p)-[ab:ABOUT]->(c)
            SET ab.weight = coalesce(ab.weight, 0.0) + coalesce(r.confidence, 0.0)
            """,
            {"paper_id": paper_id, "links": links},
        )

    def vector_search_concepts(self, embedding: list[float], k: int = 10) -> list[dict]:
        return self.run(
            """
            CALL db.index.vector.queryNodes('concept_embedding_index', $k, $embedding)
            YIELD node, score
            OPTIONAL MATCH (node)<-[:MENTIONS]-(para:Paragraph)
            WITH node, score, count(DISTINCT para) AS mentions
            RETURN node.concept_id AS concept_id,
                node.name       AS name,
                node.alt_labels AS alt_labels,
                node.description AS description,
                mentions        AS mentions,
                score
            ORDER BY score DESC
            """,
            {"embedding": embedding, "k": k},
        )
