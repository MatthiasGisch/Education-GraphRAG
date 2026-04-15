# src/neo.py
from __future__ import annotations
from typing import Any, Dict, List
from neo4j import GraphDatabase
from .config import NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD


class Neo4jClient:
    def __init__(self) -> None:
        if not (NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD):
            raise RuntimeError("Neo4j credentials missing. Check .env")
        self.driver = GraphDatabase.driver(
            NEO4J_URI, 
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
            max_connection_lifetime=3600,  # 1 Stunde
            max_connection_pool_size=50,
            connection_acquisition_timeout=120,  # 2 Minuten
            connection_timeout=30,  # 30 Sekunden für initiale Verbindung
            keep_alive=True
        )

    def close(self) -> None:
        self.driver.close()
    
    def verify_connectivity(self) -> bool:
        """Prüfe ob Verbindung noch aktiv ist."""
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    def run(self, cypher: str, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        """Führt Cypher Query aus mit Retry-Logik bei Connection Fehlern."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                with self.driver.session() as session:
                    # Setze Transaction Timeout auf 5 Minuten
                    result = session.run(cypher, params or {}, timeout=300)
                    return [r.data() for r in result]
            except Exception as e:
                error_msg = str(e).lower()
                # Bei Connection-Problemen: Retry
                if attempt < max_retries - 1 and ('defunct' in error_msg or 'connection' in error_msg):
                    print(f"⚠️  Neo4j connection issue, retrying ({attempt + 1}/{max_retries})...")
                    try:
                        self.driver.verify_connectivity()
                    except:
                        pass  # Verbindung ist tot, aber Driver wird bei nächstem Versuch neue Session erstellen
                    continue
                # Andernfalls: Exception durchreichen
                raise
    
    def run_graph(self, cypher: str, params: Dict[str, Any] | None = None) -> List[Any]:
        """
        Führt Cypher-Query aus und gibt die rohen Neo4j-Records zurück (inkl. Nodes, Paths, Relationships).
        Wichtig für Graph-Visualisierung!
        """
        with self.driver.session() as session:
            result = session.run(cypher, params or {})
            records = []
            for r in result:
                # Jeder Record kann mehrere Felder haben (z.B. p1, p2, p3)
                # Wir wollen alle Felder behalten
                record_dict = {}
                for key in r.keys():
                    record_dict[key] = r[key]
                records.append(record_dict)
            return records

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
            SET p += $meta,
                p.title = coalesce($title, p.title)
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
        """Fügt Paragraphen in Batches hinzu, um Timeouts zu vermeiden."""
        batch_size = 25  # Konservative Batch-Größe (jeder Paragraph hat 1536-dim Embedding!)
        total_batches = (len(paragraphs) - 1) // batch_size + 1
        
        print(f"  Inserting {len(paragraphs)} paragraphs in {total_batches} batches...")
        
        for i in range(0, len(paragraphs), batch_size):
            batch = paragraphs[i:i+batch_size]
            batch_num = i // batch_size + 1
            
            try:
                self.run(
                    """
                    UNWIND $paragraphs AS row
                    MATCH (p:Paper {paper_id:$paper_id})
                    MERGE (para:Paragraph {paragraph_id: row.paragraph_id})
                    SET para.text = row.text,
                        para.paper_id = $paper_id,
                        para.page = row.page,
                        para.order_in_page = row.order_in_page,
                        para.char_start = row.char_start,
                        para.char_end = row.char_end,
                        para.bbox = row.bbox,
                        para.page_width = row.page_width,
                        para.page_height = row.page_height,
                        para.sha256 = row.sha256,
                        para.embedding = row.embedding
                    MERGE (p)-[:HAS_PARAGRAPH]->(para)
                    WITH p, para, row
                    OPTIONAL MATCH (sec:Section {section_id: row.section_id_ref})
                    FOREACH (_ IN CASE WHEN sec IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (sec)-[:HAS_PARAGRAPH]->(para)
                    )
                    """,
                    {"paper_id": paper_id, "paragraphs": batch},
                )
                print(f"    ✓ Batch {batch_num}/{total_batches} ({len(batch)} paragraphs)")
            except Exception as e:
                print(f"    ✗ Batch {batch_num}/{total_batches} failed: {e}")
                raise

    def add_figures(self, paper_id: str, figures: list[dict]) -> None:
        """Fügt Figuren in Batches hinzu, um Timeouts zu vermeiden."""
        batch_size = 10  # Konservativ, da Figuren große Embeddings haben
        total_batches = (len(figures) - 1) // batch_size + 1
        
        if total_batches > 1:
            print(f"  Inserting {len(figures)} figures in {total_batches} batches...")
        
        for i in range(0, len(figures), batch_size):
            batch = figures[i:i+batch_size]
            batch_num = i // batch_size + 1
            
            try:
                self.run(
                    """
                    UNWIND $figures AS row
                    MATCH (p:Paper {paper_id:$paper_id})
                    MERGE (f:Figure {figure_id: row.figure_id})
                    SET f.caption       = row.caption,
                        f.page          = row.page,
                        f.image_uri     = row.image_uri,
                        f.image_filename = coalesce(row.image_filename, ''),
                        f.image_path    = coalesce(row.image_path, row.image_uri),
                        f.analysis_json = row.analysis_json,
                        f.embedding     = row.embedding,
                        f.bbox          = row.bbox,
                        f.page_width    = row.page_width,
                        f.page_height   = row.page_height,
                        f.figure_label  = row.figure_label,
                        f.figure_type   = coalesce(row.figure_type, 'other'),
                        f.entities      = coalesce(row.entities, []),
                        f.ocr_hints     = coalesce(row.ocr_hints, [])
                    MERGE (p)-[:HAS_FIGURE]->(f)
                    """,
                    {"paper_id": paper_id, "figures": batch},
                )
                if total_batches > 1:
                    print(f"    ✓ Batch {batch_num}/{total_batches} ({len(batch)} figures)")
            except Exception as e:
                print(f"    ✗ Figure batch {batch_num}/{total_batches} failed: {e}")
                raise

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

    # --- Convenience: attach all topic concepts to the (single) umbrella if only one exists and they are unassigned ---
    def attach_concepts_to_existing_umbrella(self, topic_name: str) -> dict:
        """If the topic has exactly one Umbrella and there are concepts without a NARROWER assignment,
        attach them. Returns stats.
        """
        umbrellas = self.run(
            """
            MATCH (t:Topic {name:$topic})-[:HAS_UMBRELLA]->(u:Umbrella)
            RETURN u.umbrella_id AS uid
            """,
            {"topic": topic_name},
        )
        if len(umbrellas) != 1:
            return {"attached": 0, "skipped": "umbrella_count!=1"}
        uid = umbrellas[0]["uid"]
        unassigned = self.run(
            """
            MATCH (t:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
            WHERE NOT ( (:Umbrella)-[:NARROWER]->(c) )
            RETURN c.concept_id AS cid
            """,
            {"topic": topic_name},
        )
        if not unassigned:
            return {"attached": 0, "skipped": "none_unassigned"}
        self.run(
            """
            MATCH (u:Umbrella {umbrella_id:$uid})
            UNWIND $cids AS cid
            MATCH (c:Concept {concept_id: cid})
            MERGE (u)-[:NARROWER]->(c)
            """,
            {"uid": uid, "cids": [r["cid"] for r in unassigned]},
        )
        return {"attached": len(unassigned), "umbrella_id": uid}

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

    def link_figures_to_concepts(self, figures: list[dict]) -> dict:
        """
        Verknüpft Figure-Knoten mit Concept-Knoten basierend auf den
        von GPT-4o extrahierten Entitäten (aus analyze_and_embed_figures).
        Erstellt: Figure -[:MENTIONS {confidence, source}]-> Concept

        Args:
            figures: Liste der gemergten Figure-Dicts (mit 'figure_id' und 'entities')
        Returns:
            {"linked": int, "attempted": int}
        """
        links = []
        for fig in figures:
            fig_id  = fig.get("figure_id")
            entities = fig.get("entities") or []
            if not fig_id or not entities:
                continue
            for entity in entities:
                entity = (entity or "").strip()
                if entity:
                    links.append({"figure_id": fig_id, "entity_name": entity})

        if not links:
            return {"linked": 0, "attempted": 0}

        result = self.run(
            """
            UNWIND $links AS L
            MATCH (f:Figure {figure_id: L.figure_id})
            OPTIONAL MATCH (c:Concept)
              WHERE toLower(c.name) = toLower(L.entity_name)
                 OR L.entity_name IN [al IN coalesce(c.alt_labels, []) | al]
            WITH f, c, L
            WHERE c IS NOT NULL
            MERGE (f)-[r:MENTIONS]->(c)
            SET r.confidence = 0.8,
                r.source     = 'vision_analysis'
            RETURN count(r) AS linked
            """,
            {"links": links},
        )
        linked = result[0].get("linked", 0) if result else 0
        return {"linked": linked, "attempted": len(links)}

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
        MERGE (para)-[r:CAPTIONS]->(f)
        SET r.confidence = 1.0, r.match_type = 'caption_prefix_long'
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
        MERGE (para)-[r:CAPTIONS]->(f)
        SET r.confidence = 0.9, r.match_type = 'caption_prefix_short'
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
        MERGE (para)-[r:REFERS_TO]->(f)
        SET r.confidence = 0.9, r.match_type = 'figure_label_exact'
        """, {"tol": page_tolerance})

        # PASS 2b: REFERS_TO mit Varianten (figure/fig/abb)
        self.run("""
        MATCH (p:Paper)-[:HAS_FIGURE]->(f:Figure)
        WHERE NOT (()-[:REFERS_TO]->(f))
        WITH p, f,
             toLower(coalesce(replace(f.figure_label,'.',''), '')) AS flbl,
             coalesce(f.page,-1) AS fpage
        WHERE flbl =~ '.*\\d+.*'
        WITH p, f, fpage,
             replace(replace(replace(replace(flbl,'figure',''),'fig',''),'abb',''),'  ',' ') AS numish
        MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
        WHERE para.page IN range(fpage-$tol, fpage+$tol)
          AND (
               toLower(para.text) CONTAINS ('figure ' + numish) OR
               toLower(para.text) CONTAINS ('fig ' + numish)    OR
               toLower(para.text) CONTAINS ('abb ' + numish)
          )
        MERGE (para)-[r:REFERS_TO]->(f)
        SET r.confidence = 0.8, r.match_type = 'figure_label_variant'
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
        MERGE (best)-[r:NEAR]->(f)
        SET r.confidence = 0.5, r.match_type = 'spatial_proximity'
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
            # Generate descriptive umbrella name using LLM
            rep = self._generate_umbrella_name(cluster_names)
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

    def _generate_umbrella_name(self, concept_names: list[str]) -> str:
        """Generate a descriptive umbrella term from concept names using LLM."""
        from openai import OpenAI
        client = OpenAI()
        
        if len(concept_names) <= 2:
            # For small clusters, just use the shortest name
            return sorted(concept_names, key=lambda x: (len(x), x.lower()))[0]
        
        try:
            prompt = (
                f"Given these related concepts: {', '.join(concept_names[:15])}\n\n"
                f"Generate a single, concise umbrella term (2-4 words max) that best represents all of them. "
                f"Return ONLY the umbrella term, nothing else."
            )
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=20
            )
            umbrella = resp.choices[0].message.content.strip().strip('"').strip("'")
            # Fallback if LLM returns something weird
            if len(umbrella) > 50 or not umbrella:
                return sorted(concept_names, key=lambda x: (len(x), x.lower()))[0]
            return umbrella
        except Exception:
            # Fallback to shortest name on error
            return sorted(concept_names, key=lambda x: (len(x), x.lower()))[0]

    # --- Umbrella Management Functions ---
    
    def list_umbrellas_for_topic(self, topic_name: str) -> list[dict]:
        """List all umbrellas for a topic with their concepts."""
        return self.run(
            """
            MATCH (t:Topic {name:$topic})-[:HAS_UMBRELLA]->(u:Umbrella)
            OPTIONAL MATCH (u)-[:NARROWER]->(c:Concept)
            WITH u, collect(c.name) AS concepts
            RETURN u.umbrella_id AS umbrella_id,
                   u.name AS name,
                   u.size AS size,
                   u.keywords AS keywords,
                   concepts,
                   size(concepts) AS concept_count
            ORDER BY u.name
            """,
            {"topic": topic_name}
        )
    
    def rename_umbrella(self, umbrella_id: str, new_name: str) -> dict:
        """Rename an umbrella."""
        self.run(
            """
            MATCH (u:Umbrella {umbrella_id:$uid})
            SET u.name = $name
            """,
            {"uid": umbrella_id, "name": new_name}
        )
        return {"umbrella_id": umbrella_id, "new_name": new_name}
    
    def delete_umbrella(self, umbrella_id: str, reassign_to: str | None = None) -> dict:
        """Delete an umbrella. If reassign_to is provided, move concepts to that umbrella."""
        if reassign_to:
            # Move concepts to another umbrella
            self.run(
                """
                MATCH (u1:Umbrella {umbrella_id:$from})-[r:NARROWER]->(c:Concept)
                MATCH (u2:Umbrella {umbrella_id:$to})
                DELETE r
                MERGE (u2)-[:NARROWER]->(c)
                """,
                {"from": umbrella_id, "to": reassign_to}
            )
        
        # Delete umbrella and its relationships
        result = self.run(
            """
            MATCH (u:Umbrella {umbrella_id:$uid})
            OPTIONAL MATCH (u)-[r]-()
            DELETE r, u
            RETURN count(u) AS deleted
            """,
            {"uid": umbrella_id}
        )
        return {"deleted": result[0]["deleted"] if result else 0, "reassigned": bool(reassign_to)}
    
    def merge_umbrellas(self, umbrella_ids: list[str], new_name: str | None = None) -> dict:
        """Merge multiple umbrellas into the first one, optionally renaming it."""
        if len(umbrella_ids) < 2:
            return {"error": "Need at least 2 umbrellas to merge"}
        
        target = umbrella_ids[0]
        sources = umbrella_ids[1:]
        
        # Move all concepts from source umbrellas to target
        for source in sources:
            self.run(
                """
                MATCH (source:Umbrella {umbrella_id:$source})-[r:NARROWER]->(c:Concept)
                MATCH (target:Umbrella {umbrella_id:$target})
                DELETE r
                MERGE (target)-[:NARROWER]->(c)
                """,
                {"source": source, "target": target}
            )
            # Delete source umbrella
            self.run(
                """
                MATCH (u:Umbrella {umbrella_id:$uid})
                OPTIONAL MATCH (u)-[r]-()
                DELETE r, u
                """,
                {"uid": source}
            )
        
        # Update target umbrella name and size
        size_update = self.run(
            """
            MATCH (u:Umbrella {umbrella_id:$uid})-[:NARROWER]->(c:Concept)
            WITH u, collect(c.name) AS concept_names
            SET u.size = size(concept_names),
                u.keywords = concept_names
            """ + (f"SET u.name = $new_name " if new_name else "") + """
            RETURN u.name AS name, u.size AS size
            """,
            {"uid": target, "new_name": new_name} if new_name else {"uid": target}
        )
        
        return {
            "target_id": target,
            "merged_count": len(sources),
            "new_size": size_update[0]["size"] if size_update else 0,
            "new_name": size_update[0]["name"] if size_update else None
        }

    # --- Concept Management Functions ---
    
    def list_concepts_for_topic(self, topic_name: str) -> list[dict]:
        """List all concepts for a topic with their metadata."""
        return self.run(
            """
            MATCH (t:Topic {name:$topic})-[:HAS_CONCEPT]->(c:Concept)
            OPTIONAL MATCH (u:Umbrella)-[:NARROWER]->(c)
            OPTIONAL MATCH (c)<-[m:MENTIONS]-(p:Paragraph)
            WITH c, u.name AS umbrella, count(DISTINCT p) AS mentions
            RETURN c.concept_id AS concept_id,
                   c.name AS name,
                   c.alt_labels AS alt_labels,
                   c.description AS description,
                   umbrella,
                   mentions
            ORDER BY c.name
            """,
            {"topic": topic_name}
        )
    
    def update_concept(self, concept_id: str, name: str | None = None, 
                      alt_labels: list[str] | None = None, description: str | None = None) -> dict:
        """Update concept metadata."""
        updates = []
        params = {"cid": concept_id}
        
        if name is not None:
            updates.append("c.name = $name")
            params["name"] = name
        if alt_labels is not None:
            updates.append("c.alt_labels = $alt_labels")
            params["alt_labels"] = alt_labels
        if description is not None:
            updates.append("c.description = $description")
            params["description"] = description
        
        if not updates:
            return {"error": "No updates provided"}
        
        self.run(
            f"""
            MATCH (c:Concept {{concept_id:$cid}})
            SET {', '.join(updates)}
            """,
            params
        )
        return {"concept_id": concept_id, "updated_fields": list(params.keys())}
    
    def delete_concept(self, concept_id: str) -> dict:
        """Delete a concept and all its relationships."""
        result = self.run(
            """
            MATCH (c:Concept {concept_id:$cid})
            OPTIONAL MATCH (c)-[r]-()
            DELETE r, c
            RETURN count(c) AS deleted
            """,
            {"cid": concept_id}
        )
        return {"deleted": result[0]["deleted"] if result else 0}
    
    def merge_concepts(self, source_id: str, target_id: str) -> dict:
        """Merge source concept into target concept."""
        # Get source info for potential alt_labels update
        source_info = self.run(
            """
            MATCH (s:Concept {concept_id:$sid})
            RETURN s.name AS name, s.alt_labels AS alt_labels
            """,
            {"sid": source_id}
        )
        
        if not source_info:
            return {"error": "Source concept not found"}
        
        # Move all MENTIONS relationships from source to target
        self.run(
            """
            MATCH (s:Concept {concept_id:$sid})<-[r:MENTIONS]-(p:Paragraph)
            MATCH (t:Concept {concept_id:$tid})
            DELETE r
            MERGE (p)-[:MENTIONS]->(t)
            """,
            {"sid": source_id, "tid": target_id}
        )
        
        # Add source name to target's alt_labels if not already there
        source_name = source_info[0]["name"]
        self.run(
            """
            MATCH (t:Concept {concept_id:$tid})
            SET t.alt_labels = coalesce(t.alt_labels, []) + 
                CASE WHEN NOT $source_name IN coalesce(t.alt_labels, []) 
                     THEN [$source_name] 
                     ELSE [] 
                END
            """,
            {"tid": target_id, "source_name": source_name}
        )
        
        # Delete source concept
        self.run(
            """
            MATCH (c:Concept {concept_id:$cid})
            OPTIONAL MATCH (c)-[r]-()
            DELETE r, c
            """,
            {"cid": source_id}
        )
        
        return {"merged": True, "source_id": source_id, "target_id": target_id}

    # =============================================================================
    # SEMANTIC RELATIONS (NEW)
    # =============================================================================

    def add_semantic_relations(self, paper_id: str, relations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Add semantic relations extracted from a paper.
        
        Args:
            paper_id: The paper ID these relations belong to
            relations: List of relation dicts with structure:
                {
                    "subject": str,  # Entity name
                    "predicate": str,  # Relation type (IS_A, PART_OF, etc.)
                    "object": str,  # Entity name
                    "confidence": float,  # 0.0-1.0
                    "context": str,  # Sentence/phrase where relation appears
                    "source": str  # "llm" or "cooccurrence"
                }
        
        Returns:
            Statistics about relations created/updated
        """
        import logging
        log = logging.getLogger(__name__)
        
        if not relations:
            return {"created": 0, "updated": 0, "skipped": 0}
        
        stats = {"created": 0, "updated": 0, "skipped": 0}
        
        # Process each relation
        for rel in relations:
            try:
                subject = rel.get("subject")
                predicate = rel.get("predicate", "RELATED_TO")
                obj = rel.get("object")
                confidence = rel.get("confidence", 0.7)
                context = rel.get("context", "")
                source = rel.get("source", "llm")
                
                if not subject or not obj:
                    stats["skipped"] += 1
                    continue
                
                # Create concept IDs (same logic as in concept_extract.py)
                subject_id = self._concept_slug(subject)
                object_id = self._concept_slug(obj)
                
                # Ensure both concepts exist (merge will create if not exists)
                result = self.run(
                    """
                    MERGE (s:Concept {concept_id: $subject_id})
                    ON CREATE SET s.name = $subject
                    MERGE (o:Concept {concept_id: $object_id})
                    ON CREATE SET o.name = $object
                    
                    // Check if relation exists
                    OPTIONAL MATCH (s)-[r:SEMANTIC_RELATION {relation_type: $predicate}]->(o)
                    
                    // Create or update relation
                    WITH s, o, r,
                         CASE WHEN r IS NULL THEN 'created' ELSE 'updated' END AS operation
                    MERGE (s)-[rel:SEMANTIC_RELATION {relation_type: $predicate}]->(o)
                    SET rel.confidence = $confidence,
                        rel.context = $context,
                        rel.source = $source,
                        rel.paper_id = $paper_id,
                        rel.updated_at = datetime()
                    ON CREATE SET rel.created_at = datetime()
                    
                    RETURN operation
                    """,
                    {
                        "subject_id": subject_id,
                        "subject": subject,
                        "object_id": object_id,
                        "object": obj,
                        "predicate": predicate,
                        "confidence": confidence,
                        "context": context,
                        "source": source,
                        "paper_id": paper_id
                    }
                )
                
                if result and result[0].get("operation") == "created":
                    stats["created"] += 1
                else:
                    stats["updated"] += 1
                    
            except Exception as e:
                log.warning(f"Failed to add relation {rel}: {e}")
                stats["skipped"] += 1
                continue
        
        return stats

    def add_cooccurrence_relations(self, paper_id: str, cooccurrences: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Add co-occurrence relations between concepts.
        
        Args:
            paper_id: The paper ID these relations belong to
            cooccurrences: List of dicts with:
                {
                    "concept1": str,
                    "concept2": str,
                    "count": int,
                    "strength": float  # normalized 0.0-1.0
                }
        
        Returns:
            Statistics about relations created
        """
        import logging
        log = logging.getLogger(__name__)
        
        if not cooccurrences:
            return {"created": 0, "updated": 0}
        
        stats = {"created": 0, "updated": 0}
        
        for cooc in cooccurrences:
            try:
                c1 = cooc.get("concept1")
                c2 = cooc.get("concept2")
                count = cooc.get("count", 1)
                strength = cooc.get("strength", 0.5)
                
                if not c1 or not c2:
                    continue
                
                c1_id = self._concept_slug(c1)
                c2_id = self._concept_slug(c2)
                
                result = self.run(
                    """
                    MERGE (c1:Concept {concept_id: $c1_id})
                    ON CREATE SET c1.name = $c1
                    MERGE (c2:Concept {concept_id: $c2_id})
                    ON CREATE SET c2.name = $c2
                    
                    // Create bidirectional co-occurrence (undirected)
                    OPTIONAL MATCH (c1)-[r:CO_OCCURS_WITH]-(c2)
                    
                    WITH c1, c2, r,
                         CASE WHEN r IS NULL THEN 'created' ELSE 'updated' END AS operation
                    MERGE (c1)-[rel:CO_OCCURS_WITH]-(c2)
                    SET rel.count = coalesce(rel.count, 0) + $count,
                        rel.strength = $strength,
                        rel.paper_id = $paper_id,
                        rel.updated_at = datetime()
                    ON CREATE SET rel.created_at = datetime()
                    
                    RETURN operation
                    """,
                    {
                        "c1_id": c1_id,
                        "c1": c1,
                        "c2_id": c2_id,
                        "c2": c2,
                        "count": count,
                        "strength": strength,
                        "paper_id": paper_id
                    }
                )
                
                if result and result[0].get("operation") == "created":
                    stats["created"] += 1
                else:
                    stats["updated"] += 1
                    
            except Exception as e:
                log.warning(f"Failed to add co-occurrence {cooc}: {e}")
                continue
        
        return stats

    def get_concept_relations(self, concept_id: str, relation_types: List[str] = None, 
                             min_confidence: float = 0.0) -> Dict[str, Any]:
        """
        Get all relations for a concept.
        
        Args:
            concept_id: The concept ID
            relation_types: Optional list of relation types to filter (e.g., ["IS_A", "PART_OF"])
            min_confidence: Minimum confidence threshold
        
        Returns:
            Dict with outgoing and incoming relations
        """
        type_filter = ""
        if relation_types:
            types_str = ", ".join([f"'{t}'" for t in relation_types])
            type_filter = f"AND r.relation_type IN [{types_str}]"
        
        # Outgoing semantic relations
        outgoing = self.run(
            f"""
            MATCH (c:Concept {{concept_id: $cid}})-[r:SEMANTIC_RELATION]->(target:Concept)
            WHERE r.confidence >= $min_conf {type_filter}
            RETURN r.relation_type AS relation_type,
                   target.concept_id AS target_id,
                   target.name AS target_name,
                   r.confidence AS confidence,
                   r.context AS context,
                   r.source AS source
            ORDER BY r.confidence DESC
            """,
            {"cid": concept_id, "min_conf": min_confidence}
        )
        
        # Incoming semantic relations
        incoming = self.run(
            f"""
            MATCH (source:Concept)-[r:SEMANTIC_RELATION]->(c:Concept {{concept_id: $cid}})
            WHERE r.confidence >= $min_conf {type_filter}
            RETURN r.relation_type AS relation_type,
                   source.concept_id AS source_id,
                   source.name AS source_name,
                   r.confidence AS confidence,
                   r.context AS context,
                   r.source AS source
            ORDER BY r.confidence DESC
            """,
            {"cid": concept_id, "min_conf": min_confidence}
        )
        
        # Co-occurrence relations
        cooccurrences = self.run(
            """
            MATCH (c:Concept {concept_id: $cid})-[r:CO_OCCURS_WITH]-(other:Concept)
            RETURN other.concept_id AS other_id,
                   other.name AS other_name,
                   r.count AS count,
                   r.strength AS strength
            ORDER BY r.strength DESC
            LIMIT 20
            """,
            {"cid": concept_id}
        )
        
        return {
            "outgoing": outgoing,
            "incoming": incoming,
            "cooccurrences": cooccurrences
        }

    def _concept_slug(self, name: str) -> str:
        """Create a slug for concept ID (same logic as in concept_extract.py)."""
        import re
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = slug[:100]
        return slug

