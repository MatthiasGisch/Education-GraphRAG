# src/pdf_export.py
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
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleBig", parent=styles["Title"], fontSize=18, leading=22, spaceAfter=10))
    styles.add(ParagraphStyle(name="Heading", parent=styles["Heading2"], spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], leading=14, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9, leading=11))
    return styles

def _escape(text: str) -> str:
    return html.escape(text or "")

def _make_scaled_image(path: str, max_width_pt: float, max_height_pt: float):
    """
    Skaliert ein Bild sicher in den verfügbaren Rahmen.
    Gibt als Fallback einen kleinen Hinweis-Paragraph zurück.
    """
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

def _sources_list(supports: List[Dict[str, Any]], styles):
    entries = []
    seen = set()
    for s in supports:
        sid = f"{'P' if s.get('type')=='paragraph' else 'F'}{s.get('paragraph_id') or s.get('figure_id')}"
        if sid in seen:
            continue
        seen.add(sid)
        line = (
            f"[{sid}] {_escape(s.get('paper_title') or '—')} — "
            f"DOI: {_escape(s.get('doi') or '—')} — "
            f"URL: {_escape(s.get('url') or '—')} — "
            f"Seite: {_escape(str(s.get('page') or '—'))} — "
            f"Abschnitt: {_escape(s.get('section_title') or '—')}"
        )
        entries.append(Paragraph(line, styles["Small"]))
    return ListFlowable([ListItem(e) for e in entries], bulletType="bullet", leftIndent=12)

def _append_inline_figures_for_line(
    story: List,
    line: str,
    fig_by_id: Dict[str, Dict[str, Any]],
    used_fig_ids: List[str],
    styles,
    max_width_pt: float,
    max_height_pt: float,
    max_inline_total: int | None,
):
    """
    Findet alle [F<id>]-Referenzen in der Zeile und fügt unmittelbar darunter
    die entsprechenden (skalierten) Bilder + Captions ein. Vermeidet Duplikate.
    """
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

        img_flow = _make_scaled_image(fig.get("image_uri"), max_width_pt, max_height_pt)
        story.append(img_flow)
        cap = fig.get("caption") or fig.get("figure_label") or ""
        cap_txt = f"[{key}] {_escape(cap)} (Seite {fig.get('page') or '—'})"
        story.append(Paragraph(cap_txt, styles["Small"]))
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
    """
    - inline_figures=True: Bilder werden dort eingefügt, wo [F<id>] im Text steht.
    - max_inline_figures_total: optionales globales Limit (None = kein Limit).
    - fallback_append_top_k_if_no_refs: wenn im Text gar keine [F...] vorkommen,
      werden (optional) Top-K Figuren am Ende hinzugefügt (0 = kein Fallback).
    """
    styles = _mk_styles()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    doc = SimpleDocTemplate(
        out_path, pagesize=A4, title="GraphRAG Antwort",
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm
    )

    # Map: "F<id>" -> Figure-Support
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

    # Antwort: Zeile für Zeile, danach ggf. inline Figuren einfügen
    story.append(Spacer(1, 8))
    story.append(Paragraph("Antwort (mit Belegen)", styles["Heading"]))

    used_fig_ids: List[str] = []
    any_inline_figs = False

    for raw_line in answer_text.split("\n"):
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 6))
            continue
        # Text-Paragraph einfügen (Referenzen [P…]/[F…] bleiben sichtbar)
        story.append(Paragraph(_escape(line).replace("  ", "&nbsp;&nbsp;"), styles["Body"]))
        # Inline-Figuren direkt darunter einfügen
        if inline_figures and fig_by_id:
            before_count = len(used_fig_ids)
            _append_inline_figures_for_line(
                story, line, fig_by_id, used_fig_ids, styles,
                max_width_pt=doc.width, max_height_pt=doc.height,
                max_inline_total=max_inline_figures_total,
            )
            if len(used_fig_ids) > before_count:
                any_inline_figs = True

    # Quellenblock
    story.append(Spacer(1, 10))
    story.append(Paragraph("Quellen", styles["Heading"]))
    story.append(_sources_list(supports, styles))

    # Fallback: Wenn keine Inline-Referenzen genutzt wurden, optional Top-K Figuren ans Ende
    if not any_inline_figs and fallback_append_top_k_if_no_refs and fallback_append_top_k_if_no_refs > 0 and fig_by_id:
        figs = [v for v in fig_by_id.values()]
        figs.sort(key=lambda f: f.get("score", 0), reverse=True)
        figs = figs[:fallback_append_top_k_if_no_refs]
        story.append(PageBreak())
        story.append(Paragraph(f"Verwendete Abbildungen (Top {len(figs)})", styles["Heading"]))
        for f in figs:
            img_flow = _make_scaled_image(f.get("image_uri"), doc.width, doc.height)
            story.append(img_flow)
            cap = f.get("caption") or f.get("figure_label") or ""
            story.append(Paragraph(f"[F{f['figure_id']}] {_escape(cap)} (Seite {f.get('page') or '—'})", styles["Small"]))
            story.append(Spacer(1, 8))

    doc.build(story)
    return out_path
