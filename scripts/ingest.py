# scripts/ingest.py
# -*- coding: utf-8 -*-
"""
Ingest-Skript für GraphRAG + Neo4j AuraDB mit genauer Provenance.
- Liest Text + Bilder aus PDFs
- Erzeugt Embeddings
- Schreibt Paper, Sections, Paragraphs (mit BBox/Order), Figures (mit BBox/Captions) nach Neo4j
- Hängt DOI/URL/File-Hash an den Paper-Knoten

Aufruf:
    python scripts/ingest.py <pdf1> [<pdf2> ...]
"""

from __future__ import annotations
import sys, json, traceback
from pathlib import Path
from typing import Dict, Any, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.concept_extract import extract_and_embed_concepts, extract_and_embed_concepts_hybrid
from src.neo import Neo4jClient
from src.pdf_ingest import (
    read_pdf_text_and_images,  # -> (paper_meta, sections, paragraphs, figures)
    embed_paragraphs,          # -> paragraphs mit "embedding"
    analyze_and_embed_figures  # -> figures mit "caption", "analysis_json", "embedding"
)


def _merge_figure_meta_with_analysis(fig_meta: List[Dict[str, Any]],
                                     fig_analysis: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merged pro figure_id die ursprünglichen Metadaten (bbox, page_width, figure_label, image_path)
    mit der VLM-Analyse (caption, analysis_json, embedding, image_uri).
    """
    by_id: Dict[str, Dict[str, Any]] = {f["figure_id"]: f for f in fig_meta}
    merged: List[Dict[str, Any]] = []
    for fa in fig_analysis:
        base = by_id.get(fa["figure_id"], {})
        # image_uri bevorzugen, ansonsten aus meta (image_path) übernehmen
        image_uri = fa.get("image_uri") or base.get("image_uri") or base.get("image_path")
        merged.append({
            "figure_id":      fa["figure_id"],
            "page":           fa.get("page", base.get("page")),
            "image_uri":      image_uri,
            "image_filename": fa.get("image_filename") or base.get("image_filename"),
            "caption":        fa.get("caption", base.get("figure_label", "")),
            "analysis_json":  fa.get("analysis_json"),

            # Neue Analyse-Felder (aus reichem Embedding)
            "figure_type": fa.get("figure_type", "other"),
            "entities":    fa.get("entities", []),
            "ocr_hints":   fa.get("ocr_hints", []),

            # Embedding aus Analyse
            "embedding": fa.get("embedding"),

            # Provenance-Felder aus Meta
            "bbox":        base.get("bbox"),
            "page_width":  base.get("page_width"),
            "page_height": base.get("page_height"),
            "figure_label": base.get("figure_label"),
        })
    return merged


def ingest_one(pdf_path: Path, neo: Neo4jClient) -> Dict[str, Any]:
    """
    Ingest genau *eines* PDFs. Gibt eine kompakte Zusammenfassung zurück.
    Erwartet, dass `read_pdf_text_and_images` vier Werte liefert:
      paper_meta, sections, paragraphs, figures
    """
    paper_meta, sections, paragraphs, figures = read_pdf_text_and_images(str(pdf_path))

    # --- Paper upsert inkl. DOI/URL/File-Hash/Metadaten ---
    neo.upsert_paper(
        paper_meta["paper_id"],
        paper_meta.get("title"),
        {
            "source_path": paper_meta["source_path"],
            "doi": paper_meta.get("doi"),
            "url": paper_meta.get("url"),
            "file_sha256": paper_meta.get("file_sha256"),
            **paper_meta.get("meta", {}),
        },
    )

    # --- Sections (falls vorhanden) ---
    if sections:
        neo.add_sections(paper_meta["paper_id"], sections)

    # --- Paragraphs: Embedding + Schreiben ---
    paragraphs_emb = embed_paragraphs(paragraphs)
    neo.add_paragraphs(paper_meta["paper_id"], paragraphs_emb)

    # --- Concepts: aus Absätzen extrahieren, einfügen, verlinken ---
    # Option 1: Legacy-Extraktion (nur LLM)
    # concepts, links = extract_and_embed_concepts(
    #     paper_title=paper_meta.get("title") or "",
    #     paragraphs=paragraphs_emb,
    #     topic_hint="Künstliche Intelligenz",
    #     max_concepts=30,
    #     neo_client=neo,
    #     persist_to_topic=True
    # )
    
    # Option 2: Hybrid-Extraktion (NER + LLM + Relationen) - EMPFOHLEN
    full_text = "\n\n".join([p.get("text", "") for p in paragraphs_emb])
    extraction_result = extract_and_embed_concepts_hybrid(
        paper_title=paper_meta.get("title") or "",
        paper_text=full_text,
        paragraphs=paragraphs_emb,
        topic_hint="Künstliche Intelligenz",
        max_entities=30,
        max_relations=20,
        use_scispacy=True,
        neo_client=neo,
        persist_to_topic=True
    )
    concepts = extraction_result["concepts"]
    links = extraction_result["paragraph_links"]
    relations = extraction_result["relations"]
    
    print(f"  Extracted: {len(concepts)} concepts, {len(relations)} relations")
    
    # Verknüpfe Paragraphen mit Konzepten
    if links:
        neo.link_paragraphs_to_concepts(paper_meta["paper_id"], links)

    # --- Figures: VLM-Analyse + Embedding, dann Mergen mit Meta & Schreiben ---
    figs_analysed = analyze_and_embed_figures(figures) if figures else []
    figs_ready = _merge_figure_meta_with_analysis(figures, figs_analysed) if figures else []
    if figs_ready:
        neo.add_figures(paper_meta["paper_id"], figs_ready)
        # Figure → Concept Verlinkung basierend auf extrahierten Entitäten
        try:
            fig_concept_stats = neo.link_figures_to_concepts(figs_ready)
            print(f"  Figure→Concept links: {fig_concept_stats.get('linked', 0)} created "
                  f"(of {fig_concept_stats.get('attempted', 0)} attempted)")
        except Exception as e:
            print(f"  ⚠ Figure→Concept linking failed (non-fatal): {e}")

    return {
        "paper_id": paper_meta["paper_id"],
        "title": paper_meta.get("title"),
        "doi": paper_meta.get("doi"),
        "url": paper_meta.get("url"),
        "file_sha256": paper_meta.get("file_sha256"),
        "n_sections": len(sections),
        "n_paragraphs": len(paragraphs_emb),
        "n_figures": len(figs_ready),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest.py <pdf1> [<pdf2> ...]")
        sys.exit(1)

    pdfs = [Path(p) for p in sys.argv[1:]]

    neo = Neo4jClient()
    reports: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    try:
        for p in pdfs:
            if not p.exists():
                err = {"file": str(p), "error": "File not found"}
                errors.append(err)
                print(f"❌ {err['file']}: {err['error']}")
                continue
            try:
                report = ingest_one(p, neo)
                reports.append(report)
                print(f"✅ Ingested: {report['title']} "
                      f"(sections={report['n_sections']}, paras={report['n_paragraphs']}, figs={report['n_figures']})")
            except Exception as e:
                tb = traceback.format_exc()
                err = {"file": str(p), "error": str(e), "traceback": tb}
                errors.append(err)
                print(f"❌ Error while ingesting {p.name}: {e}\n{tb}")

        # Auto-stitch graph structure after ingest (Sections ↔ Paragraphs/Figures)
        try:
            print("\n🧵 Auto-stitching document hierarchy (Sections ↔ Paragraphs/Figures)...")
            stats1 = neo.stitch_document_hierarchy()
            print("  ✓ Document hierarchy stitched")
            print(f"  Paragraphs via Section: {stats1.get('paras_via_section')}")
            print(f"  Figures via Section:    {stats1.get('figs_via_section')}")
        except Exception as e:
            print(f"  ⚠ Failed to stitch document hierarchy: {e}")

        # Auto-stitch figures to paragraphs (CAPTIONS/REFERS_TO/NEAR)
        try:
            print("\n🧵 Auto-stitching figures to paragraphs (CAPTIONS/REFERS_TO/NEAR)...")
            stats2 = neo.stitch_figures_to_paragraphs(prefix_length=60, page_tolerance=1)
            print("  ✓ Figures stitched to paragraphs")
            print(f"  CAPTIONS: {stats2.get('captions_after', stats2.get('captions_before'))}")
            print(f"  REFERS_TO: {stats2.get('refers_after', stats2.get('refers_before'))}")
            print(f"  NEAR: {stats2.get('near_after', stats2.get('near_before'))}")
        except Exception as e:
            print(f"  ⚠ Failed to stitch figures to paragraphs: {e}")
    finally:
        neo.close()

    print("\n=== SUMMARY ===")
    print(json.dumps({"ok": reports, "errors": errors}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
