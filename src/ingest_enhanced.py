# src/ingest_enhanced.py
"""
Enhanced Ingest Module with:
- Dynamic parameter adjustment
- Duplicate detection
- Enhanced metadata extraction
- Quality validation
- Batch processing support
"""
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import hashlib
import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# Type checking imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .neo import Neo4jClient


def calculate_dynamic_parameters(num_paragraphs: int) -> Dict[str, int]:
    """
    Calculate optimal extraction parameters based on document size.
    
    Args:
        num_paragraphs: Number of paragraphs in document
        
    Returns:
        Dict with max_entities, max_relations, k_paragraphs, k_figures
    """
    # Base parameters on document size
    max_entities = min(50, max(20, num_paragraphs // 10))
    max_relations = min(30, max(10, num_paragraphs // 15))
    k_paragraphs = min(40, max(15, num_paragraphs // 5))
    k_figures = min(12, max(5, num_paragraphs // 20))
    
    return {
        "max_entities": max_entities,
        "max_relations": max_relations,
        "k_paragraphs": k_paragraphs,
        "k_figures": k_figures
    }


def infer_topic_from_title(title: str) -> str:
    """
    Attempt to infer topic from paper title.
    
    Args:
        title: Paper title
        
    Returns:
        Inferred topic or default
    """
    title_lower = title.lower()
    
    # Topic keywords mapping
    topic_keywords = {
        "Künstliche Intelligenz": ["artificial intelligence", "ai", "machine learning", "ml", "deep learning", "neural network", "nlp", "computer vision"],
        "Medizin": ["medical", "medicine", "clinical", "patient", "disease", "diagnosis", "therapy", "health"],
        "Physik": ["physics", "quantum", "particle", "relativity", "mechanics", "thermodynamics"],
        "Chemie": ["chemistry", "chemical", "molecule", "reaction", "synthesis", "compound"],
        "Biologie": ["biology", "biological", "cell", "gene", "protein", "organism", "evolution"],
        "Informatik": ["computer science", "programming", "algorithm", "software", "database", "network"],
        "Mathematik": ["mathematics", "mathematical", "theorem", "proof", "equation", "geometry"],
        "Ingenieurwesen": ["engineering", "design", "construction", "mechanical", "electrical", "system"]
    }
    
    for topic, keywords in topic_keywords.items():
        if any(kw in title_lower for kw in keywords):
            return topic
    
    return "Künstliche Intelligenz"  # Default fallback


def check_for_duplicates(neo: Neo4jClient, paper_meta: dict) -> Optional[Dict[str, Any]]:
    """
    Check for duplicate papers using multiple strategies.
    
    Args:
        neo: Neo4j client
        paper_meta: Paper metadata dict
        
    Returns:
        Dict with duplicate info if found, None otherwise
    """
    # Strategy 1: SHA256 hash match
    file_hash = paper_meta.get("file_sha256")
    if file_hash:
        query = """
        MATCH (p:Paper {file_sha256: $hash})
        RETURN p.paper_id AS paper_id, p.title AS title
        LIMIT 1
        """
        result = neo.run(query, {"hash": file_hash})
        if result:
            return {
                "type": "exact_hash",
                "paper_id": result[0]["paper_id"],
                "title": result[0]["title"],
                "message": f"Exaktes Duplikat (SHA256) gefunden: {result[0]['title']}"
            }
    
    # Strategy 2: DOI match
    doi = paper_meta.get("doi")
    if doi:
        query = """
        MATCH (p:Paper {doi: $doi})
        RETURN p.paper_id AS paper_id, p.title AS title
        LIMIT 1
        """
        result = neo.run(query, {"doi": doi})
        if result:
            return {
                "type": "doi",
                "paper_id": result[0]["paper_id"],
                "title": result[0]["title"],
                "message": f"Duplikat via DOI gefunden: {result[0]['title']}"
            }
    
    # Strategy 3: Title similarity (exact match for now, could use embeddings)
    title = paper_meta.get("title", "").strip()
    if title:
        query = """
        MATCH (p:Paper)
        WHERE toLower(p.title) = toLower($title)
        RETURN p.paper_id AS paper_id, p.title AS title
        LIMIT 1
        """
        result = neo.run(query, {"title": title})
        if result:
            return {
                "type": "title_exact",
                "paper_id": result[0]["paper_id"],
                "title": result[0]["title"],
                "message": f"Duplikat via Titel gefunden: {result[0]['title']}"
            }
    
    return None


def extract_enhanced_metadata(pdf_path: Path, paper_meta: dict) -> dict:
    """
    Extract enhanced metadata from PDF.
    
    Args:
        pdf_path: Path to PDF file
        paper_meta: Basic metadata from read_pdf_text_and_images
        
    Returns:
        Enhanced metadata dict
    """
    enhanced = paper_meta.copy()
    
    # File statistics
    enhanced["file_size"] = pdf_path.stat().st_size
    enhanced["ingested_at"] = datetime.now().isoformat()
    
    # Try to extract additional metadata from PDF
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        
        metadata = doc.metadata
        if metadata:
            enhanced["pdf_author"] = metadata.get("author", "")
            enhanced["pdf_subject"] = metadata.get("subject", "")
            enhanced["pdf_keywords"] = metadata.get("keywords", "")
            enhanced["pdf_creator"] = metadata.get("creator", "")
            enhanced["pdf_producer"] = metadata.get("producer", "")
            enhanced["pdf_creation_date"] = metadata.get("creationDate", "")
            enhanced["pdf_mod_date"] = metadata.get("modDate", "")
        
        enhanced["page_count"] = len(doc)
        doc.close()
        
    except Exception as e:
        log.warning(f"Could not extract PDF metadata: {e}")
    
    return enhanced


def filter_low_quality_concepts(concepts: List[dict], min_confidence: float = 0.6, 
                                 links: List[dict] = None) -> Tuple[List[dict], List[dict]]:
    """
    Filter out low-quality concepts based on confidence scores.
    
    Args:
        concepts: List of concept dicts
        min_confidence: Minimum average confidence threshold
        links: List of paragraph-concept links
        
    Returns:
        Tuple of (filtered_concepts, filtered_links)
    """
    if not links:
        return concepts, links or []
    
    # Calculate average confidence per concept
    concept_confidence = {}
    for link in links:
        cid = link.get("concept_id") or link.get("concept_name")
        conf = link.get("confidence", 1.0)
        
        if cid not in concept_confidence:
            concept_confidence[cid] = []
        concept_confidence[cid].append(conf)
    
    # Filter concepts
    filtered_concepts = []
    for concept in concepts:
        cid = concept.get("concept_id") or concept.get("name")
        if cid in concept_confidence:
            avg_conf = sum(concept_confidence[cid]) / len(concept_confidence[cid])
            if avg_conf >= min_confidence:
                filtered_concepts.append(concept)
        else:
            # Keep concepts without links (might be important)
            filtered_concepts.append(concept)
    
    # Filter links
    filtered_concept_ids = {c.get("concept_id") or c.get("name") for c in filtered_concepts}
    filtered_links = [
        link for link in links 
        if (link.get("concept_id") or link.get("concept_name")) in filtered_concept_ids
    ]
    
    log.info(f"Quality filter: {len(concepts)} -> {len(filtered_concepts)} concepts, "
             f"{len(links)} -> {len(filtered_links)} links")
    
    return filtered_concepts, filtered_links


def validate_ingestion_quality(report: dict) -> Dict[str, Any]:
    """
    Validate ingestion quality and provide recommendations.
    
    Args:
        report: Ingestion report dict
        
    Returns:
        Validation results with quality score and issues
    """
    issues = []
    warnings = []
    recommendations = []
    
    n_paragraphs = report.get("n_paragraphs", 0)
    n_concepts = report.get("n_concepts", 0)
    n_figures = report.get("n_figures", 0)
    n_links = report.get("n_links", 0)
    
    # Check for critical issues
    if n_concepts == 0:
        issues.append("⚠️ Keine Konzepte extrahiert")
        recommendations.append("Dokument könnte zu kurz oder inhaltlich ungeeignet sein")
    
    if n_paragraphs < 5:
        issues.append("⚠️ Sehr wenige Paragraphen")
        recommendations.append("PDF-Extraktion könnte fehlgeschlagen sein")
    
    if n_paragraphs > 20 and n_concepts < 3:
        warnings.append("ℹ️ Wenige Konzepte für Dokumentgröße")
        recommendations.append("Topic-Hint anpassen oder max_entities erhöhen")
    
    if n_figures == 0 and n_paragraphs > 10:
        warnings.append("ℹ️ Keine Abbildungen gefunden")
    
    if n_concepts > 0 and n_links == 0:
        issues.append("⚠️ Konzepte nicht mit Paragraphen verknüpft")
        recommendations.append("Linking-Logik prüfen")
    
    # Calculate quality score (0-100)
    score = 100
    score -= len(issues) * 20
    score -= len(warnings) * 5
    
    if n_paragraphs > 0:
        concept_ratio = n_concepts / n_paragraphs
        if concept_ratio < 0.1:
            score -= 10
        elif concept_ratio > 2.0:
            score -= 5
            warnings.append("ℹ️ Sehr viele Konzepte pro Paragraph")
    
    score = max(0, min(100, score))
    
    return {
        "quality_score": score,
        "quality_label": "Excellent" if score >= 90 else "Good" if score >= 70 else "Fair" if score >= 50 else "Poor",
        "issues": issues,
        "warnings": warnings,
        "recommendations": recommendations
    }


def create_ingestion_summary(report: dict, validation: dict) -> str:
    """
    Create a human-readable ingestion summary.
    
    Args:
        report: Ingestion report
        validation: Validation results
        
    Returns:
        Formatted summary string
    """
    summary_lines = [
        f"📄 **{report.get('title', 'Unknown')}**",
        f"",
        f"**Qualität:** {validation['quality_label']} ({validation['quality_score']}/100)",
        f"",
        f"**Statistik:**",
        f"- {report.get('n_paragraphs', 0)} Paragraphen",
        f"- {report.get('n_concepts', 0)} Konzepte",
        f"- {report.get('n_links', 0)} Verknüpfungen",
        f"- {report.get('n_figures', 0)} Abbildungen",
        f"- {report.get('n_sections', 0)} Sections",
    ]
    
    if validation['issues']:
        summary_lines.extend([
            f"",
            f"**Probleme:**",
            *[f"- {issue}" for issue in validation['issues']]
        ])
    
    if validation['warnings']:
        summary_lines.extend([
            f"",
            f"**Hinweise:**",
            *[f"- {warning}" for warning in validation['warnings']]
        ])
    
    if validation['recommendations']:
        summary_lines.extend([
            f"",
            f"**Empfehlungen:**",
            *[f"- {rec}" for rec in validation['recommendations']]
        ])
    
    return "\n".join(summary_lines)
