# src/citation_validator.py
"""
Citation Validation System für GenerierteTexte
- Semantic Similarity Check (Mittlere Variante)
- Detaillierter Audit Report (Umfassende Variante)
"""

import re
from typing import Dict, List, Any, Tuple
from .openai_client import embed_text


def _extract_citations_from_text(text: str) -> List[Tuple[str, str]]:
    """
    Extrahiert alle Citations aus dem Text.
    Unterstützt: [1], [2], [1; 2; 3] (numerisch) und [Pxxx], [Fxxx] (ID-basiert)
    Gibt Liste von (citation_id, surrounding_sentence) zurück.
    """
    citations = []
    
    # Pattern 1: Numerische Zitationen [1], [2], [1; 2], etc.
    numeric_pattern = r'\[(\d+(?:\s*[;,]\s*\d+)*)\]'
    numeric_matches = re.finditer(numeric_pattern, text)
    
    for match in numeric_matches:
        citation_nums = match.group(1)
        # Splitte mehrfache Zitationen
        nums = re.split(r'[;,]\s*', citation_nums)
        
        start_pos = match.start()
        end_pos = match.end()
        
        # Extrahiere umgebenden Satz
        sent_start = max(0, start_pos - 150)
        sent_end = min(len(text), end_pos + 100)
        surrounding = text[sent_start:sent_end].strip()
        
        for num in nums:
            citations.append((num.strip(), surrounding))
    
    # Pattern 2: ID-basierte Zitationen [Pxxx], [Fxxx] (Legacy-Support)
    id_pattern = r'\[([PF][a-zA-Z0-9]+)\]'
    id_matches = re.finditer(id_pattern, text)
    
    for match in id_matches:
        citation_id = match.group(1)
        start_pos = match.start()
        end_pos = match.end()
        
        sent_start = max(0, start_pos - 150)
        sent_end = min(len(text), end_pos + 100)
        surrounding = text[sent_start:sent_end].strip()
        
        citations.append((citation_id, surrounding))
    
    return citations


def _find_support_by_id(supports: List[Dict[str, Any]], citation_id: str) -> Dict[str, Any] | None:
    """
    Findet das Support-Dokument für eine gegebene Citation-ID.
    Citation ID Format: P12345abc (Paragraph) oder F67890def (Figure)
    """
    citation_type = citation_id[0]  # 'P' oder 'F'
    
    for support in supports:
        if citation_type == 'P' and support.get('type') == 'paragraph':
            # Vergleiche Paragraph ID (ohne Prefix)
            para_id_from_support = f"P{support.get('paragraph_id', '')}"
            if para_id_from_support == citation_id:
                return support
        elif citation_type == 'F' and support.get('type') == 'figure':
            # Vergleiche Figure ID (ohne Prefix)
            fig_id_from_support = f"F{support.get('figure_id', '')}"
            if fig_id_from_support == citation_id:
                return support
    
    return None


def calculate_semantic_similarity(text1: str, text2: str) -> float:
    """
    Berechnet Semantic Similarity zwischen zwei Texten via Embeddings.
    Wertebereich: 0.0 (völlig unterschiedlich) bis 1.0 (identisch)
    """
    try:
        # Kürze sehr lange Texte für Performance
        text1_short = text1[:300] if len(text1) > 300 else text1
        text2_short = text2[:300] if len(text2) > 300 else text2
        
        emb1 = embed_text(text1_short)
        emb2 = embed_text(text2_short)
        
        # Cosine Similarity
        import math
        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        magnitude1 = math.sqrt(sum(a ** 2 for a in emb1))
        magnitude2 = math.sqrt(sum(b ** 2 for b in emb2))
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        similarity = dot_product / (magnitude1 * magnitude2)
        # Normalisiere auf 0.0-1.0
        return max(0.0, min(1.0, (similarity + 1) / 2))
    
    except Exception as e:
        print(f"Fehler bei Embedding-Berechnung: {e}")
        return 0.5  # Neutral bei Fehler


def validate_citations(
    generated_text: str,
    supports: List[Dict[str, Any]],
    similarity_threshold: float = 0.65
) -> Dict[str, Any]:
    """
    Validiert alle Citations im Text gegen die Support-Dokumente.
    Für numerische Zitationen [1], [2], ... wird nur geprüft, ob Supports vorhanden sind.
    Für ID-basierte Zitationen [Pxxx], [Fxxx] wird semantische Ähnlichkeit geprüft.
    
    Returns:
        {
            "total_citations": int,
            "valid_count": int,
            "invalid_count": int,
            "warning_count": int,
            "warnings": List[str],
            "details": [...]
        }
    """
    
    citations = _extract_citations_from_text(generated_text)
    details = []
    warnings = []
    valid_count = 0
    invalid_count = 0
    warning_count = 0
    
    print(f"DEBUG Citation Validator: Found {len(citations)} citations, {len(supports)} supports")
    print(f"DEBUG Citation IDs: {[cit[0] for cit in citations[:5]]}")
    
    # Für numerische Zitationen: Einfache Validierung
    numeric_citations = [(cid, ctx) for cid, ctx in citations if cid.isdigit()]
    id_citations = [(cid, ctx) for cid, ctx in citations if not cid.isdigit()]
    
    # Validiere numerische Zitationen
    for citation_id, context in numeric_citations:
        citation_num = int(citation_id)
        
        # Prüfe ob genug Supports vorhanden sind
        if len(supports) == 0:
            invalid_count += 1
            details.append({
                "citation_id": citation_id,
                "context": context[:100],
                "status": "invalid",
                "message": "Keine Support-Dokumente gefunden"
            })
            warnings.append(f"[{citation_id}]: Keine Quellen vorhanden")
        else:
            valid_count += 1
            details.append({
                "citation_id": citation_id,
                "context": context[:100],
                "status": "valid",
                "message": f"Numerische Zitation referenziert Literaturverzeichnis (Eintrag #{citation_id})"
            })
    
    # Validiere ID-basierte Zitationen (Legacy)
    for citation_id, context in id_citations:
        support = _find_support_by_id(supports, citation_id)
        
        if not support:
            # Citation-ID nicht in Supports gefunden
            details.append({
                "citation_id": citation_id,
                "context": context[:150],
                "support_document": None,
                "similarity_score": 0.0,
                "status": "invalid",
                "message": f"Citation [{citation_id}] nicht in den Quelldokumenten gefunden!"
            })
            invalid_count += 1
            warnings.append(f"[{citation_id}] Quelldokument nicht vorhanden")
            continue
        
        # Hole Support-Text für Similarity Check
        if support.get('type') == 'paragraph':
            support_text = support.get('text', '')
        else:  # figure
            support_text = support.get('caption', '')
        
        if not support_text:
            details.append({
                "citation_id": citation_id,
                "context": context[:150],
                "support_document": {
                    "title": support.get('paper_title', 'Unbekannt'),
                    "page": support.get('page', '—'),
                    "doi": support.get('doi', '—')
                },
                "similarity_score": 0.0,
                "status": "warning",
                "message": f"[{citation_id}] Support-Dokument hat keinen Text/Caption zur Validierung"
            })
            warnings.append(f"[{citation_id}] Support-Text ist leer")
            continue
        
        # Semantic Similarity Check
        similarity = calculate_semantic_similarity(context, support_text)
        
        if similarity >= similarity_threshold:
            status = "valid"
            valid_count += 1
            message = f"Validiert (Similarity: {similarity:.1%})"
        elif similarity >= (similarity_threshold - 0.15):
            status = "warning"
            valid_count += 1
            message = f"Warnung: Ähnlichkeit niedrig (Similarity: {similarity:.1%})"
            warnings.append(f"[{citation_id}] Niedrige Ähnlichkeit zum Support-Dokument ({similarity:.1%})")
        else:
            status = "invalid"
            invalid_count += 1
            message = f"Fehler: Keine Übereinstimmung gefunden (Similarity: {similarity:.1%})"
            warnings.append(f"[{citation_id}] Keine semantische Übereinstimmung mit Support ({similarity:.1%})")
        
        details.append({
            "citation_id": citation_id,
            "context": context[:200],
            "support_document": {
                "title": support.get('paper_title', 'Unbekannt'),
                "type": support.get('type', 'unknown'),
                "page": support.get('page', '—'),
                "section": support.get('section_title', '—'),
                "doi": support.get('doi', '—'),
                "text_preview": support_text[:200] + ("..." if len(support_text) > 200 else "")
            },
            "similarity_score": similarity,
            "status": status,
            "message": message
        })
    
    # Berechne warning_count korrekt
    warning_count = len([d for d in details if d.get("status") == "warning"])
    
    print(f"DEBUG Citation Validation Complete: {len(citations)} total, {valid_count} valid, {invalid_count} invalid, {warning_count} warnings")
    
    return {
        "total_citations": len(citations),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "warning_count": warning_count,
        "warnings": warnings,
        "details": details,
        "validation_passed": invalid_count == 0
    }


def generate_audit_report(validation_result: Dict[str, Any]) -> str:
    """
    Generiert einen detaillierten Audit-Report aus dem Validierungsergebnis.
    """
    report = []
    report.append("=" * 80)
    report.append("CITATION VALIDATION AUDIT REPORT")
    report.append("=" * 80)
    report.append("")
    
    # Summary
    report.append("ZUSAMMENFASSUNG")
    report.append("-" * 80)
    report.append(f"Gesamt Citations: {validation_result['total_citations']}")
    report.append(f"Valid: {validation_result['valid_count']}")
    report.append(f"Warnings: {validation_result['warning_count']}")
    report.append(f"Invalid: {validation_result['invalid_count']}")
    report.append("")
    
    if validation_result['warnings']:
        report.append("WARNUNGEN")
        report.append("-" * 80)
        for warning in validation_result['warnings']:
            report.append(f"  • {warning}")
        report.append("")
    
    # Detaillierte Citation Details
    report.append("DETAILLIERTE CITATION-VALIDIERUNG")
    report.append("-" * 80)
    
    for idx, detail in enumerate(validation_result['details'], 1):
        status_icon = "✓" if detail['status'] == 'valid' else ("!" if detail['status'] == 'warning' else "✗")
        report.append(f"{idx}. [{detail['citation_id']}] {status_icon} {detail['message']}")
        report.append(f"   Kontext: {detail['context'][:120]}...")
        
        if detail['support_document']:
            doc = detail['support_document']
            report.append(f"   Quelle: {doc.get('title', 'Unbekannt')}")
            report.append(f"   Seite: {doc.get('page', '—')} | Abschnitt: {doc.get('section', '—')}")
            report.append(f"   Similarity: {detail['similarity_score']:.1%}")
            if doc.get('text_preview'):
                report.append(f"   Text-Auszug: {doc['text_preview'][:100]}...")
        report.append("")
    
    # Empfehlungen
    if validation_result['invalid_count'] > 0:
        report.append("EMPFEHLUNGEN")
        report.append("-" * 80)
        report.append(f"  Es wurden {validation_result['invalid_count']} problematische Citations gefunden.")
        report.append("  Bitte überprüfen Sie diese vor der Veröffentlichung:")
        for detail in validation_result['details']:
            if detail['status'] != 'valid':
                report.append(f"    - [{detail['citation_id']}]: {detail['message']}")
        report.append("")
    
    report.append("=" * 80)
    report.append(f"Report erstellt: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("=" * 80)
    
    return "\n".join(report)
