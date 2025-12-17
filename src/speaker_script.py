# src/speaker_script.py
"""
Sprechtext-Generierung für Video- und Podcast-Produktion
Optimiert für Synthesia, ElevenLabs und andere KI-Sprachsynthese
"""

import json
import re
from typing import Dict, List, Any
from datetime import datetime
import textwrap


def _normalize_text(text: str) -> str:
    """Normalisiert Text für Sprachsynthese."""
    # Entferne mehrfache Leerzeichen
    text = re.sub(r'\s+', ' ', text)
    # Entferne Markdown-Formatierungen
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    return text.strip()


def _optimize_for_speech(text: str) -> str:
    """
    Optimiert Text für natürliche Sprachsynthese.
    - Zerlegt lange Sätze
    - Ersetzt Zahlen durch Wörter
    - Fügt Pausen-Markierungen ein
    """
    text = _normalize_text(text)
    
    # Zahlenwörter (vereinfacht)
    replacements = {
        r'\b1\b': 'eins',
        r'\b2\b': 'zwei',
        r'\b3\b': 'drei',
        r'\b4\b': 'vier',
        r'\b5\b': 'fünf',
        r'\b6\b': 'sechs',
        r'\b7\b': 'sieben',
        r'\b8\b': 'acht',
        r'\b9\b': 'neun',
        r'\b0\b': 'null',
    }
    
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    
    # Ersetze Abkürzungen
    abbreviations = {
        r'\bz\.B\.': 'zum Beispiel',
        r'\betc\.': 'et cetera',
        r'\bbspw\.': 'beziehungsweise',
        r'\bvgl\.': 'vergleiche',
        r'\bSPE': 'SPE',  # Fachbegriffe behalten
    }
    
    for pattern, replacement in abbreviations.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    
    return text


def _estimate_duration(text: str, words_per_minute: int = 130) -> str:
    """Schätzt Sprachdauer basierend auf Wortanzahl."""
    words = len(text.split())
    minutes = max(1, words // words_per_minute)
    seconds = ((words % words_per_minute) * 60) // words_per_minute
    return f"{minutes}m {seconds}s"


def _split_into_sentences(text: str, max_length: int = 150) -> List[str]:
    """Zerlegt Text in Sätze mit max. Länge für bessere Lesbarkeit."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # Wenn Satz zu lang, teile ihn auf
        if len(sentence) > max_length:
            # Versuche, an Kommas zu teilen
            parts = sentence.split(', ')
            current = ""
            for part in parts:
                if len(current) + len(part) + 2 <= max_length:
                    current += (", " if current else "") + part
                else:
                    if current:
                        result.append(current)
                    current = part
            if current:
                result.append(current)
        else:
            result.append(sentence)
    
    return result


def generate_speaker_script(
    course_struct: Dict[str, Any],
    course_name: str,
    learner_role: str = None
) -> Dict[str, Any]:
    """
    Generiert strukturierte Sprechtexte für Video/Podcast-Produktion.
    
    Args:
        course_struct: Kursstruktur aus der GUI
        course_name: Name des Kurses
        learner_role: Zielgruppe/Rolle (optional)
    
    Returns:
        Dict mit JSON und Markdown Varianten
    """
    
    chapters = course_struct.get("Kapitel", [])
    
    # JSON-Struktur für APIs (z.B. Synthesia, ElevenLabs)
    json_script = {
        "metadata": {
            "title": course_name,
            "created": datetime.now().isoformat(),
            "learner_role": learner_role or "Allgemein",
            "total_chapters": len(chapters),
            "version": "1.0"
        },
        "chapters": []
    }
    
    # Markdown für Menschen
    markdown_script = f"# {course_name}\n\n"
    if learner_role:
        markdown_script += f"**Zielgruppe:** {learner_role}\n\n"
    markdown_script += f"**Erstellt:** {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
    markdown_script += "---\n\n"
    
    total_duration_min = 0
    
    for chapter_idx, chapter in enumerate(chapters, 1):
        chapter_title = chapter.get("Titel", f"Kapitel {chapter_idx}")
        sections = chapter.get("Abschnitte", [])
        
        chapter_data = {
            "number": chapter_idx,
            "title": chapter_title,
            "duration_estimate": "TBD",
            "sections": []
        }
        
        chapter_duration_text = 0
        
        # Markdown-Kapitelüberschrift
        markdown_script += f"## Kapitel {chapter_idx}: {chapter_title}\n\n"
        
        # Lernziele
        learning_objectives = chapter.get("Lernziele", [])
        if learning_objectives:
            markdown_script += "**Lernziele:**\n"
            for obj in learning_objectives:
                if obj.strip():
                    markdown_script += f"- {obj}\n"
            markdown_script += "\n"
        
        for section_idx, section_title in enumerate(sections, 1):
            # Hole Inhaltstext aus der Struktur
            content_text = chapter.get("Inhalte", {}).get(str(section_idx - 1), "")
            
            if not content_text:
                content_text = f"Inhalt für {section_title} wird generiert."
            
            # Optimiere für Sprache
            optimized_text = _optimize_for_speech(content_text)
            
            # Zerlege in Sätze
            sentences = _split_into_sentences(optimized_text)
            
            # Schätze Dauer
            duration = _estimate_duration(optimized_text)
            
            # Parse Dauer für Summation
            dur_match = re.match(r'(\d+)m\s+(\d+)s', duration)
            if dur_match:
                minutes = int(dur_match.group(1))
                seconds = int(dur_match.group(2))
                total_seconds = minutes * 60 + seconds
                chapter_duration_text += total_seconds
            
            section_data = {
                "number": section_idx,
                "title": section_title,
                "script": optimized_text,
                "script_sentences": sentences,  # Für visuelle Marker
                "duration_estimate": duration,
                "word_count": len(optimized_text.split()),
                "speaker_notes": f"Natürliche Geschwindigkeit. Pausen nach wichtigen Punkten einbauen."
            }
            
            chapter_data["sections"].append(section_data)
            
            # Markdown-Abschnitt
            markdown_script += f"### Abschnitt {section_idx}: {section_title}\n\n"
            markdown_script += f"**Dauer:** {duration}\n\n"
            markdown_script += f"**Sprechtext:**\n\n"
            
            # Formatiere Sätze mit Pausen
            for sent in sentences:
                markdown_script += f"{sent}\n\n"
            
            markdown_script += "---\n\n"
        
        # Summiere Kapitel-Dauer
        chapter_min = chapter_duration_text // 60
        chapter_sec = chapter_duration_text % 60
        chapter_data["duration_estimate"] = f"{chapter_min}m {chapter_sec}s"
        total_duration_min += chapter_duration_text
        
        json_script["chapters"].append(chapter_data)
    
    # Gesamtdauer
    total_min = total_duration_min // 60
    total_sec = total_duration_min % 60
    json_script["metadata"]["total_duration"] = f"{total_min}m {total_sec}s"
    
    return {
        "json": json_script,
        "markdown": markdown_script,
        "total_duration": f"{total_min}m {total_sec}s"
    }


def export_speaker_script(
    script_data: Dict[str, Any],
    output_dir: str,
    course_name: str
) -> Dict[str, str]:
    """
    Speichert Sprechtexte als JSON und Markdown.
    
    Returns:
        Dict mit Pfaden zu beiden Dateien
    """
    from pathlib import Path
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize Dateinamen
    safe_name = "".join(c if c.isalnum() or c in (' ', '_', '-') else '' for c in course_name)
    safe_name = safe_name.replace(' ', '_')
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # JSON exportieren
    json_path = output_dir / f"{safe_name}_{timestamp}_script.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(script_data["json"], f, ensure_ascii=False, indent=2)
    
    # Markdown exportieren
    md_path = output_dir / f"{safe_name}_{timestamp}_script.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(script_data["markdown"])
    
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "json_filename": json_path.name,
        "markdown_filename": md_path.name
    }
