from __future__ import annotations
import fitz, os, uuid, json, re
from typing import List, Dict, Any, Tuple, Optional
from tqdm import tqdm
from PIL import Image
from .config import IMAGES_DIR, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP
from .openai_client import embed_text, describe_image
import hashlib
import imagehash

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def extract_doi_and_url(doc) -> tuple[str|None, str|None]:
    # 1) Metadaten
    meta_raw = doc.metadata or {}
    # Filter leere Metafelder, damit keine leeren author/creator Felder gespeichert werden
    meta = {}
    for k, v in meta_raw.items():
        if v is None:
            continue
        if isinstance(v, str):
            if v.strip():
                meta[k] = v.strip()
        else:
            meta[k] = v
    doi = meta.get("doi") or None
    url = meta.get("identifier") or None
    # 2) Heuristik auf ersten 2 Seiten
    import re
    if not doi or not url:
        for i in range(min(2, len(doc))):
            t = doc[i].get_text("text")
            if not doi:
                m = re.search(r'\b10\.\d{4,9}/\S+\b', t)
                if m: doi = m.group(0)
            if not url:
                m2 = re.search(r'(https?://\S+)', t)
                if m2: url = m2.group(1)
    return doi, url

def extract_sections(doc) -> list[dict]:
    """Nimmt doc.get_toc(simple=True) und baut Page-Ranges."""
    toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
    sections = []
    for i, (level, title, page) in enumerate(toc):
        page_start = max(1, page)
        page_end = (toc[i+1][2]-1) if i+1 < len(toc) else len(doc)
        sections.append({
            "section_id": str(uuid.uuid4()),
            "title": title.strip(),
            "level": int(level),
            "page_start": int(page_start),
            "page_end": int(page_end),
            "order": i+1,
        })
    return sections


def extract_title_from_first_page(doc) -> str | None:
    """
    Extrahiere den Titel aus der ersten Seite durch Analyse der Textformatierung.
    Sucht nach dem größten/fettesten Text am Anfang der Seite.
    
    Returns:
        Extrahierter Titel oder None
    """
    if len(doc) == 0:
        return None
    
    page = doc[0]
    try:
        # Hole Text mit Formatierungsinformationen
        blocks = page.get_text("dict").get("blocks", [])
        
        # Sammle Kandidaten: Textblöcke mit großer Schriftgröße
        candidates = []
        for block in blocks:
            if block.get("type") != 0:  # Nur Textblöcke
                continue
            
            lines = block.get("lines", [])
            for line in lines:
                spans = line.get("spans", [])
                for span in spans:
                    text = span.get("text", "").strip()
                    if not text or len(text) < 10:  # Zu kurz für Titel
                        continue
                    
                    size = span.get("size", 0)
                    flags = span.get("flags", 0)
                    is_bold = bool(flags & 2**4)  # Flag 16 = bold
                    
                    # Bewerte: Größere Schrift + Bold = wahrscheinlicher Titel
                    score = size * (1.5 if is_bold else 1.0)
                    
                    candidates.append({
                        "text": text,
                        "score": score,
                        "y_pos": span.get("bbox", [0, 0, 0, 0])[1]  # Y-Position
                    })
        
        if not candidates:
            return None
        
        # Sortiere nach Score (höchster zuerst) und dann nach Y-Position (oben zuerst)
        candidates.sort(key=lambda c: (-c["score"], c["y_pos"]))
        
        # Nimm den besten Kandidaten, aber nur wenn er deutlich größer ist
        best = candidates[0]
        if best["score"] > 12:  # Mindestens Schriftgröße ~12pt
            return best["text"]
        
    except Exception as e:
        print(f"Warning: Could not extract title from first page: {e}")
    
    return None


def section_for_page(sections: list[dict], page_num1: int) -> dict|None:
    for s in sections:
        if s["page_start"] <= page_num1 <= s["page_end"]:
            return s
    return None

def extract_paragraph_blocks(page) -> list[dict]:
    """
    Robust: liest page.get_text('rawdict') und baut Absätze.
    - Unterstützt Spans ohne 'text' (nimmt dann 'chars' zusammen)
    - Ignoriert fehlerhafte / leere Lines
    Rückgabe: Liste aus Dicts mit text, bbox, order_in_page, page_width/-height, char_start/-end
    """
    rd = page.get_text("rawdict") or {}
    W, H = page.rect.width, page.rect.height
    paras: list[dict] = []
    order = 0

    blocks = rd.get("blocks", [])
    for b in blocks:
        if b.get("type") != 0:  # 0 = Textblock
            continue

        lines = b.get("lines") or []
        buf: list[str] = []

        for l in lines:
            spans = l.get("spans") or []
            line_parts: list[str] = []
            for s in spans:
                # 1) Normalfall
                t = s.get("text")
                # 2) Fallback: Zeichenliste
                if not t and isinstance(s.get("chars"), list):
                    try:
                        t = "".join(ch.get("c", "") for ch in s["chars"])
                    except Exception:
                        t = ""
                if t:
                    line_parts.append(t)
            if line_parts:
                buf.append("".join(line_parts))

        text = "\n".join(buf).strip()
        if not text:
            continue

        order += 1
        x0, y0, x1, y1 = (b.get("bbox") or (0, 0, 0, 0))
        paras.append({
            "text": text,
            "bbox": [x0, y0, x1, y1],
            "order_in_page": order,
            "page_width": W,
            "page_height": H,
            "char_start": 0,
            "char_end": len(text),
        })

    return paras

def extract_images_with_bbox(page) -> list[dict]:
    """
    Robust: holt Image-BBoxen aus 'rawdict'; falls nichts gefunden,
    liefert leere Liste (Speichern der Bilddateien passiert weiterhin via get_images()).
    """
    rd = page.get_text("rawdict") or {}
    W, H = page.rect.width, page.rect.height
    out: list[dict] = []
    for b in rd.get("blocks", []):
        if b.get("type") == 1:  # 1 = Imageblock
            x0, y0, x1, y1 = (b.get("bbox") or (0, 0, 0, 0))
            out.append({"bbox": [x0, y0, x1, y1], "page_width": W, "page_height": H})
    return out


def extract_figure_label(text: str) -> Optional[str]:
    """
    Extrahiert Figure-Label aus Text wie "Figure 1:", "Fig. 2.3:", "Abb. 5", etc.
    """
    patterns = [
        r'\b(?:Figure|Fig\.|Abb\.|Abbildung)\s+(\d+(?:\.\d+)?)',
        r'\bFigure\s+(\d+(?:\.\d+)?)\s*[:\.]',
        r'\bFig\.\s*(\d+(?:\.\d+)?)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return f"Figure {match.group(1)}"
    return None


def find_caption_for_image(image_bbox: list, text_blocks: list, y_tolerance: int = 50, x_tolerance: int = 20) -> tuple[str, Optional[str]]:
    """
    Intelligente Caption-Suche für Bilder:
    1. Sucht Text unterhalb des Bildes (y_tolerance)
    2. Muss horizontal aligned sein (x_tolerance)
    3. Extrahiert Figure-Label wenn vorhanden
    4. Filtert Section-Header aus (zu weit weg, zu groß)
    
    Returns: (caption_text, figure_label)
    """
    x0, y0, x1, y1 = image_bbox
    img_bottom = y1
    img_center_x = (x0 + x1) / 2
    img_width = x1 - x0
    
    candidates = []
    
    for tb in text_blocks:
        tb_x0, tb_y0, tb_x1, tb_y1 = tb["bbox"]
        tb_top = tb_y0
        tb_center_x = (tb_x0 + tb_x1) / 2
        
        # 1. Muss unterhalb des Bildes sein
        if tb_top < img_bottom:
            continue
        
        # 2. Vertikaler Abstand muss klein sein
        vertical_distance = tb_top - img_bottom
        if vertical_distance > y_tolerance:
            continue
        
        # 3. Horizontal alignment prüfen (Caption sollte unter Bild sein)
        horizontal_distance = abs(tb_center_x - img_center_x)
        if horizontal_distance > img_width / 2 + x_tolerance:
            continue
        
        # 4. Text darf nicht zu kurz sein (min. 10 Zeichen für Caption)
        text = tb.get("text", "").strip()
        if len(text) < 10:
            continue
        
        # 5. Kandidat hinzufügen mit Score (näher = besser)
        candidates.append({
            "text": text,
            "distance": vertical_distance,
            "horizontal_offset": horizontal_distance
        })
    
    if not candidates:
        return "", None
    
    # Sortiere nach vertikalem Abstand (näher ist besser)
    candidates.sort(key=lambda c: (c["distance"], c["horizontal_offset"]))
    
    # Nimm den nächsten Kandidaten
    caption_text = candidates[0]["text"]
    
    # Extrahiere Figure-Label
    figure_label = extract_figure_label(caption_text)
    
    return caption_text, figure_label


def match_bbox_to_xref(image_bboxes: list, xref_list: list, page) -> dict:
    """
    Spatial Matching: Ordnet xref (image index) der passenden BBox zu.
    Nutzt Position und Größe statt Reihenfolge.
    
    Returns: {xref: bbox_index} mapping
    """
    if not image_bboxes:
        return {}
    
    doc = page.parent
    mapping = {}
    
    for idx, img_info in enumerate(xref_list):
        xref = img_info[0]
        
        # Hole Image-Rects von der Page
        img_rects = page.get_image_rects(xref)
        
        if not img_rects:
            continue
        
        # Nimm das erste Rect (meist gibt es nur eins)
        img_rect = img_rects[0]
        img_bbox = [img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1]
        
        # Finde beste BBox-Match (kleinster Abstand)
        best_match_idx = None
        best_distance = float('inf')
        
        for bbox_idx, bbox_info in enumerate(image_bboxes):
            bbox = bbox_info["bbox"]
            
            # Berechne Zentrum-Distanz
            img_center_x = (img_bbox[0] + img_bbox[2]) / 2
            img_center_y = (img_bbox[1] + img_bbox[3]) / 2
            bbox_center_x = (bbox[0] + bbox[2]) / 2
            bbox_center_y = (bbox[1] + bbox[3]) / 2
            
            distance = ((img_center_x - bbox_center_x) ** 2 + 
                       (img_center_y - bbox_center_y) ** 2) ** 0.5
            
            if distance < best_distance:
                best_distance = distance
                best_match_idx = bbox_idx
        
        # Nur matchen wenn Distanz klein genug (< 50 Pixel)
        if best_match_idx is not None and best_distance < 50:
            mapping[xref] = best_match_idx
    
    return mapping


def nearest_caption_after(image_bbox, text_blocks, y_tol=30):
    """DEPRECATED: Use find_caption_for_image instead."""
    caption, _ = find_caption_for_image(image_bbox, text_blocks, y_tolerance=y_tol)
    return caption

def read_pdf_text_and_images(path: str):
    """
    Wie zuvor – aber mit Fallback, falls rawdict-paragraphs leer:
    nimmt dann page.get_text('text') + Chunking.
    """
    doc = fitz.open(path)
    doi, url = extract_doi_and_url(doc)
    paper_id = str(uuid.uuid4())
    meta = doc.metadata or {}
    
    # Titel-Extraktion: Stets Dateiname (ohne .pdf Extension) verwenden
    # Wunsch: Metadaten weiterhin nutzen, aber den Namen des Papers aus dem Upload-Filename setzen
    title = os.path.splitext(os.path.basename(path))[0]
    
    paper_meta = {
        "paper_id": paper_id,
        "title": title,
        "source_path": os.path.abspath(path),
        "meta": meta,
        "doi": doi,
        "url": url,
        "file_sha256": file_sha256(path),
    }

    sections = extract_sections(doc)  # kann leer sein
    paragraphs: list[dict] = []
    figures_meta: list[dict] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_num1 = page_idx + 1
        sec = section_for_page(sections, page_num1)

        # --- TEXTPFAD (robust + Fallback) ---
        text_blocks = extract_paragraph_blocks(page)
        if not text_blocks:
            # Fallback: Plain-Text der Seite + Chunking
            raw_text = page.get_text("text") or ""
            for chunk in chunk_text(raw_text, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP):
                if not chunk.strip():
                    continue
                text_blocks.append({
                    "text": chunk.strip(),
                    "bbox": [0, 0, page.rect.width, page.rect.height],
                    "order_in_page": 9999,  # Fallback-Order
                    "page_width": page.rect.width,
                    "page_height": page.rect.height,
                    "char_start": 0,
                    "char_end": len(chunk),
                })

        for tb in text_blocks:
            paragraphs.append({
                "paragraph_id": str(uuid.uuid4()),
                "text": tb["text"],
                "page": page_num1,
                "order_in_page": tb["order_in_page"],
                "char_start": tb["char_start"],
                "char_end": tb["char_end"],
                "bbox": tb["bbox"],
                "page_width": tb["page_width"],
                "page_height": tb["page_height"],
                "sha256": sha256(tb["text"]),
                "section_id_ref": sec["section_id"] if sec else None,
            })

        # --- BILDEXTRAKTION (verbessert) ---
        # 1) BBoxen sammeln
        img_blocks = extract_images_with_bbox(page)
        
        # 2) Spatial Matching: xref → bbox
        img_list = page.get_images(full=True)
        xref_to_bbox = match_bbox_to_xref(img_blocks, img_list, page)
        
        # 3) Duplikat-Tracking (via perceptual hash)
        seen_hashes = set()
        
        # 4) Bilddateien physisch extrahieren
        for img_idx, img in enumerate(img_list):
            xref = img[0]
            
            try:
                pix = fitz.Pixmap(doc, xref)
                
                # Quality Check: Größenfilter (ignoriere kleine Icons/Logos)
                width, height = pix.width, pix.height
                if width < 50 or height < 50:
                    continue  # Zu klein, wahrscheinlich Icon/Logo
                
                # Temporär speichern für Hash-Berechnung
                if pix.alpha:
                    temp_format = "PNG"
                    img_path = os.path.join(IMAGES_DIR, f"{paper_id}_{page_num1}_{xref}.png")
                else:
                    # Ensure RGB colorspace for JPG
                    if pix.colorspace and pix.colorspace.name not in ["DeviceRGB", "CalRGB"]:
                        pix_rgb = fitz.Pixmap(fitz.csRGB, pix)
                        pix = None
                        pix = pix_rgb
                    temp_format = "JPEG"
                    img_path = os.path.join(IMAGES_DIR, f"{paper_id}_{page_num1}_{xref}.jpg")
                
                # Speichern
                pix.save(img_path)
                
                # Duplikat-Erkennung via perceptual hash
                try:
                    img_pil = Image.open(img_path)
                    img_hash = str(imagehash.phash(img_pil))
                    
                    if img_hash in seen_hashes:
                        # Duplikat gefunden, Datei löschen
                        os.remove(img_path)
                        continue
                    
                    seen_hashes.add(img_hash)
                except Exception:
                    # Falls Hash-Berechnung fehlschlägt, trotzdem behalten
                    pass
                
                # BBox via Spatial Matching
                bbox_idx = xref_to_bbox.get(xref)
                if bbox_idx is not None and bbox_idx < len(img_blocks):
                    bbox = img_blocks[bbox_idx]["bbox"]
                    pw = img_blocks[bbox_idx]["page_width"]
                    ph = img_blocks[bbox_idx]["page_height"]
                    
                    # Caption und Figure-Label extrahieren
                    caption, figure_label = find_caption_for_image(bbox, text_blocks, y_tolerance=50)
                else:
                    bbox = None
                    pw = page.rect.width
                    ph = page.rect.height
                    caption = ""
                    figure_label = None
                
                # DPI/Auflösung berechnen
                dpi_x = int(width / (page.rect.width / 72)) if page.rect.width > 0 else 72
                dpi_y = int(height / (page.rect.height / 72)) if page.rect.height > 0 else 72
                
                figures_meta.append({
                    "figure_id": str(uuid.uuid4()),
                    "page": page_num1,
                    "image_path": img_path,
                    "bbox": bbox,
                    "page_width": pw,
                    "page_height": ph,
                    "figure_label": figure_label or "",
                    "caption": caption,
                    "width": width,
                    "height": height,
                    "dpi_x": dpi_x,
                    "dpi_y": dpi_y,
                    "format": temp_format,
                    "section_title": sec["title"] if sec else None,
                })
                
            except Exception as e:
                # Graceful handling von korrupten Bildern
                print(f"Warning: Could not extract image xref={xref} on page {page_num1}: {e}")
                continue

    return paper_meta, sections, paragraphs, figures_meta

def chunk_text(text: str, size: int, overlap: int) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        parts.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return parts

def embed_paragraphs(paragraphs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for p in tqdm(paragraphs, desc="Embedding paragraphs"):
        emb = embed_text(p["text"])
        out.append({**p, "embedding": emb})
    return out

def analyze_and_embed_figures(figures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for f in tqdm(figures, desc="Analyzing figures"):
        analysis = describe_image(f["image_path"])
        caption = analysis.get("caption", "") or ""
        figure_type = analysis.get("figure_type", "other") or "other"

        # Entitäten normalisieren (Liste oder kommagetrennte Zeichenkette)
        entities = analysis.get("entities") or []
        if isinstance(entities, str):
            entities = [e.strip() for e in entities.split(",") if e.strip()]

        # OCR-Hinweise normalisieren
        ocr_hints = analysis.get("ocr_hints") or []
        if isinstance(ocr_hints, str):
            ocr_hints = [ocr_hints] if ocr_hints.strip() else []

        # Reiches Embedding: Caption + Typ + Entitäten + OCR-Hinweise + Section + Label
        section_title = f.get("section_title") or ""
        figure_label  = f.get("figure_label") or ""
        rich_parts = [caption, figure_type]
        if entities:
            rich_parts.append(" ".join(entities[:10]))
        if ocr_hints:
            rich_parts.append(" ".join(ocr_hints[:5]))
        if section_title:
            rich_parts.append(section_title)
        if figure_label:
            rich_parts.append(figure_label)
        rich_text = " ".join(p for p in rich_parts if p).strip() or "figure"

        emb = embed_text(rich_text)
        out.append({
            "figure_id":      f["figure_id"],
            "page":           f["page"],
            "image_uri":      os.path.abspath(f["image_path"]),
            "image_filename": os.path.basename(f["image_path"]),
            "caption":        caption,
            "figure_type":    figure_type,
            "entities":       entities,
            "ocr_hints":      ocr_hints,
            "analysis_json":  json.dumps(analysis, ensure_ascii=False),
            "embedding":      emb,
        })
    return out
