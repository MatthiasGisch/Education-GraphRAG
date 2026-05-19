"""PDF-Export: erzeugt wissenschaftlich formatierte Antwort-PDFs mit Inline-Abbildungen und Literaturverzeichnis."""
from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime
import os, html, re
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak,
    ListFlowable, ListItem
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

def _mk_styles():
    """Erstellt und registriert benutzerdefinierte ReportLab-Absatzstile."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleBig", parent=styles["Title"], fontSize=18, leading=22, spaceAfter=10))
    styles.add(ParagraphStyle(name="Heading", parent=styles["Heading2"], spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], leading=14, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9, leading=11))
    styles.add(ParagraphStyle(name="Caption", parent=styles["BodyText"], fontSize=9, leading=11, 
                             leftIndent=12, rightIndent=12, spaceAfter=6, fontName="Helvetica-Oblique"))
    styles.add(ParagraphStyle(name="Reference", parent=styles["BodyText"], fontSize=9, leading=12,
                             leftIndent=24, firstLineIndent=-24, spaceAfter=8))
    return styles

def _escape(text: str) -> str:
    """Escaped einen String für die sichere Verwendung in ReportLab-XML-Paragraphen."""
    return html.escape(text or "")

def _make_scaled_image(path: str, max_width_pt: float, max_height_pt: float):
    """Skaliert ein Bild proportional in den Druckrahmen; gibt bei Fehler einen Fallback-Paragraph zurück."""
    from reportlab.platypus import Paragraph
    if not (path and os.path.exists(path)):
        return Paragraph(f"(Kein Bildpfad gefunden: {_escape(path or '')})", getSampleStyleSheet()["Small"])
    try:
        img = Image(path)
        iw, ih = float(getattr(img, "imageWidth", 0)), float(getattr(img, "imageHeight", 0))
        if iw <= 0 or ih <= 0:
            return Paragraph(f"(Bild unlesbar: {_escape(path)})", getSampleStyleSheet()["Small"])
        # kleiner Sicherheitsrand + angemessene Höhe (inline unter Absatz)
        max_w = max_width_pt * 0.98
        max_h = max_height_pt * 0.45
        scale = min(max_w / iw, max_h / ih, 1.0)  # nie hochskalieren
        img.drawWidth = iw * scale
        img.drawHeight = ih * scale
        return img
    except Exception as e:
        return Paragraph(f"(Bild konnte nicht geladen werden: {_escape(path)} – {e})", getSampleStyleSheet()["Small"])

def _sources_list(supports: List[Dict[str, Any]], id_mapping: Dict[str, int], styles):
    """Erstellt nummerierte Literaturverzeichnis-Paragraphen aus den Retrieval-Supports."""
    entries = []
    seen = set()
    
    # Sortiere nach Referenznummer
    sorted_supports = sorted(supports, key=lambda s: id_mapping.get(
        f"{'P' if s.get('type')=='paragraph' else 'F'}{s.get('paragraph_id') or s.get('figure_id')}", 999
    ))
    
    for s in sorted_supports:
        sid = f"{'P' if s.get('type')=='paragraph' else 'F'}{s.get('paragraph_id') or s.get('figure_id')}"
        if sid in seen:
            continue
        seen.add(sid)
        
        ref_num = id_mapping.get(sid, "?")
        title = s.get('paper_title') or 'Ohne Titel'
        doi = s.get('doi')
        url = s.get('url')
        page = s.get('page')
        section = s.get('section_title')
        
        # Wissenschaftliches Format
        parts = [f"[{ref_num}]", f"<b>{_escape(title)}</b>"]
        
        if section and section != '—':
            parts.append(f"Abschnitt: {_escape(section)}.")
        
        if page and page != '—':
            parts.append(f"S. {_escape(str(page))}.")
        
        if doi and doi != '—':
            parts.append(f"DOI: {_escape(doi)}.")
        elif url and url != '—':
            parts.append(f"URL: {_escape(url)}.")
        
        line = " ".join(parts)
        entries.append(Paragraph(line, styles["Reference"]))
    
    return entries

def _append_inline_figures_for_line(
    story: List,
    line: str,
    fig_by_id: Dict[str, Dict[str, Any]],
    used_fig_ids: List[str],
    id_mapping: Dict[str, int],
    fig_counter: Dict[str, int],
    styles,
    max_width_pt: float,
    max_height_pt: float,
    max_inline_total: int | None,
):
    """Fügt alle [F...]-referenzierten Abbildungen einer Zeile mit Caption direkt in die Story ein."""
    # pattern match: [Fxxxxxxxx-xxxx-....]  (ID ist alles außer ])
    refs = re.findall(r"\[F([^\]]+)\]", line)
    for rid in refs:
        key = f"F{rid}"
        fig = fig_by_id.get(key)
        if not fig:
            continue
        if max_inline_total is not None and len(used_fig_ids) >= max_inline_total:
            break
        if key in used_fig_ids:
            continue

        # Bild einfügen
        story.append(Spacer(1, 6))
        img_flow = _make_scaled_image(fig.get("image_uri"), max_width_pt, max_height_pt)
        story.append(img_flow)
        
        # Wissenschaftliche Caption: "Figure 1: Caption text [Ref]"
        fig_counter['count'] += 1
        fig_num = fig_counter['count']
        ref_num = id_mapping.get(key, "?")
        
        caption_text = fig.get("caption") or fig.get("figure_label") or "Ohne Beschreibung"
        paper_title = fig.get("paper_title", "")
        page = fig.get("page")
        
        # Format: Figure X: Caption [Ref] (Quelle, S. Y)
        cap_parts = [f"<b>Figure {fig_num}:</b>", _escape(caption_text)]
        cap_parts.append(f"[{ref_num}]")
        
        if paper_title:
            source_info = f"({_escape(paper_title)}"
            if page and page != '—':
                source_info += f", S. {page}"
            source_info += ")"
            cap_parts.append(source_info)
        
        cap_txt = " ".join(cap_parts)
        story.append(Paragraph(cap_txt, styles["Caption"]))
        story.append(Spacer(1, 8))
        used_fig_ids.append(key)

def write_answer_pdf(
    query: str,
    answer_text: str,
    supports: List[Dict[str, Any]],
    out_path: str,
    *,
    inline_figures: bool = True,
    max_inline_figures_total: int | None = None,
    fallback_append_top_k_if_no_refs: int = 3
) -> str:
    """Schreibt ein wissenschaftliches Antwort-PDF mit nummerierten Referenzen und Inline-Abbildungen."""
    styles = _mk_styles()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    doc = SimpleDocTemplate(
        out_path, pagesize=A4, title="GraphRAG Antwort",
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm
    )

    # 1) Erstelle ID-Mapping: [Pxxxxx] -> [1], [Fxxxxx] -> [2], etc.
    id_mapping = {}
    counter = 1
    
    # Extrahiere alle IDs aus dem Text in Reihenfolge
    all_refs = re.findall(r'\[([PF][^\]]+)\]', answer_text)
    seen_ids = set()
    
    for ref_id in all_refs:
        if ref_id not in seen_ids:
            id_mapping[ref_id] = counter
            counter += 1
            seen_ids.add(ref_id)
    
    # Füge auch alle Supports hinzu (falls nicht im Text erwähnt)
    for s in supports:
        sid = f"{'P' if s.get('type')=='paragraph' else 'F'}{s.get('paragraph_id') or s.get('figure_id')}"
        if sid not in id_mapping:
            id_mapping[sid] = counter
            counter += 1
    
    # 2) Konvertiere Text: [Pxxxxx] -> [1], [Fxxxxx] -> [2]
    converted_text = answer_text
    for old_id, new_num in sorted(id_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        converted_text = converted_text.replace(f"[{old_id}]", f"[{new_num}]")

    # Map: "F<id>" -> Figure-Support (mit original IDs für Matching)
    fig_by_id = {
        f"F{f['figure_id']}": f
        for f in supports if f.get("type") == "figure"
    }

    story: List = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    story.append(Paragraph("Antwortbericht (GraphRAG)", styles["TitleBig"]))
    story.append(Paragraph(f"Datum: {ts}", styles["Small"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Frage", styles["Heading"]))
    story.append(Paragraph(_escape(query), styles["Body"]))

    # Antwort: Zeile für Zeile mit konvertierten Referenzen
    story.append(Spacer(1, 8))
    story.append(Paragraph("Antwort (mit Belegen)", styles["Heading"]))

    used_fig_ids: List[str] = []
    fig_counter = {'count': 0}  # Zähler für Figure-Nummern (Figure 1, Figure 2, ...)
    any_inline_figs = False

    # Verarbeite beide Versionen parallel (original für Figure-Matching, konvertiert für Anzeige)
    original_lines = answer_text.split("\n")
    converted_lines = converted_text.split("\n")
    
    for orig_line, conv_line in zip(original_lines, converted_lines):
        line = conv_line.strip()
        if not line:
            story.append(Spacer(1, 6))
            continue
        
        # Text-Paragraph mit nummerierten Referenzen [1], [2], etc.
        story.append(Paragraph(_escape(line).replace("  ", "&nbsp;&nbsp;"), styles["Body"]))
        
        # Inline-Figuren direkt darunter einfügen (suche im ORIGINAL-Text nach [Fxxxxx])
        if inline_figures and fig_by_id:
            before_count = len(used_fig_ids)
            _append_inline_figures_for_line(
                story, orig_line, fig_by_id, used_fig_ids, id_mapping, fig_counter, styles,
                max_width_pt=doc.width, max_height_pt=doc.height,
                max_inline_total=max_inline_figures_total,
            )
            if len(used_fig_ids) > before_count:
                any_inline_figs = True

    # Quellenverzeichnis (wissenschaftlich formatiert)
    story.append(Spacer(1, 10))
    story.append(Paragraph("Literaturverzeichnis", styles["Heading"]))
    for ref_entry in _sources_list(supports, id_mapping, styles):
        story.append(ref_entry)

    # Fallback: Wenn keine Inline-Referenzen genutzt wurden, optional Top-K Figuren ans Ende
    if not any_inline_figs and fallback_append_top_k_if_no_refs and fallback_append_top_k_if_no_refs > 0 and fig_by_id:
        figs = [v for v in fig_by_id.values()]
        figs.sort(key=lambda f: f.get("score", 0), reverse=True)
        figs = figs[:fallback_append_top_k_if_no_refs]
        story.append(PageBreak())
        story.append(Paragraph(f"Verwendete Abbildungen (Top {len(figs)})", styles["Heading"]))
        
        for f in figs:
            fig_counter['count'] += 1
            fig_num = fig_counter['count']
            
            story.append(Spacer(1, 6))
            img_flow = _make_scaled_image(f.get("image_uri"), doc.width, doc.height)
            story.append(img_flow)
            
            # Wissenschaftliche Caption
            sid = f"F{f['figure_id']}"
            ref_num = id_mapping.get(sid, "?")
            caption_text = f.get("caption") or f.get("figure_label") or "Ohne Beschreibung"
            paper_title = f.get("paper_title", "")
            page = f.get("page")
            
            cap_parts = [f"<b>Figure {fig_num}:</b>", _escape(caption_text)]
            cap_parts.append(f"[{ref_num}]")
            
            if paper_title:
                source_info = f"({_escape(paper_title)}"
                if page and page != '—':
                    source_info += f", S. {page}"
                source_info += ")"
                cap_parts.append(source_info)
            
            cap_txt = " ".join(cap_parts)
            story.append(Paragraph(cap_txt, styles["Caption"]))
            story.append(Spacer(1, 8))

    doc.build(story)
    return out_path
