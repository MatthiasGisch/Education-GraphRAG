import html
import streamlit as st
from src.neo import Neo4jClient
from src.agent import answer_query
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from pathlib import Path
import datetime
from io import BytesIO
import re


def _safe_para(text: str) -> str:
    """Escapes text for ReportLab Paragraph XML while preserving <super>…</super> citation tags."""
    parts = re.split(r'(</?super>)', text)
    return ''.join(
        p if p in ('<super>', '</super>') else html.escape(p)
        for p in parts
    )


def _remap_inline_citations(text: str, local_titles: list, global_mapping: dict) -> str:
    """
    Remaps section-local <super>[n]</super> numbers to global bibliography numbers.
    local_titles: sorted list of paper titles used in this section (defines local 1-based order)
    global_mapping: {paper_title: global_citation_number}
    """
    if not text or not local_titles or not global_mapping:
        return text
    local_to_global = {}
    for local_idx, title in enumerate(local_titles, 1):
        if title in global_mapping:
            local_to_global[local_idx] = global_mapping[title]
    # Replace in reverse order so [10] is handled before [1]
    for local_num in sorted(local_to_global.keys(), reverse=True):
        global_num = local_to_global[local_num]
        if local_num != global_num:
            text = text.replace(
                f"<super>[{local_num}]</super>",
                f"<super>[{global_num}]</super>",
            )
    return text


def clean_llm_metadata(text: str) -> str:
    """
    Entfernt überflüssige Metadaten und Präfixe, die das LLM manchmal generiert.
    Z.B. "Schulungsunterlagen: Titel – Untertitel" am Anfang des Textes.
    """
    if not text:
        return text
    
    # Pattern 1: Entferne "Schulungsunterlagen:" oder ähnliche Präfixe am Anfang
    text = re.sub(r'^(?:Schulungsunterlagen|Kursmaterial|Lehrmaterial|Vorlesung|Kapitel|Abschnitt)\s*:\s*', '', text, flags=re.IGNORECASE)
    
    # Pattern 2: Entferne Titel-Zeilen mit " – " (Gedankenstrich) am Anfang
    # Typisch: "Titel – Untertitel" oder "Frage – Kontext"
    lines = text.split('\n')
    if lines and '–' in lines[0] and len(lines[0]) < 150:
        # Erste Zeile ist wahrscheinlich ein Titel/Meta-Zeile
        lines = lines[1:]
    
    text = '\n'.join(lines).strip()
    
    return text


def fetch_content_for_chapter(neo: Neo4jClient, chapter_title: str, topic: str = None, retrieval_hints: dict = None, section_title: str = None, learner_role: str = None) -> dict:
    """
    Holt relevante Inhalte aus dem Wissensgraphen für ein Kapitel.
    Nutzt das bewährte Retrieval-System aus dem Fragen-Tab.
    
    Args:
        neo: Neo4j Client
        chapter_title: Titel des Kapitels
        topic: Optional das Topic für bessere Zuordnung
        retrieval_hints: Optional dict mit Abschnitt-Index -> Retrieval-Hinweis
        section_title: Optional Abschnittstitel für spezifische Sections
        learner_role: Optional Zielgruppe/Rolle (z.B. "Vertriebsmitarbeiter", "Lagerarbeiter")
        
    Returns:
        Dict mit answer_text, paragraphs, figures, related_concepts
    """
    result = {
        "answer_text": "",
        "paragraphs": [], 
        "figures": [], 
        "related_concepts": [], 
        "concept_description": "",
        "sources": []  # Liste von Quellen für Gamma-Export
    }
    
    try:
        # Erstelle eine strukturierte Frage für das Retrieval
        # Baue einen rollenspezifischen Prompt auf
        if learner_role and learner_role.strip():
            # Rollenspezifischer Prompt mit Praxisbezug
            role_context = f"""Erstelle umfassende Schulungsunterlagen für '{learner_role}' mit folgenden Anforderungen:
- Arbeitskontext: Relevanz für den beruflichen Alltag dieser Rolle
- Praxisbezüge: Konkrete Beispiele und Anwendungsfälle aus der Praxis
- Mehrwert: Zeige Nutzen und ROI im Unternehmenskontext auf
- Actionability: Praktische Handlungsempfehlungen und konkrete Umsetzungsschritte
- Verständlichkeit: Didaktisch strukturiert für Mitarbeiter im operativen Kontext"""
            
            if section_title:
                # Für einen spezifischen Abschnitt
                query = f"{role_context}\n\nZum Konzept '{chapter_title}' - Aspekt '{section_title}':\nErläutere detailliert mit praktischen Beispielen und Tipps für die tägliche Arbeit."
            else:
                # Für das gesamte Kapitel
                query = f"{role_context}\n\nZum Konzept '{chapter_title}':\nGib eine umfassende Erklärung mit praktischen Beispielen, Handlungsempfehlungen und konkretisierten Mehrwerten."
        else:
            # Fallback für generische Zielgruppe
            if section_title:
                query = f"Erkläre zum Konzept '{chapter_title}' den Aspekt '{section_title}' ausführlich und didaktisch verständlich für Lernende."
            else:
                query = f"Erkläre das Konzept '{chapter_title}' umfassend und didaktisch verständlich."
        
        # Füge Retrieval-Hinweise zur Query hinzu, falls vorhanden
        if retrieval_hints:
            # Sammle alle Fokus-Hinweise aus den dict-Strukturen
            all_fokus_hints = []
            
            for hint_data in retrieval_hints.values():
                if isinstance(hint_data, dict):
                    # Sammle alle drei Fokus-Felder
                    for key in ["fokus1", "fokus2", "fokus3"]:
                        if hint_data.get(key, "").strip():
                            all_fokus_hints.append(hint_data[key].strip())
                    # Legacy support für alte Felder (fokus, kontext, ausschluss)
                    for key in ["fokus", "kontext", "ausschluss"]:
                        if key in hint_data and hint_data[key].strip():
                            all_fokus_hints.append(hint_data[key].strip())
                elif isinstance(hint_data, str) and hint_data.strip():
                    # Legacy support für alte String-Hinweise
                    all_fokus_hints.append(hint_data.strip())
            
            # Füge alle Fokus-Hinweise zur Query hinzu
            if all_fokus_hints:
                query += f" Berücksichtige dabei folgende Aspekte: {', '.join(all_fokus_hints)}."

        query += " Schreibe einen ausführlichen, vollständigen Kursabschnitt mit mindestens 400 Wörtern."

        print(f"DEBUG: Fetching content for chapter '{chapter_title}'" + (f", section '{section_title}'" if section_title else ""))
        print(f"DEBUG: Query: {query[:200]}...")
        
        # Nutze das bewährte Retrieval-System
        # Versuche ZUERST mit min_supports=5 um echte Supports zu erhalten
        response = answer_query(
            query=query,
            neo=neo,
            web_mode="off",
            k_paragraphs=25,
            k_figures=12,
            use_concept_retrieval=True,
            min_supports=5,
            max_supports=30,
        )

        # Falls zu wenig Supports gefunden wurden, versuche mit geringerer Schwelle
        supports = response.get("supports", [])
        if len(supports) < 3:
            print(f"DEBUG: Only {len(supports)} supports found, retrying with lower threshold...")
            response = answer_query(
                query=query,
                neo=neo,
                web_mode="off",
                k_paragraphs=25,
                k_figures=10,
                use_concept_retrieval=True,
                min_supports=1,
                max_supports=30,
            )
            supports = response.get("supports", [])
            print(f"DEBUG: Retry result - {len(supports)} supports")
        
        # Debug: Zeige Support-Informationen
        paragraphs = [s for s in supports if s.get("type") == "paragraph"]
        figures = [s for s in supports if s.get("type") == "figure"]
        debug_info = response.get("debug", {})
        print(f"DEBUG: Retrieval result - {len(paragraphs)} paragraphs, {len(figures)} figures, total {len(supports)} supports")
        print(f"DEBUG: Response mode: {response.get('mode')}, Total supports: {len(supports)}")
        print(f"DEBUG: Response debug: {debug_info}")
        
        # Extrahiere die Antwort (bereits gut strukturiert und in deutscher Sprache)
        result["answer_text"] = response.get("answer", "")
        
        # WICHTIG: Speichere den RAW TEXT VOR Bereinigung für Citation Validierung
        # Der rohe Text enthält noch die [Pxxx] Zitationen vom LLM
        result["answer_text_raw"] = result["answer_text"]
        
        # Entferne überflüssige LLM-Metadaten am Anfang
        result["answer_text"] = clean_llm_metadata(result["answer_text"])
        
        # Entferne Markdown-Formatierungen für saubere Vorlesungsunterlagen
        
        # Entferne alle Arten von Quellenverweisen, die vom System eingefügt wurden
        # Pattern 1: [paper_id: 123] oder [paper_id:123] - auch ohne Leerzeichen
        result["answer_text"] = re.sub(r'\[paper_id:\s*[^\]]+\]', '', result["answer_text"], flags=re.IGNORECASE)
        result["answer_text"] = re.sub(r'\[paperid:\s*[^\]]+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 2: [source: ...] beliebiger Inhalt
        result["answer_text"] = re.sub(r'\[source:\s*[^\]]+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 3: [para_id: 123] oder ähnliche ID-Formate
        result["answer_text"] = re.sub(r'\[para_id:\s*\d+\]', '', result["answer_text"], flags=re.IGNORECASE)
        result["answer_text"] = re.sub(r'\[paragraph_id:\s*[^\]]+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 4: [fig_id: 123] oder [figure_id: 123]
        result["answer_text"] = re.sub(r'\[fig(?:ure)?_id:\s*\d+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 5: [Figure 1], [Fig. 1], [Abbildung 1]
        result["answer_text"] = re.sub(r'\[(?:Figure|Fig\.?|Abbildung)\s+\d+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 6: Standalone numerische Referenzen wie [1], [2], [1,2,3] (aber nicht am Ende von Sätzen wo wir sie wollen)
        # Entferne nur wenn sie NICHT am Ende eines Satzes stehen (kein Punkt/Zeilenende davor)
        result["answer_text"] = re.sub(r'(?<![.!?])\s*\[\d+(?:,\s*\d+)*\](?!\s*$)', '', result["answer_text"])
        
        # Pattern 7: UUIDs oder lange IDs in eckigen Klammern (auch mit Präfix wie P, F, etc.)
        result["answer_text"] = re.sub(r'\[[A-Z]?[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 7b: Kurze Paper/Figure IDs wie [P9693640d] oder [Pee7505a7]
        result["answer_text"] = re.sub(r'\[[A-Z][a-z0-9]{7,12}\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 8: Generische [id: ...] oder [ID: ...]
        result["answer_text"] = re.sub(r'\[i?d:\s*[^\]]+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 9: [Quelle: ...] oder [Source: ...]
        result["answer_text"] = re.sub(r'\[(?:Quelle|Source):[^\]]+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # Pattern 10: Beliebige IDs mit Doppelpunkten [beliebig_id: ...]
        result["answer_text"] = re.sub(r'\[[a-z_]+_id:\s*[^\]]+\]', '', result["answer_text"], flags=re.IGNORECASE)
        
        # ENTFERNT: Pattern 11 - Entferne NICHT numerische Zitationen [1], [2], etc.!
        # Diese werden gezielt als Inline-Zitationen eingefügt und sind wichtig für die PDF
        # result["answer_text"] = re.sub(r'(?<!\.)\s*\[\d+\](?!\s*$|\s*\.)', ' ', result["answer_text"])
        
        # Pattern 12: Entferne kompletten "Quellen"-Block am Ende (falls LLM ihn trotzdem erstellt)
        # Dieser Block beginnt mit "Quellen" und enthält Listen mit DOI, URL, Seite etc.
        result["answer_text"] = re.sub(
            r'\n\s*Quellen[:\s]*\n[\s\S]*?(?=\n\n|\Z)', 
            '', 
            result["answer_text"], 
            flags=re.MULTILINE | re.IGNORECASE
        )
        
        # Entferne **fett** Formatierung
        result["answer_text"] = result["answer_text"].replace("**", "")
        # Entferne *kursiv* Formatierung (aber behalte einzelne * wenn sie nicht für Formatierung sind)
        result["answer_text"] = re.sub(r'\*([^\*]+)\*', r'\1', result["answer_text"])
        # Entferne Markdown-Überschriften (###, ##, #)
        result["answer_text"] = re.sub(r'^#{1,6}\s+', '', result["answer_text"], flags=re.MULTILINE)
        # Entferne andere Markdown-Elemente
        result["answer_text"] = result["answer_text"].replace("___", "")
        result["answer_text"] = result["answer_text"].replace("---", "")
        
        # Bereinige mehrfache Leerzeichen IN Zeilen, aber behalte Zeilenumbrüche
        lines = result["answer_text"].split('\n')
        lines = [re.sub(r' +', ' ', line) for line in lines]  # Mehrfache Leerzeichen pro Zeile
        result["answer_text"] = '\n'.join(lines)
        # Normalisiere Absätze: Max 2 Zeilenumbrüche (= 1 Leerzeile zwischen Absätzen)
        result["answer_text"] = re.sub(r'\n{3,}', '\n\n', result["answer_text"])
        
        # Markiere Fokus-Begriffe im Text fett (falls retrieval_hints vorhanden)
        if retrieval_hints:
            for hint_data in retrieval_hints.values():
                if isinstance(hint_data, dict):
                    for key in ["fokus1", "fokus2", "fokus3"]:
                        fokus_term = hint_data.get(key, "").strip()
                        if fokus_term:
                            # Ersetze den Begriff im Text mit fetter Version (case-insensitive)
                            # Verwende Word Boundaries damit nur ganze Wörter/Phrasen ersetzt werden
                            pattern = r'\b' + re.escape(fokus_term) + r'\b'
                            result["answer_text"] = re.sub(
                                pattern, 
                                f'<b>{fokus_term}</b>', 
                                result["answer_text"], 
                                flags=re.IGNORECASE
                            )
        
        # Extrahiere die Supports (Paragraphen und Figuren)
        supports = response.get("supports", [])
        print(f"DEBUG: Processing {len(supports)} supports from answer_query")
        
        # Sammle Quellen mit vollständigen Informationen UND Zuordnung zu Supports
        sources_dict = {}  # paper_title -> full citation info
        source_to_number = {}  # paper_title -> citation number (für inline citations)
        
        for idx, support in enumerate(supports):
            paper_title = support.get("paper_title", "").strip()
            paper_id = support.get("paper_id", "")
            
            # Fallback: Wenn paper_title leer ist, nutze paper_id
            if not paper_title and paper_id:
                paper_title = f"Document {paper_id[:8]}"  # Gekürzte paper_id als Fallback
            
            if idx < 5:  # Zeige die ersten 5 Supports
                print(f"DEBUG: Support {idx+1} - Type: {support.get('type')}, Paper: '{paper_title}', ID: {paper_id[:20] if paper_id else 'None'}")
            
            if support.get("type") == "paragraph":
                result["paragraphs"].append({
                    "type": "paragraph",
                    "text": support.get("text", ""),
                    "paper_title": paper_title,
                    "paper_id": paper_id,
                    "paragraph_id": support.get("paragraph_id", ""),  # WICHTIG für Citation Validator
                    "section": support.get("section_title", ""),
                    "score": support.get("score", 0)
                })
            elif support.get("type") == "figure":
                result["figures"].append({
                    "type": "figure",
                    "caption": support.get("caption", ""),
                    "label": support.get("figure_label", ""),
                    "paper_title": paper_title,
                    "paper_id": paper_id,
                    "figure_id": support.get("figure_id", ""),  # WICHTIG für Citation Validator
                })
            
            # Sammle eindeutige Quellen
            if paper_title and paper_title not in sources_dict:
                # Filtere nur komplett leere Titel
                if not paper_title or paper_title.strip().lower() in ['unknown', '']:
                    continue  # Überspringe ungültige Einträge
                
                # Extrahiere Jahr aus pdf_creation_date falls vorhanden
                year_raw = support.get("year", "")
                year = ""
                if year_raw:
                    # Versuche Jahr aus Datum zu extrahieren (z.B. "D:20210315..." -> "2021")
                    year_match = re.search(r'(\d{4})', str(year_raw))
                    if year_match:
                        year = year_match.group(1)
                
                # Sammle Seitenzahlen für diese Quelle
                pages = set()
                if support.get("page"):
                    pages.add(support.get("page"))
                
                # Versuche Metadaten aus Neo4j zu holen wenn paper_id vorhanden
                authors = support.get("authors", "")
                doi = support.get("doi", "")
                url = support.get("url", "")
                source_pub = support.get("source", "")
                
                if neo and paper_id:
                    try:
                        # Abfrage mit kurierten Metadaten-Feldern (aus GUI nachgetragen)
                        neo_query = """
                        MATCH (p:Paper {paper_id: $pid})
                        RETURN p.author AS author, p.creator AS creator, 
                               p.publication_year AS publication_year,
                               p.doi AS doi, p.url AS url, p.source AS source, p.publisher AS publisher,
                               p.creationDate AS creationDate, p.subject AS subject
                        """
                        neo_result = neo.run(neo_query, {"pid": paper_id})
                        
                        # Falls nicht gefunden, versuche mit Titel
                        if not neo_result or not neo_result[0]:
                            neo_query_title = """
                            MATCH (p:Paper)
                            WHERE p.title STARTS WITH $title_prefix
                            RETURN p.author AS author, p.creator AS creator, 
                                   p.publication_year AS publication_year,
                                   p.doi AS doi, p.url AS url, p.source AS source, p.publisher AS publisher,
                                   p.creationDate AS creationDate, p.subject AS subject
                            LIMIT 1
                            """
                            title_prefix = paper_title[:30] if len(paper_title) > 30 else paper_title
                            neo_result = neo.run(neo_query_title, {"title_prefix": title_prefix})
                        
                        if neo_result and neo_result[0]:
                            paper_meta = neo_result[0]
                            
                            # Extrahiere Autoren: Priorisiere kurierte Daten
                            if not authors:
                                author_field = paper_meta.get("author") or ""
                                # Filtere Softwarenamen heraus (z.B. "Adobe InDesign", "Pressbooks")
                                software_names = ["adobe", "pressbooks", "indesign", "pages", "word"]
                                if author_field and not any(sw in author_field.lower() for sw in software_names):
                                    authors = author_field.strip()
                            
                            # Jahr: Priorisiere publication_year (kuriert), Fallback auf creationDate
                            if not year:
                                if paper_meta.get("publication_year"):
                                    year = str(paper_meta.get("publication_year")).strip()
                                elif paper_meta.get("creationDate"):
                                    year_raw = str(paper_meta.get("creationDate", ""))
                                    year_match = re.search(r'(\d{4})', year_raw)
                                    if year_match:
                                        year = year_match.group(1)
                            
                            # DOI, URL, Source, Publisher aus kurierten Daten
                            if not doi and paper_meta.get("doi"):
                                doi = paper_meta.get("doi").strip()
                            if not url and paper_meta.get("url"):
                                url = paper_meta.get("url").strip()
                            if not source_pub:
                                # Priorisiere kurierte "source", Fallback auf "subject"
                                if paper_meta.get("source"):
                                    source_pub = paper_meta.get("source").strip()
                                elif paper_meta.get("subject"):
                                    source_pub = paper_meta.get("subject").strip()
                        else:
                            if idx < 2:
                                print(f"DEBUG: Paper not found in Neo4j - ID: {paper_id[:20]}, Title: {paper_title[:50]}")
                    except Exception as e:
                        print(f"WARNING: Could not fetch metadata from Neo4j for {paper_id}: {e}")
                
                sources_dict[paper_title] = {
                    "title": paper_title,
                    "authors": authors,
                    "year": year,
                    "doi": doi,
                    "source": source_pub,
                    "url": url,
                    "pages": pages  # Set of page numbers
                }
                
                # Debug: Zeige erste Quelle mit allen Feldern
                if len(sources_dict) == 1:
                    print(f"DEBUG: First source metadata (after Neo4j lookup):")
                    print(f"  - title: {paper_title[:50]}")
                    print(f"  - authors: {authors or 'NONE'}")
                    print(f"  - year: {year or 'NONE'}")
                    print(f"  - doi: {doi or 'NONE'}")
                    print(f"  - source: {source_pub or 'NONE'}")
                    print(f"  - url: {url or 'NONE'}")
                    print(f"  - page: {support.get('page', 'NONE')}")
            else:
                # Quelle existiert bereits, füge weitere Seiten hinzu
                if paper_title in sources_dict and support.get("page"):
                    sources_dict[paper_title]["pages"].add(support.get("page"))
        
        # Weise Zitationsnummern zu NACHDEM alle Quellen gesammelt wurden
        sorted_titles = sorted(sources_dict.keys())
        for i, title in enumerate(sorted_titles):
            source_to_number[title] = i + 1
        
        print(f"DEBUG: Collected {len(sources_dict)} valid sources")
        print(f"DEBUG: source_to_number mapping: {source_to_number}")
        
        # Füge inline Zitationen zum Text hinzu basierend auf Supports
        # Strategie: Verteile Zitationen für alle verwendeten Quellen über den Text
        # NEU: Mit Seitenzahlen und Abschnittsinformationen
        if supports and source_to_number:
            sentences = result["answer_text"].split('. ')
            cited_sources = set()
            new_sentences = []
            
            # Sammle alle Paper-Titel aus Supports MIT Seitenzahlen und Abschnitten
            support_details = []  # [(title, page, section), ...]
            for s in supports:
                title = s.get("paper_title", "").strip()
                paper_id = s.get("paper_id", "")
                page = s.get("page", None)
                section = s.get("section_title", "")
                
                # Verwende den gleichen Fallback wie oben
                if not title and paper_id:
                    title = f"Document {paper_id[:8]}"
                if title and title.lower() not in ['document', 'unknown']:  # Nur "document" ohne ID ausschließen
                    support_details.append({"title": title, "page": page, "section": section})
            
            # Gruppiere Support-Details nach Titel
            title_to_details = {}
            for detail in support_details:
                title = detail["title"]
                if title not in title_to_details:
                    title_to_details[title] = {"pages": set(), "sections": set()}
                if detail["page"]:
                    title_to_details[title]["pages"].add(detail["page"])
                if detail["section"]:
                    title_to_details[title]["sections"].add(detail["section"])
            
            unique_titles = list(title_to_details.keys())
            unique_titles.sort(key=lambda t: source_to_number.get(t, 999))
            print(f"DEBUG: Unique titles for inline citations: {len(unique_titles)} - {unique_titles[:3]}")
            
            # Verteile Zitationen HÄUFIGER über den Text (alle 3 Sätze)
            citation_frequency = 3
            print(f"DEBUG: Citation frequency: every {citation_frequency} sentences (total: {len(sentences)}, unique titles: {len(unique_titles)})")
            
            title_cycle_idx = 0
            for i, sentence in enumerate(sentences):
                new_sentences.append(sentence)
                
                # Füge Zitation alle N Sätze ein, rotiere durch Quellen
                if unique_titles and (i + 1) % citation_frequency == 0:
                    title = unique_titles[title_cycle_idx % len(unique_titles)]
                    if title in source_to_number:
                        cite_num = source_to_number[title]
                        
                        # Baue Zitation NUR mit Nummer auf (ohne Seitenzahlen)
                        citation_text = f"<super>[{cite_num}]</super>"
                        
                        new_sentences[-1] += citation_text
                        cited_sources.add(title)
                        print(f"DEBUG: Added inline citation [{cite_num}] after sentence {i+1}")
                        title_cycle_idx += 1
            
            # ENTFERNT: Restliche nicht-zitierte Quellen am Ende
            # Diese sind ohnehin im Quellenverzeichnis vorhanden und brauchen nicht
            # extra am Ende des Textes als massive Zitationenliste angehängt zu werden
            # remaining_titles = [t for t in unique_titles if t not in cited_sources and t in source_to_number]
            
            result["answer_text"] = '. '.join(new_sentences)
            print(f"DEBUG: Inline citations applied. Final text length: {len(result['answer_text'])}")
        else:
            print(f"DEBUG: No inline citations - supports: {len(supports)}, sources: {len(source_to_number)}")
        
        result["sources"] = list(sources_dict.values())
        
        # === WICHTIG: Löse Paper-ID-Zitationen auf ===
        # Der Text kann Zitationen der Form [Pxxx] oder [Fxxx] enthalten (Paper/Figure IDs)
        # Diese müssen wir in echte Quellen umwandeln
        
        # Extrahiere alle Paper-ID-Zitationen aus dem Text
        paper_id_pattern = r'\[P([a-f0-9\-]+)\]'
        paper_ids = re.findall(paper_id_pattern, result["answer_text"])
        print(f"DEBUG: Found {len(set(paper_ids))} unique paper IDs in text: {list(set(paper_ids))[:3]}")
        
        # Lade die Paper-Informationen für jede Paper-ID
        for paper_id in set(paper_ids):
            if paper_id and paper_id not in sources_dict:
                try:
                    # Hole Paper-Informationen aus Neo4j
                    paper_query = """
                    MATCH (p:Paper {paper_id: $paper_id})
                    RETURN p.title AS title, p.authors AS authors, p.year AS year, 
                           p.doi AS doi, p.source AS source, p.url AS url
                    """
                    paper_result = neo.run(paper_query, {"paper_id": paper_id})
                    
                    if paper_result and len(paper_result) > 0:
                        paper = paper_result[0]
                        title = paper.get("title", f"Paper {paper_id[:8]}").strip()
                        
                        if title and title not in sources_dict:
                            sources_dict[title] = {
                                "title": title,
                                "authors": paper.get("authors", ""),
                                "year": paper.get("year", ""),
                                "doi": paper.get("doi", ""),
                                "source": paper.get("source", ""),
                                "url": paper.get("url", ""),
                                "pages": set()
                            }
                            print(f"DEBUG: Loaded paper '{title[:50]}...' for ID {paper_id[:8]}")
                except Exception as e:
                    print(f"DEBUG: Failed to load paper {paper_id[:8]}: {e}")
        
        # Aktualisiere sources mit den neu geladenen Papieren
        result["sources"] = list(sources_dict.values())
        
        # Fallback: Wenn sources_dict noch immer leer ist, versuche, direkt aus Neo4j Papiere zu holen
        if not result["sources"] and neo:
            print(f"DEBUG: No sources from supports, trying Neo4j fallback...")
            try:
                fallback_query = """
                MATCH (p:Paper)
                WHERE p.title IS NOT NULL
                RETURN p.title AS title, p.authors AS authors, p.year AS year, p.doi AS doi, p.source AS source
                LIMIT 10
                """
                fallback_result = neo.run(fallback_query)
                
                if fallback_result:
                    for paper in fallback_result[:10]:  # Max 10 Papiere
                        title = paper.get("title", "").strip()
                        if title and title not in sources_dict:
                            sources_dict[title] = {
                                "title": title,
                                "authors": paper.get("authors", ""),
                                "year": paper.get("year", ""),
                                "doi": paper.get("doi", ""),
                                "source": paper.get("source", ""),
                                "pages": set()
                            }
                    result["sources"] = list(sources_dict.values())
                    print(f"DEBUG: Fallback loaded {len(result['sources'])} papers from Neo4j")
            except Exception as e:
                print(f"DEBUG: Fallback failed: {e}")
        
        print(f"DEBUG: Retrieved answer length: {len(result['answer_text'])} chars")
        print(f"DEBUG: Found {len(result['paragraphs'])} paragraphs, {len(result['figures'])} figures")
        print(f"DEBUG: Collected {len(result['sources'])} valid sources")
        if result['sources']:
            print(f"DEBUG: Source titles: {[s.get('title', 'NO_TITLE') for s in result['sources'][:5]]}")
        else:
            print(f"DEBUG: WARNING - No sources collected! Check if papers have valid titles.")
            print(f"DEBUG: Source titles: {[s.get('title', 'NO_TITLE') for s in result['sources'][:3]]}")
        
        # Versuche zusätzlich, die Konzeptbeschreibung zu holen
        concept_query = """
        MATCH (c:Concept)
        WHERE toLower(c.name) = toLower($name)
           OR toLower(c.name) CONTAINS toLower($name)
        OPTIONAL MATCH (c)-[r:SEMANTIC_RELATION]-(related:Concept)
        WITH c, collect(DISTINCT {name: related.name, type: r.relation_type}) AS rels
        RETURN c.description AS description, rels AS related_concepts
        LIMIT 1
        """
        concept_result = neo.run(concept_query, {"name": chapter_title})
        
        if concept_result and len(concept_result) > 0:
            result["concept_description"] = concept_result[0].get("description", "")
            result["related_concepts"] = concept_result[0].get("related_concepts", [])
            print(f"DEBUG: Found concept description and {len(result['related_concepts'])} related concepts")
    
    except Exception as e:
        print(f"ERROR: Error fetching content for chapter '{chapter_title}': {e}")
        import traceback
        traceback.print_exc()
    
    return result
    
    try:
        # Strategie 1: Exakte Suche nach Konzept mit passendem Namen
        concept_query = """
        MATCH (c:Concept)
        WHERE toLower(c.name) = toLower($name)
           OR toLower(c.name) CONTAINS toLower($name)
           OR ANY(alt IN c.alt_labels WHERE toLower(alt) = toLower($name))
        OPTIONAL MATCH (c)<-[:MENTIONS]-(p:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
        OPTIONAL MATCH (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(p)
        WITH c, p, paper, sec
        ORDER BY p.order_in_page
        RETURN c.concept_id AS concept_id,
               c.name AS concept_name,
               c.description AS concept_description,
               collect(DISTINCT {text: p.text, paper_title: paper.title, section: sec.title})[..5] AS paragraphs
        LIMIT 1
        """
        concept_result = neo.run(concept_query, {"name": chapter_title})
        
        print(f"DEBUG: Fetching content for chapter '{chapter_title}'")
        print(f"DEBUG: Query returned {len(concept_result)} results")
        
        if concept_result and len(concept_result) > 0:
            concept_data = concept_result[0]
            result["paragraphs"] = concept_data.get("paragraphs", [])
            result["concept_description"] = concept_data.get("concept_description", "")
            
            print(f"DEBUG: Found {len(result['paragraphs'])} paragraphs")
            print(f"DEBUG: Concept description: {result['concept_description'][:100] if result['concept_description'] else 'None'}")
            
            # Hole verwandte Konzepte
            related_query = """
            MATCH (c:Concept {concept_id: $concept_id})-[r:SEMANTIC_RELATION]-(related:Concept)
            RETURN related.name AS name, r.relation_type AS relation_type
            LIMIT 5
            """
            related_result = neo.run(related_query, {"concept_id": concept_data.get("concept_id")})
            result["related_concepts"] = related_result
            
            # Hole Figuren zum Konzept
            figure_query = """
            MATCH (c:Concept {concept_id: $concept_id})<-[:MENTIONS]-(p:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
            MATCH (paper)-[:HAS_FIGURE]->(f:Figure)
            WHERE f.page = p.page OR abs(f.page - p.page) <= 1
            RETURN DISTINCT f.caption AS caption, f.figure_label AS label
            LIMIT 3
            """
            figure_result = neo.run(figure_query, {"concept_id": concept_data.get("concept_id")})
            result["figures"] = figure_result
            
            print(f"DEBUG: Found {len(result['related_concepts'])} related concepts, {len(result['figures'])} figures")
        else:
            print(f"DEBUG: No exact concept match found for '{chapter_title}'")
            
            # Strategie 2: Fuzzy-Suche nach ähnlichen Konzepten
            fuzzy_concept_query = """
            MATCH (c:Concept)
            WITH c, 
                 CASE 
                   WHEN toLower(c.name) CONTAINS toLower($keyword) THEN 2
                   WHEN ANY(alt IN c.alt_labels WHERE toLower(alt) CONTAINS toLower($keyword)) THEN 1
                   ELSE 0
                 END AS relevance
            WHERE relevance > 0
            OPTIONAL MATCH (c)<-[:MENTIONS]-(p:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
            OPTIONAL MATCH (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(p)
            WITH c, p, paper, sec, relevance
            ORDER BY relevance DESC, p.order_in_page
            RETURN c.concept_id AS concept_id,
                   c.name AS concept_name,
                   c.description AS concept_description,
                   collect(DISTINCT {text: p.text, paper_title: paper.title, section: sec.title})[..5] AS paragraphs
            LIMIT 1
            """
            fuzzy_result = neo.run(fuzzy_concept_query, {"keyword": chapter_title})
            
            if fuzzy_result and len(fuzzy_result) > 0 and fuzzy_result[0].get("paragraphs"):
                concept_data = fuzzy_result[0]
                result["paragraphs"] = concept_data.get("paragraphs", [])
                result["concept_description"] = concept_data.get("concept_description", "")
                print(f"DEBUG: Fuzzy concept search found {len(result['paragraphs'])} paragraphs for similar concept '{concept_data.get('concept_name')}'")
                
                # Hole auch verwandte Konzepte
                if concept_data.get("concept_id"):
                    related_query = """
                    MATCH (c:Concept {concept_id: $concept_id})-[r:SEMANTIC_RELATION]-(related:Concept)
                    RETURN related.name AS name, r.relation_type AS relation_type
                    LIMIT 5
                    """
                    result["related_concepts"] = neo.run(related_query, {"concept_id": concept_data.get("concept_id")})
            else:
                print(f"DEBUG: Fuzzy concept search found no results")
                
                # Strategie 3: Volltext-Suche in Paragraphen
                # Teile den Kapiteltitel in Wörter und suche nach jedem
                keywords = chapter_title.split()
                if keywords:
                    main_keyword = max(keywords, key=len) if len(keywords) > 1 else chapter_title
                    fallback_query = """
                    MATCH (p:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
                    WHERE toLower(p.text) CONTAINS toLower($keyword)
                    OPTIONAL MATCH (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(p)
                    WITH p, paper, sec
                    ORDER BY p.order_in_page
                    LIMIT 5
                    RETURN collect({text: p.text, paper_title: paper.title, section: sec.title}) AS paragraphs
                    """
                    fallback_result = neo.run(fallback_query, {"keyword": main_keyword})
                    if fallback_result and len(fallback_result) > 0:
                        result["paragraphs"] = fallback_result[0].get("paragraphs", [])
                        print(f"DEBUG: Fulltext search found {len(result['paragraphs'])} paragraphs with keyword '{main_keyword}'")
                    else:
                        print(f"DEBUG: No content found for '{chapter_title}' even with fulltext search")
    
    except Exception as e:
        print(f"ERROR: Error fetching content for chapter '{chapter_title}': {e}")
        import traceback
        traceback.print_exc()
    
    return result

def generate_course_pdf(course: dict, output_path: str, neo: Neo4jClient = None, include_content: bool = True, cover_logo_bytes: bytes = None, learner_role: str = None) -> dict:
    """
    Generiert eine strukturierte PDF aus der Kursstruktur mit Inhalten aus dem Wissensgraphen.
    
    Args:
        course: Dict mit Kursname und Kapiteln
        output_path: Pfad für die Ausgabedatei
        neo: Neo4j Client für Inhaltsabruf (optional)
        include_content: Ob Inhalte aus dem Graph eingebunden werden sollen
        cover_logo_bytes: Optionales Logo als Bytes für das Deckblatt
        learner_role: Optional Zielgruppe/Rolle für die Inhaltsanpassung
        
    Returns:
        Dict mit 'path', 'supports', 'generated_text' für Citation Validation
    """
    # Speichere die learner_role für Zugriff in der PDF-Generierung
    generate_course_pdf._learner_role = learner_role
    
    # Sammle alle Supports und generierten Text für Citation Validation
    all_collected_supports = []
    all_generated_text = []
    all_generated_text_raw = []  # Für Citation Validierung VOR Bereinigung
    
    doc = SimpleDocTemplate(output_path, pagesize=A4, 
                           leftMargin=2.5*cm, rightMargin=2.5*cm,
                           topMargin=2.5*cm, bottomMargin=2.5*cm)
    
    story = []
    styles = getSampleStyleSheet()
    
    # Titel-Style
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.black,
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    # Kapitel-Style
    chapter_style = ParagraphStyle(
        'ChapterTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.black,
        spaceAfter=12,
        spaceBefore=20,
        fontName='Helvetica-Bold'
    )
    
    # Unterkapitel-Style
    section_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.black,
        spaceAfter=8,
        spaceBefore=10,
        fontName='Helvetica-Bold'
    )
    
    # Lernziel-Style
    objective_style = ParagraphStyle(
        'Objective',
        parent=styles['BodyText'],
        fontSize=11,
        leftIndent=20,
        spaceAfter=6,
        bulletIndent=10,
        fontName='Helvetica'
    )
    
    # Deckblatt-Styles
    cover_title_style = ParagraphStyle(
        'CoverTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=colors.black,
        spaceAfter=12,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    cover_subtitle_style = ParagraphStyle(
        'CoverSubtitle',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.black,
        spaceAfter=6,
        alignment=TA_CENTER,
        fontName='Helvetica'
    )
    cover_meta_style = ParagraphStyle(
        'CoverMeta',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.black,
        spaceAfter=4,
        alignment=TA_CENTER,
        fontName='Helvetica'
    )

    # Titelseite mit optionalem Logo
    cover_elements = []
    cover_elements.append(Spacer(1, 2*cm))

    if cover_logo_bytes:
        try:
            logo_img = Image(BytesIO(cover_logo_bytes), mask='auto')
            # Begrenze Logo-Kantenlänge und erhalte Seitenverhältnis
            logo_img._restrictSize(6*cm, 6*cm)
            logo_img.hAlign = 'CENTER'
            cover_elements.append(logo_img)
            cover_elements.append(Spacer(1, 1*cm))
        except Exception:
            pass

    cover_elements.append(Paragraph("Kursdokumentation", cover_subtitle_style))
    cover_elements.append(Spacer(1, 0.3*cm))
    cover_elements.append(Paragraph(course.get("Kursname", "Kurs"), cover_title_style))
    cover_elements.append(Spacer(1, 0.5*cm))
    cover_elements.append(Paragraph(f"Erstellt am: {datetime.datetime.now().strftime('%d.%m.%Y')}", cover_meta_style))
    cover_elements.append(Paragraph(f"Kapitel: {len(course.get('Kapitel', []))}", cover_meta_style))
    cover_elements.append(Spacer(1, 2.5*cm))

    story.extend(cover_elements)
    story.append(PageBreak())
    
    # Inhaltsverzeichnis
    story.append(Paragraph("Inhaltsverzeichnis", chapter_style))
    story.append(Spacer(1, 0.5*cm))
    
    toc_data = []
    for idx, kap in enumerate(course.get("Kapitel", []), 1):
        toc_data.append([f"Kapitel {idx}", kap.get("Titel", "")])
        for aidx, abschnitt in enumerate(kap.get("Abschnitte", []), 1):
            toc_data.append([f"  {idx}.{aidx}", abschnitt])
    
    if toc_data:
        toc_table = Table(toc_data, colWidths=[3*cm, 12*cm])
        toc_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(toc_table)
    
    story.append(PageBreak())
    
    # Content-Style mit Blocksatz
    content_style = ParagraphStyle(
        'Content',
        parent=styles['BodyText'],
        fontSize=11,
        spaceAfter=14,  # Mehr Abstand zwischen Absätzen
        spaceBefore=2,
        alignment=TA_JUSTIFY,  # Blocksatz
        fontName='Helvetica',
        leading=16  # Zeilenabstand
    )
    
    citation_style = ParagraphStyle(
        'Citation',
        parent=styles['Italic'],
        fontSize=9,
        textColor=colors.HexColor('#666666'),
        leftIndent=15,
        spaceAfter=8
    )
    
    # OPTION A: Sammle ERST alle Quellen aus allen Kapiteln, DANN nummeriere global
    print("DEBUG: Option A - Pre-scanning all chapters for sources...")
    all_sources = {}  # paper_title -> citation info
    
    # Scan 1: Gehe durch alle Kapitel und sammle Quellen (ohne Text zu generieren)
    for idx, kapitel in enumerate(course.get("Kapitel", []), 1):
        chapter_title = kapitel.get('Titel', '')
        retrieval_hints = kapitel.get("Retrieval_Hinweise", {})
        abschnitte = kapitel.get("Abschnitte", [])
        
        if include_content and neo:
            # Hole nur Metadaten aus allen Abschnitten
            if abschnitte:
                for aidx, abschnitt in enumerate(abschnitte, 1):
                    if abschnitt.strip():
                        section_hints = {}
                        section_key = str(aidx - 1)
                        if section_key in retrieval_hints:
                            section_hints[section_key] = retrieval_hints[section_key]
                        
                        section_content = fetch_content_for_chapter(
                            neo, chapter_title, 
                            retrieval_hints=section_hints,
                            section_title=abschnitt,
                            learner_role=learner_role
                        )
                        
                        # Sammle Quellen
                        if section_content.get("sources"):
                            for source in section_content["sources"]:
                                title = source.get("title", "")
                                if title and title.strip().lower() not in ['document', 'unknown'] and title not in all_sources:
                                    all_sources[title] = source
                                    print(f"DEBUG: Pre-scan: Added '{title[:40]}...'")
            else:
                # Kapitel ohne Abschnitte
                full_content = fetch_content_for_chapter(neo, chapter_title, retrieval_hints=retrieval_hints, learner_role=learner_role)
                if full_content.get("sources"):
                    for source in full_content["sources"]:
                        title = source.get("title", "")
                        if title and title.strip().lower() not in ['document', 'unknown'] and title not in all_sources:
                            all_sources[title] = source
                            print(f"DEBUG: Pre-scan: Added '{title[:40]}...'")
    
    # Weise globale Zitationsnummern zu NACHDEM alle Quellen gesammelt wurden
    sorted_all_sources = sorted(all_sources.keys())
    source_to_citation_number = {title: idx + 1 for idx, title in enumerate(sorted_all_sources)}
    print(f"DEBUG: Pre-scan complete. Total sources: {len(all_sources)}")
    print(f"DEBUG: Global citation numbers assigned: {list(source_to_citation_number.items())[:3]}")
    
    # Kapitel
    for idx, kapitel in enumerate(course.get("Kapitel", []), 1):
        chapter_title = kapitel.get('Titel', '')
        chapter_number = kapitel.get('Nummer', '').strip()
        
        # Baue Kapitelüberschrift: Mit Nummer falls vorhanden, sonst nur Titel
        if chapter_number:
            chapter_heading = f"{chapter_number} {chapter_title}"
        else:
            chapter_heading = chapter_title
        
        story.append(Paragraph(chapter_heading, chapter_style))
        story.append(Spacer(1, 0.3*cm))
        
        # Hole Inhalte aus dem Wissensgraphen
        content = {}
        if include_content and neo:
            # Hole Retrieval-Hinweise für dieses Kapitel
            retrieval_hints = kapitel.get("Retrieval_Hinweise", {})
            
            print(f"DEBUG: Fetching content for chapter {idx}: '{chapter_title}' (neo={neo}, include_content={include_content})")
            content = fetch_content_for_chapter(neo, chapter_title, retrieval_hints=retrieval_hints, learner_role=learner_role)
            print(f"DEBUG: Content retrieved: answer_text length={len(content.get('answer_text', ''))}, {len(content.get('paragraphs', []))} paragraphs, {len(content.get('figures', []))} figures")
            
            # Sammle Supports und Text für Citation Validation
            if content.get('paragraphs'):
                all_collected_supports.extend(content['paragraphs'])
            if content.get('figures'):
                all_collected_supports.extend(content['figures'])
            
            # Sammle Quellen für das Quellenverzeichnis
            if content.get("sources"):
                for source in content["sources"]:
                    title = source.get("title", "")
                    # Filtere nur komplett leere oder 'unknown' Titel
                    if not title or title.strip().lower() in ['unknown']:
                        continue
                    if title and title not in all_sources:
                        all_sources[title] = source
        else:
            print(f"DEBUG: Skipping content fetch (include_content={include_content}, neo={neo})")
        
        # Konzeptbeschreibung (falls vorhanden)
        if content.get("concept_description"):
            story.append(Paragraph("Überblick:", section_style))
            story.append(Paragraph(html.escape(content["concept_description"]), content_style))
            story.append(Spacer(1, 0.3*cm))
        
        # Lernziele
        if kapitel.get("Lernziele"):
            story.append(Paragraph("Lernziele:", section_style))
            for ziel in kapitel.get("Lernziele", []):
                if ziel.strip():
                    story.append(Paragraph(f"• {html.escape(ziel)}", objective_style))
            story.append(Spacer(1, 0.5*cm))
        
        # Initialisiere chapter_refs für Zitationen
        chapter_refs = []
        cited_titles = set()  # Tracking welche Titel bereits zitiert wurden
        
        # Abschnitte mit Inhalten
        abschnitte = kapitel.get("Abschnitte", [])
        retrieval_hints = kapitel.get("Retrieval_Hinweise", {})
        
        if abschnitte and include_content and neo:
            # Filtere nur Abschnitte mit Titel
            non_empty_sections = [(i, sec) for i, sec in enumerate(abschnitte, 1) if sec.strip()]
            
            print(f"DEBUG: Processing {len(non_empty_sections)} non-empty sections")
            
            if non_empty_sections:
                for section_idx, (aidx, abschnitt) in enumerate(non_empty_sections):
                    # Abschnittstitel
                    story.append(Paragraph(f"{idx}.{aidx} {abschnitt}", section_style))
                    
                    # Hole spezifische Hinweise für diesen Abschnitt
                    section_hints = {}
                    section_key = str(aidx - 1)  # Index im Array
                    if section_key in retrieval_hints:
                        section_hints[section_key] = retrieval_hints[section_key]
                    
                    # Hole Inhalt speziell für diesen Abschnitt
                    print(f"DEBUG: Fetching content for section {aidx}: '{abschnitt}'")
                    section_content = fetch_content_for_chapter(
                        neo, 
                        chapter_title, 
                        retrieval_hints=section_hints,
                        section_title=abschnitt,
                        learner_role=learner_role
                    )
                    
                    section_text = section_content.get("answer_text", "")
                    section_text_raw = section_content.get("answer_text_raw", section_text)

                    # Remap section-local citation numbers to global bibliography numbers
                    local_titles = sorted([
                        s.get("title", "").strip()
                        for s in section_content.get("sources", [])
                        if s.get("title", "").strip()
                    ])
                    section_text = _remap_inline_citations(section_text, local_titles, source_to_citation_number)
                    
                    # Sammle Supports und Text für Citation Validation
                    if section_content.get('paragraphs'):
                        all_collected_supports.extend(section_content['paragraphs'])
                    if section_content.get('figures'):
                        all_collected_supports.extend(section_content['figures'])
                    if section_text:
                        all_generated_text.append(section_text)
                    if section_text_raw:
                        all_generated_text_raw.append(section_text_raw)  # Speichere RAW Text
                    
                    # Entferne Überschrift am Anfang falls LLM sie wiederholt hat
                    # Z.B. wenn Abschnittstitel "Was ist KI?" ist und Text mit "Was ist KI?" beginnt
                    if section_text and abschnitt:
                        # Entferne Abschnittstitel am Anfang (mit/ohne Nummerierung)
                        # Pattern: Optional "1.1 " oder ähnlich, dann der Titel
                        pattern = r'^(?:\d+\.?\d*\s+)?' + re.escape(abschnitt.strip()) + r'\s*\n*'
                        section_text = re.sub(pattern, '', section_text, flags=re.IGNORECASE | re.MULTILINE)
                    
                    # Sammle ZUERST Quellen aus diesem Abschnitt (vor dem Paragraph-Loop!)
                    if section_content.get("sources"):
                        print(f"DEBUG: Section {aidx} has {len(section_content['sources'])} sources")
                        for source in section_content["sources"]:
                            title = source.get("title", "")
                            # Filtere ungültige Titel
                            if not title or title.strip().lower() in ['document', 'unknown']:
                                print(f"DEBUG: Skipping invalid source title: '{title}'")
                                continue
                            if title and title not in all_sources:
                                all_sources[title] = source
                                print(f"DEBUG: Added source: {title[:50]}...")
                            if title and title not in cited_titles:
                                cited_titles.add(title)
                                # Nutze die GLOBALE Zitationsnummer (bereits berechnet im Pre-Scan)
                                if title in source_to_citation_number:
                                    ref_num = source_to_citation_number[title]
                                    chapter_refs.append(ref_num)
                                    print(f"DEBUG: Using global citation number {ref_num} for '{title[:30]}...'")
                    else:
                        print(f"DEBUG: Section {aidx} has NO sources!")
                    
                    if section_text:
                        # Teile in Absätze
                        paragraphs_text = [p.strip() for p in section_text.split('\n\n') if p.strip()]
                        print(f"DEBUG: Section {aidx} got {len(paragraphs_text)} paragraphs")
                        print(f"DEBUG: chapter_refs before adding citations: {chapter_refs}")
                        
                        for para_idx, para_text in enumerate(paragraphs_text):
                            story.append(Paragraph(_safe_para(para_text), content_style))
                    else:
                        story.append(Paragraph("(Keine Inhalte für diesen Abschnitt gefunden)", content_style))
                    
                    story.append(Spacer(1, 0.3*cm))
        
        elif abschnitte and not include_content:
            # Nur Abschnitte ohne Inhalt
            story.append(Paragraph("Abschnitte:", section_style))
            for aidx, abschnitt in enumerate(abschnitte, 1):
                if abschnitt.strip():
                    story.append(Paragraph(f"{idx}.{aidx} {abschnitt}", objective_style))
            story.append(Spacer(1, 0.5*cm))
        
        elif not abschnitte and include_content and neo:
            # Kein Abschnitte, aber Inhalt gewünscht - hole für gesamtes Kapitel
            print(f"DEBUG: No sections, fetching content for entire chapter")
            full_content = fetch_content_for_chapter(neo, chapter_title, retrieval_hints=retrieval_hints, learner_role=learner_role)
            answer_text = full_content.get("answer_text", "")

            # Remap local citation numbers to global bibliography numbers
            full_local_titles = sorted([
                s.get("title", "").strip()
                for s in full_content.get("sources", [])
                if s.get("title", "").strip()
            ])
            answer_text = _remap_inline_citations(answer_text, full_local_titles, source_to_citation_number)

            if answer_text:
                # Inhalt ohne Abschnitte - als Ganzes einfügen
                story.append(Paragraph("Inhalt:", section_style))
                paragraphs_text = answer_text.split('\n\n')
                for para_idx, para_text in enumerate(paragraphs_text):
                    if para_text.strip():
                        story.append(Paragraph(_safe_para(para_text.strip()), content_style))
                story.append(Spacer(1, 0.5*cm))
                
                # Sammle Quellen
                if full_content.get("sources"):
                    for source in full_content["sources"]:
                        title = source.get("title", "")
                        # Filtere ungültige Titel
                        if not title or title.strip().lower() in ['document', 'unknown']:
                            continue
                        if title and title not in all_sources:
                            all_sources[title] = source
                        if title and title not in cited_titles:
                            cited_titles.add(title)
                            # Nutze die GLOBALE Zitationsnummer (bereits berechnet im Pre-Scan)
                            if title in source_to_citation_number:
                                ref_num = source_to_citation_number[title]
                                chapter_refs.append(ref_num)
        
        # Pagebreak nach jedem Kapitel (außer dem letzten)
        if idx < len(course.get("Kapitel", [])):
            story.append(PageBreak())
    
    # Quellenverzeichnis am Ende
    story.append(PageBreak())
    story.append(Paragraph("Literaturverzeichnis", chapter_style))
    story.append(Spacer(1, 0.5*cm))
    
    print(f"DEBUG: Creating bibliography with {len(all_sources)} sources")
    if all_sources:
        print(f"DEBUG: Source titles: {list(all_sources.keys())[:3]}")
    
    # Erstelle wissenschaftliches Literaturverzeichnis im APA-Stil
    reference_style = ParagraphStyle(
        'Reference',
        parent=styles['BodyText'],
        fontSize=10,
        leftIndent=36,  # 0.5 inch für hängenden Einzug
        firstLineIndent=-36,
        spaceAfter=12,
        spaceBefore=0,
        fontName='Helvetica',
        alignment=TA_JUSTIFY
    )
    
    if all_sources:
        # Sortiere alphabetisch nach Titel (APA: normalerweise nach Autor, aber wir haben oft keine Autoren)
        sorted_sources = sorted(all_sources.items(), key=lambda x: x[0])
        
        for idx, (title, source_info) in enumerate(sorted_sources, 1):
            # APA Format: Autor(en). (Jahr). Titel. Quelle. DOI/URL
            citation_parts = []

            # Zitatnummer in eckigen Klammern - Nutze die globale Nummer
            citation_num = source_to_citation_number.get(title, idx)
            citation_parts.append(f"[{citation_num}]")

            # Autoren (APA: Nachname, Initialen.)
            authors = (source_info.get("authors") or "").strip()
            if authors:
                authors = re.sub(r'\s*\.+\s*', '.', authors).strip()
                if ";" in authors:
                    author_list = [a.strip().rstrip('.') for a in authors.split(";")]
                    authors = ", ".join(author_list)
                suffix = "" if authors.endswith('.') else "."
                citation_parts.append(f"{html.escape(authors)}{suffix}")

            # Jahr in Klammern (APA)
            year = (source_info.get("year") or "").strip()
            if year:
                citation_parts.append(f"({html.escape(year)}).")

            # Titel kursiv
            citation_parts.append(f"<i>{html.escape(title)}</i>.")

            # Quelle/Publikation (z.B. Journal, Konferenz)
            source_pub = (source_info.get("source") or "").strip()
            if source_pub:
                citation_parts.append(f"{html.escape(source_pub)}.")

            # Seitenzahlen (falls vorhanden)
            pages = source_info.get("pages", set())
            if pages:
                sorted_pages = sorted(pages)
                if len(sorted_pages) == 1:
                    citation_parts.append(f"S. {sorted_pages[0]}.")
                elif len(sorted_pages) <= 5:
                    citation_parts.append(f"S. {', '.join(map(str, sorted_pages))}.")
                else:
                    citation_parts.append(f"S. {sorted_pages[0]}–{sorted_pages[-1]}.")

            # DOI (APA: https://doi.org/...)
            doi = (source_info.get("doi") or "").strip()
            if doi:
                doi_clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
                citation_parts.append(f"https://doi.org/{html.escape(doi_clean)}")
            elif (source_info.get("url") or "").strip():
                url = (source_info.get("url") or "").strip()
                citation_parts.append(f"Abgerufen von {html.escape(url)}")

            citation_text = " ".join(citation_parts)
            if len(citation_parts) <= 2:  # Nur [n] und Titel
                citation_text = f"[{citation_num}] {html.escape(title)}. (Details nicht verfügbar)"

            story.append(Paragraph(citation_text, reference_style))
    else:
        print("DEBUG: No sources to display in bibliography!")
        story.append(Paragraph("Keine Quellen aus dem Wissensgraph verwendet.", objective_style))
    
    # Seitenzahlen ab erster Inhaltsseite (Deckblatt + TOC ohne Nummer)
    def add_page_number(canvas_obj, doc_obj):
        page_num = canvas_obj.getPageNumber()
        if page_num <= 2:
            return  # Kein Deckblatt/TOC nummerieren
        canvas_obj.saveState()
        canvas_obj.setFillColor(colors.black)
        canvas_obj.setFont("Helvetica", 9)
        display_num = page_num - 2  # Inhalte bei 1 starten lassen
        canvas_obj.drawRightString(doc_obj.pagesize[0] - 2*cm, 1.5*cm, str(display_num))
        canvas_obj.restoreState()

    # PDF erstellen
    doc.build(story, onLaterPages=add_page_number)
    return {
        'path': output_path,
        'supports': all_collected_supports,
        'generated_text': '\n\n'.join(all_generated_text),
        'generated_text_raw': '\n\n'.join(all_generated_text_raw)  # RAW Text mit [Pxxx] Zitationen
    }

def show_course_generator():
    # Neo4j Client initialisieren (wird für Graph-Zugriff und PDF-Generierung benötigt)
    from pathlib import Path
    import json
    import re
    
    neo = Neo4jClient()
    
    # Initialisiere Session State
    if "course_struct" not in st.session_state:
        st.session_state["course_struct"] = {
            "Kursname": "Mein Kurs",
            "Kapitel": []
        }

    course = st.session_state["course_struct"]

    # Kursname bearbeiten
    st.subheader("Kursname")
    course["Kursname"] = st.text_input("Kursname", value=course["Kursname"], label_visibility="collapsed")

    # Zielgruppe / Rolle bearbeiten
    st.markdown("---")
    st.subheader("Zielgruppe & Personalisierung")
    learner_role = st.text_input(
        "Rolle / Zielgruppe (optional)",
        value=st.session_state.get("coursegen_learner_role", ""),
        placeholder="z.B. Vertriebsmitarbeiter, Lagerarbeiter, Führungskraft, etc.",
        help="Gib hier eine Rolle oder Zielgruppe ein. Die Kursinhalte werden dann spezifisch für diese Gruppe aufbereitet. Leer lassen für allgemeine Darstellung."
    )
    st.session_state["coursegen_learner_role"] = learner_role
    st.caption(f"Aktuelle Zielgruppe: {learner_role if learner_role.strip() else '(Keine Angabe - allgemeine Darstellung)'}")

    st.markdown("---")
    st.subheader("Kapitel")
    
    # Kapitel hinzufügen
    if st.button("Neues Kapitel hinzufügen"):
        course["Kapitel"].append({
            "Nummer": "",
            "Titel": "Kapitel",
            "Lernziele": [],
            "Abschnitte": [],
            "Retrieval_Hinweise": {}
        })

    # Kapitel bearbeiten

    for idx, kapitel in enumerate(course["Kapitel"]):
        # Stelle sicher, dass "Nummer" existiert (für bestehende Kapitel)
        if "Nummer" not in kapitel:
            kapitel["Nummer"] = ""
        
        # Zeige Kapitelnummer im Expander-Titel falls vorhanden
        chapter_num = kapitel.get('Nummer', '').strip()
        if chapter_num:
            expander_title = f"{chapter_num}: {kapitel['Titel']}"
        else:
            expander_title = f"Kapitel: {kapitel['Titel']}"
        
        with st.expander(expander_title, expanded=True):
            col_num, col_title = st.columns([1, 3])
            with col_num:
                kapitel["Nummer"] = st.text_input(
                    "Nummer", 
                    value=kapitel.get("Nummer", ""), 
                    key=f"nummer_{idx}",
                    placeholder="z.B. 1, 2.1, A",
                    help="Optional: Kapitelnummer oder Bezeichnung"
                )
            with col_title:
                kapitel["Titel"] = st.text_input(
                    "Kapitel-Titel", 
                    value=kapitel["Titel"], 
                    key=f"titel_{idx}"
                )

            # Lernziele bearbeiten
            st.markdown("**Lernziele:**")
            for lidx, ziel in enumerate(kapitel["Lernziele"]):
                kapitel["Lernziele"][lidx] = st.text_input(f"Lernziel {lidx+1}", value=ziel, key=f"ziel_{idx}_{lidx}")

            col_z1, col_z2 = st.columns(2)
            with col_z1:
                if st.button(f"Lernziel hinzufügen", key=f"add_ziel_{idx}"):
                    kapitel["Lernziele"].append("")
                    st.rerun()
            with col_z2:
                if kapitel["Lernziele"]:
                    if st.button(f"Letztes Lernziel entfernen", key=f"del_ziel_{idx}"):
                        kapitel["Lernziele"].pop()
                        st.rerun()

            # Abschnitte bearbeiten
            st.markdown("**Abschnitte (Unterkapitel):**")
            # Initialisiere Retrieval_Hinweise falls nicht vorhanden
            if "Retrieval_Hinweise" not in kapitel:
                kapitel["Retrieval_Hinweise"] = {}
            
            for aidx, abschnitt in enumerate(kapitel["Abschnitte"]):
                # Abschnittstitel
                kapitel["Abschnitte"][aidx] = st.text_input(
                    f"Abschnitt {aidx+1} - Titel", 
                    value=abschnitt, 
                    key=f"abs_{idx}_{aidx}",
                    help="Titel des Abschnitts"
                )
                
                # Drei gleichwertige Fokus-Felder für Retrieval
                st.markdown(f"*Retrieval-Fokus für Abschnitt {aidx+1}:*")
                
                # Initialisiere dict für diesen Abschnitt falls nicht vorhanden
                if str(aidx) not in kapitel["Retrieval_Hinweise"]:
                    kapitel["Retrieval_Hinweise"][str(aidx)] = {
                        "fokus1": "",
                        "fokus2": "",
                        "fokus3": ""
                    }
                
                col_r1, col_r2, col_r3 = st.columns(3)
                
                with col_r1:
                    # Fokus-Feld 1
                    current_fokus1 = kapitel["Retrieval_Hinweise"][str(aidx)].get("fokus1", "") if isinstance(kapitel["Retrieval_Hinweise"][str(aidx)], dict) else ""
                    fokus1 = st.text_input(
                        f"Fokus 1",
                        value=current_fokus1,
                        key=f"fokus1_{idx}_{aidx}",
                        placeholder="z.B. 'praktische Anwendungen'",
                        help="Erster Schwerpunkt für die Inhaltssuche"
                    )
                    if isinstance(kapitel["Retrieval_Hinweise"][str(aidx)], dict):
                        kapitel["Retrieval_Hinweise"][str(aidx)]["fokus1"] = fokus1
                    else:
                        kapitel["Retrieval_Hinweise"][str(aidx)] = {"fokus1": fokus1, "fokus2": "", "fokus3": ""}
                
                with col_r2:
                    # Fokus-Feld 2
                    current_fokus2 = kapitel["Retrieval_Hinweise"][str(aidx)].get("fokus2", "") if isinstance(kapitel["Retrieval_Hinweise"][str(aidx)], dict) else ""
                    fokus2 = st.text_input(
                        f"Fokus 2",
                        value=current_fokus2,
                        key=f"fokus2_{idx}_{aidx}",
                        placeholder="z.B. 'mathematische Grundlagen'",
                        help="Zweiter Schwerpunkt für die Inhaltssuche"
                    )
                    if isinstance(kapitel["Retrieval_Hinweise"][str(aidx)], dict):
                        kapitel["Retrieval_Hinweise"][str(aidx)]["fokus2"] = fokus2
                
                with col_r3:
                    # Fokus-Feld 3
                    current_fokus3 = kapitel["Retrieval_Hinweise"][str(aidx)].get("fokus3", "") if isinstance(kapitel["Retrieval_Hinweise"][str(aidx)], dict) else ""
                    fokus3 = st.text_input(
                        f"Fokus 3",
                        value=current_fokus3,
                        key=f"fokus3_{idx}_{aidx}",
                        placeholder="z.B. 'historischer Kontext'",
                        help="Dritter Schwerpunkt für die Inhaltssuche"
                    )
                    if isinstance(kapitel["Retrieval_Hinweise"][str(aidx)], dict):
                        kapitel["Retrieval_Hinweise"][str(aidx)]["fokus3"] = fokus3
                
                st.markdown("---")  # Trenner zwischen Abschnitten

            col_a1, col_a2 = st.columns(2)
            with col_a1:
                if st.button(f"Abschnitt hinzufügen", key=f"add_abs_{idx}"):
                    kapitel["Abschnitte"].append("")
                    st.rerun()
            with col_a2:
                if kapitel["Abschnitte"]:
                    if st.button(f"Letzten Abschnitt entfernen", key=f"del_abs_{idx}"):
                        kapitel["Abschnitte"].pop()
                        st.rerun()

            # Kapitel entfernen
            if st.button(f"Kapitel entfernen", key=f"del_kap_{idx}"):
                course["Kapitel"].pop(idx)
                st.rerun()

    st.markdown("---")
    st.subheader("Vorschau Kursstruktur")
    st.write(course)
    
    st.markdown("---")
    st.subheader("Kurs als PDF exportieren")
    
    pdf_filename = st.text_input(
        "PDF-Dateiname", 
        value=f"{course['Kursname'].replace(' ', '_')}_Kurs.pdf",
        help="Name der zu erstellenden PDF-Datei"
    )
    
    logo_file = st.file_uploader(
        "Logo für Deckblatt (optional)",
        type=["png", "jpg", "jpeg"],
        help="Quadratische Logos wirken am besten (z.B. 512x512)."
    )
    logo_bytes = logo_file.getvalue() if logo_file else None
    
    # Inhalte aus Wissensgraph werden standardmäßig immer eingefügt
    include_content = True
    
    col_btn1, col_btn2, col_btn3 = st.columns(3)
    with col_btn1:
        if st.button("PDF generieren", type="primary", key="gen_pdf"):
            if not course.get("Kapitel"):
                st.warning("Bitte füge mindestens ein Kapitel hinzu, bevor du die PDF generierst.")
            else:
                from pathlib import Path
                
                exports_dir = Path(__file__).parent.parent / "exports"
                exports_dir.mkdir(exist_ok=True)
                output_path = exports_dir / pdf_filename
                
                try:
                    # Generiere PDF
                    with st.spinner("Generiere Kurs-PDF..."):
                        pdf_result = generate_course_pdf(
                            course, 
                            str(output_path),
                            neo=neo if include_content else None,
                            include_content=include_content,
                            cover_logo_bytes=logo_bytes,
                            learner_role=learner_role if learner_role.strip() else None
                        )
                    
                    result_path = pdf_result['path']
                    
                    st.success(f"PDF erfolgreich erstellt: {output_path.name}")
                    
                    # Download Button
                    st.session_state["pdf_result_path"] = result_path
                    st.session_state["pdf_output_name"] = output_path.name
                    
                except Exception as e:
                    st.error(f"Fehler beim Erstellen der PDF: {e}")
                    import traceback
                    st.code(traceback.format_exc())
    
    with col_btn2:
        if st.button("Kurs speichern", key="save_course"):
            if not course.get("Kapitel"):
                st.warning("Der Kurs hat keine Kapitel zum Speichern.")
            else:
                exports_dir = Path(__file__).parent.parent / "exports"
                exports_dir.mkdir(exist_ok=True)
                
                # Erstelle Dateinamen aus Kursnamen
                safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", course.get("Kursname", "Kurs"))[:50]
                course_file = exports_dir / f"{safe_name}_course.json"
                
                try:
                    with open(course_file, 'w', encoding='utf-8') as f:
                        json.dump(course, f, ensure_ascii=False, indent=2)
                    st.success(f"Kurs gespeichert: {course_file.name}")
                except Exception as e:
                    st.error(f"Fehler beim Speichern: {e}")
    
    with col_btn3:
        if "pdf_result_path" in st.session_state:
            with open(st.session_state["pdf_result_path"], "rb") as f:
                st.download_button(
                    label="PDF herunterladen",
                    data=f.read(),
                    file_name=st.session_state["pdf_output_name"],
                    mime="application/pdf",
                    key="dl_pdf"
                )
    
    # === Sprechtexte für Video/Podcast ===
    st.divider()
    st.subheader("Sprechtexte für Video- und Podcast-Produktion")
    st.markdown("Generiere optimierte Sprechtexte für Synthesia, ElevenLabs oder andere KI-Sprachsynthese-Systeme.")
    
    if not course.get("Kapitel"):
        st.info("Bitte erstelle zuerst einen Kurs im oberen Abschnitt.")
    else:
        col_script1, col_script2 = st.columns([1, 1])
        
        with col_script1:
            if st.button("Sprechtexte generieren", key="gen_scripts"):
                try:
                    from pathlib import Path
                    from src.speaker_script import generate_speaker_script, export_speaker_script
                    
                    # Hole aktuelle Werte
                    current_course_name = course.get("Kursname", "Kurs")
                    current_learner_role = st.session_state.get("coursegen_learner_role", "")
                    
                    with st.spinner("Generiere Sprechtexte..."):
                        script_data = generate_speaker_script(
                            course_struct=course,
                            course_name=current_course_name,
                            learner_role=current_learner_role if current_learner_role.strip() else None
                        )
                    
                    # Exportiere
                    exports_dir = Path(__file__).parent.parent / "exports"
                    exports_dir.mkdir(exist_ok=True)
                    
                    result = export_speaker_script(
                        script_data=script_data,
                        output_dir=str(exports_dir),
                        course_name=current_course_name
                    )
                    
                    st.success("Sprechtexte erfolgreich erstellt!")
                    st.write(f"**Gesamtdauer:** {script_data['total_duration']}")
                    
                    # Download-Buttons
                    col_dl1, col_dl2 = st.columns(2)
                    
                    with col_dl1:
                        with open(result["json_path"], "r", encoding="utf-8") as f:
                            st.download_button(
                                label="JSON herunterladen",
                                data=f.read(),
                                file_name=result["json_filename"],
                                mime="application/json",
                                key="dl_json"
                            )
                    
                    with col_dl2:
                        with open(result["markdown_path"], "r", encoding="utf-8") as f:
                            st.download_button(
                                label="Markdown herunterladen",
                                data=f.read(),
                                file_name=result["markdown_filename"],
                                mime="text/markdown",
                                key="dl_md"
                            )
                    
                    # Preview
                    with st.expander("Vorschau"):
                        st.markdown(script_data["markdown"][:2000] + "\n...*[gekürzt]*")
                
                except Exception as e:
                    st.error(f"Fehler beim Generieren der Sprechtexte: {e}")
                    import traceback
                    st.code(traceback.format_exc())
