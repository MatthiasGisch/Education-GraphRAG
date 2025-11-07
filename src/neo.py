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

    def vector_search_concepts(self, embedding: list[float], k: int = 10, min_score: float = 0.0) -> list[dict]:
        """
        Vector similarity search for concepts with score threshold.
        Returns concepts sorted by similarity score, filtered by min_score if provided.
        """
        return self.run(
            """
            CALL db.index.vector.queryNodes('concept_embedding_index', $k, $embedding)
            YIELD node, score
            WHERE score >= $min_score
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
            {"embedding": embedding, "k": k, "min_score": min_score},
        )

    def vector_search_similar_concepts(self, embedding: list[float], min_similarity: float = 0.92) -> list[dict]:
        """
        Find semantically similar existing concepts using vector similarity.
        Returns only concepts above the similarity threshold.
        """
        return self.vector_search_concepts(embedding=embedding, k=5, min_score=min_similarity)

    def _get_single_value(self, cypher: str) -> int:
        """Hilfsmethode: Führt Cypher aus und holt einen einzelnen Zählwert."""
        res = self.run(cypher)
        return int(res[0]["c"]) if res and "c" in res[0] else 0

    def stitch_document_hierarchy(self) -> dict:
        """
        Verbindet Paragraphs/Figures mit ihren Sections basierend auf Seitenbereichen.
        Erstellt Dummy-Sections für Paper ohne Sections.

        Rückgabe: Stats über Verknüpfungen vor/nach dem Stitching.
        """
        # Dummy-Section pro Paper falls nötig
        self.run("""
        MATCH (p:Paper)
        WHERE NOT (p)-[:HAS_SECTION]->()
        MERGE (s:Section {section_id: p.paper_id + ":DOC"})
        SET s.title = "Document",
            s.level = 1,
            s.page_start = 1,
            s.page_end = 999999,
            s.`order` = 1
        MERGE (p)-[:HAS_SECTION]->(s)
        """)

        # Paragraphs zu passenden Sections verbinden
        self.run("""
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
        MATCH (p)-[:HAS_SECTION]->(sec:Section)
        WHERE para.page >= coalesce(sec.page_start, 1)
          AND para.page <= coalesce(sec.page_end, 999999)
        MERGE (sec)-[:HAS_PARAGRAPH]->(para)
        """)

        # Figures zu passenden Sections verbinden
        self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        MATCH (p)-[:HAS_SECTION]->(sec:Section)
        WHERE f.page >= coalesce(sec.page_start, 1)
          AND f.page <= coalesce(sec.page_end, 999999)
        MERGE (sec)-[:HAS_FIGURE]->(f)
        """)

        stats = {}
        stats["paras_direct_at_paper"] = self._get_single_value(
            "MATCH (p:Paper)-[:HAS_PARAGRAPH]->(:Paragraph) RETURN count(*) AS c")
        stats["paras_via_section"] = self._get_single_value(
            "MATCH (:Section)-[:HAS_PARAGRAPH]->(:Paragraph) RETURN count(*) AS c")
        stats["figs_at_paper"] = self._get_single_value(
            "MATCH (p:Paper)-[:HAS_FIGURE]->(:Figure) RETURN count(*) AS c")
        stats["figs_via_section"] = self._get_single_value(
            "MATCH (:Section)-[:HAS_FIGURE]->(:Figure) RETURN count(*) AS c")
        stats["paragraph_orphans"] = self._get_single_value(
            "MATCH (para:Paragraph) WHERE NOT (()-[:HAS_PARAGRAPH]->(para)) RETURN count(para) AS c")
        stats["figure_orphans"] = self._get_single_value(
            "MATCH (f:Figure) WHERE NOT (()-[:HAS_FIGURE]->(f)) RETURN count(f) AS c")
        stats["papers_without_sections"] = self._get_single_value(
            "MATCH (p:Paper) WHERE NOT (p)-[:HAS_SECTION]->() RETURN count(p) AS c")
        return stats

    def stitch_figures_to_paragraphs(self, prefix_length: int = 60, page_tolerance: int = 1) -> dict:
        """
        Verbindet Figures mit relevanten Paragraphen über drei Arten von Beziehungen:
        1) CAPTIONS: Absatz enthält Prefix der (normalisierten) Caption
        2) REFERS_TO: Absatz erwähnt figure_label (z.B. "Figure 2", "Fig. 2")
        3) NEAR: Fallback für noch unverbundene Figures

        Args:
            prefix_length: Länge des Caption-Prefixes für den ersten CAPTIONS-Pass
            page_tolerance: ±Seiten für Caption/Reference-Matching

        Rückgabe: Stats über neue Verbindungen + Beispiele unverbundener Figures
        """
        def cnt(rel: str) -> int:
            return self._get_single_value(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")

        cap_before = cnt("CAPTIONS")
        ref_before = cnt("REFERS_TO")
        near_before = cnt("NEAR")

        # PASS 1a: CAPTIONS mit längerem Präfix, ±1 Seite
        self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        WHERE NOT (()-[:CAPTIONS]->(f))
        WITH p, f,
             toLower(coalesce(replace(replace(replace(f.caption, '\r',' '), '\n',' '), '  ',' '), '')) AS cap,
             coalesce(f.page,-1) AS fpage
        WHERE cap <> ''
        WITH p, f, cap, fpage, substring(cap,0,$prefix) AS pref
        MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        WHERE para.page IN range(fpage-$tol, fpage+$tol)
          AND toLower(para.text) CONTAINS pref
        MERGE (para)-[:CAPTIONS]->(f)
        """, {"prefix": prefix_length, "tol": page_tolerance})

        # PASS 1b: CAPTIONS mit kürzerem Präfix, nur gleiche Seite
        self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        WHERE NOT (()-[:CAPTIONS]->(f))
        WITH p, f,
             toLower(coalesce(replace(replace(replace(f.caption, '\r',' '), '\n',' '), '  ',' '), '')) AS cap,
             coalesce(f.page,-1) AS fpage
        WHERE cap <> ''
        WITH p, f, cap, fpage, substring(cap,0,25) AS pref
        MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        WHERE para.page = fpage
          AND toLower(para.text) CONTAINS pref
        MERGE (para)-[:CAPTIONS]->(f)
        """)

        # PASS 2a: REFERS_TO mit figure_label direkt
        self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        WHERE NOT (()-[:REFERS_TO]->(f))
        WITH p, f,
             toLower(coalesce(replace(f.figure_label,'.',''), '')) AS flbl,
             coalesce(f.page,-1) AS fpage
        WHERE flbl <> ''
        MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        WHERE para.page IN range(fpage-$tol, fpage+$tol)
          AND toLower(replace(para.text,'.','')) CONTAINS flbl
        MERGE (para)-[:REFERS_TO]->(f)
        """, {"tol": page_tolerance})

        # PASS 2b: REFERS_TO mit Varianten (figure/fig/abb)
        self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        WHERE NOT (()-[:REFERS_TO]->(f))
        WITH p, f,
             toLower(coalesce(replace(f.figure_label,'.',''), '')) AS flbl,
             coalesce(f.page,-1) AS fpage
        WHERE flbl =~ '.*\\d+.*'   // nur wenn Ziffern drin sind
        WITH p, f, fpage,
             replace(replace(replace(replace(flbl,'figure',''),'fig',''),'abb',''),'  ',' ') AS numish
        MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        WHERE para.page IN range(fpage-$tol, fpage+$tol)
          AND (
               toLower(para.text) CONTAINS ('figure ' + numish) OR
               toLower(para.text) CONTAINS ('fig ' + numish)    OR
               toLower(para.text) CONTAINS ('abb ' + numish)
          )
        MERGE (para)-[:REFERS_TO]->(f)
        """, {"tol": page_tolerance})

        # PASS 3: NEAR Fallback
        self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        WHERE NOT (()-[:CAPTIONS|:REFERS_TO]->(f))
        MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        WHERE para.page = coalesce(f.page,-999999)
        WITH f, para
        ORDER BY size(coalesce(para.text,'')) DESC
        WITH f, head(collect(para)) AS best
        MERGE (best)-[:NEAR]->(f)
        """)

        cap_after = cnt("CAPTIONS")
        ref_after = cnt("REFERS_TO")
        near_after = cnt("NEAR")

        # Beispiele für weiterhin unverbundene Figures
        samples = self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        WHERE NOT (()-[:CAPTIONS|:REFERS_TO|:NEAR]->(f))
        RETURN p.title    AS paper,
               f.figure_id  AS figure_id,
               f.page      AS page,
               f.figure_label AS figure_label,
               left(coalesce(f.caption,''), 100) AS caption
        LIMIT 10
        """)

        return {
            "captions_before": cap_before, "captions_after": cap_after,
            "captions_new": max(0, cap_after - cap_before),
            "refers_before": ref_before, "refers_after": ref_after,
            "refers_new": max(0, ref_after - ref_before),
            "near_before": near_before, "near_after": near_after,
            "near_new": max(0, near_after - near_before),
            "still_unlinked_examples": samples
        }

    def cluster_concepts_into_umbrellas(self, topic_name: str, sim_threshold: float = 0.86, min_cluster_size: int = 2) -> dict:
        """
        Bildet Umbrella-Knoten aus Concept-Embeddings für ein Topic.

        Rückgabe: {"clusters": n_clusters, "umbrellas_created": n_created, "assigned": n_assigned}

        Diese Methode repliziert die (einfache) Greedy-Clustering-Logik, die zuvor in
        `scripts/gui_app.py` implementiert war, und stellt sie als wiederverwendbare Backend-Funktion
        bereit, damit andere Teile der Applikation (CLI/GUI/Pipelines) dieselbe Logik nutzen.
        """
        import numpy as np
        # Konzepte + Embeddings laden
        concepts = self.run(
            """
            MATCH (:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
            WHERE c.embedding IS NOT NULL
            RETURN c.concept_id AS concept_id, c.name AS name, c.embedding AS emb
            ORDER BY name
            """,
            {"topic": topic_name},
        )

        if not concepts:
            return {"clusters": 0, "assigned": 0, "message": "Keine Konzepte mit Embeddings gefunden."}

        ids = [c["concept_id"] for c in concepts]
        names = [c["name"] for c in concepts]
        vecs = np.array([c["emb"] for c in concepts], dtype=float)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
        vecsN = vecs / norms

        assigned = np.full(len(ids), False)
        clusters = []
        for i in range(len(ids)):
            if assigned[i]:
                continue
            sims = (vecsN[i] @ vecsN.T)
            members = [j for j, s in enumerate(sims) if (s >= float(sim_threshold)) and (not assigned[j])]
            if len(members) >= int(min_cluster_size):
                for j in members:
                    assigned[j] = True
                clusters.append(members)
            else:
                continue

        created = 0
        for mem in clusters:
            cluster_names = [names[j] for j in mem]
            rep = sorted(cluster_names, key=lambda x: (len(x), x.lower()))[0]
            umbrella_id = f"umb-{__import__('uuid').uuid4()}"
            self.run(
                """
                MERGE (t:Topic {name:$topic})
                MERGE (u:Umbrella {umbrella_id:$uid})
                SET u.name=$name, u.size=$size, u.keywords=$keywords
                MERGE (t)-[:HAS_UMBRELLA]->(u)
                """,
                {"topic": topic_name, "uid": umbrella_id, "name": rep, "size": len(mem), "keywords": cluster_names},
            )

            self.run(
                """
                MATCH (u:Umbrella {umbrella_id:$uid})
                UNWIND $concept_ids AS cid
                MATCH (c:Concept {concept_id: cid})
                MERGE (u)-[:NARROWER]->(c)
                """,
                {"uid": umbrella_id, "concept_ids": [ids[j] for j in mem]},
            )
            created += 1

        return {"clusters": len(clusters), "umbrellas_created": created, "assigned": int(np.sum(assigned))}
