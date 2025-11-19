
import streamlit as st
from src.neo import Neo4jClient
from src.agent import answer_query
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from pathlib import Path
import datetime

def fetch_content_for_chapter(neo: Neo4jClient, chapter_title: str, topic: str = None) -> dict:
    """
    Holt relevante Inhalte aus dem Wissensgraphen für ein Kapitel.
    Nutzt das bewährte Retrieval-System aus dem Fragen-Tab.
    
    Args:
        neo: Neo4j Client
        chapter_title: Titel des Kapitels
        topic: Optional das Topic für bessere Zuordnung
        
    Returns:
        Dict mit answer_text, paragraphs, figures, related_concepts
    """
    result = {
        "answer_text": "",
        "paragraphs": [], 
        "figures": [], 
        "related_concepts": [], 
        "concept_description": ""
    }
    
    try:
        # Erstelle eine strukturierte Frage für das Retrieval
        query = f"Erkläre das Konzept '{chapter_title}' ausführlich und didaktisch verständlich für Studierende."
        
        print(f"DEBUG: Fetching content for chapter '{chapter_title}' using answer_query")
        
        # Nutze das bewährte Retrieval-System
        response = answer_query(
            query=query,
            neo=neo,
            web_mode="off",  # Nur Wissensgraph nutzen
            k_paragraphs=24,
            k_figures=8,
            use_concept_retrieval=True
        )
        
        # Extrahiere die Antwort (bereits gut strukturiert und in deutscher Sprache)
        result["answer_text"] = response.get("answer", "")
        
        # Entferne Markdown-Formatierungen für saubere Vorlesungsunterlagen
        import re
        
        # Entferne alle Arten von Quellenverweisen, die vom System eingefügt wurden
        # z.B. [paper_id: 123], [1], [source: ...], etc.
        result["answer_text"] = re.sub(r'\[paper_id:\s*\d+\]', '', result["answer_text"])
        result["answer_text"] = re.sub(r'\[source:[^\]]+\]', '', result["answer_text"])
        result["answer_text"] = re.sub(r'\[\d+\]', '', result["answer_text"])  # Entferne alte numerische Referenzen
        result["answer_text"] = re.sub(r'\[fig_id:\s*\d+\]', '', result["answer_text"])
        result["answer_text"] = re.sub(r'\[Figure\s+\d+\]', '', result["answer_text"])
        
        # Entferne **fett** Formatierung
        result["answer_text"] = result["answer_text"].replace("**", "")
        # Entferne *kursiv* Formatierung (aber behalte einzelne * wenn sie nicht für Formatierung sind)
        result["answer_text"] = re.sub(r'\*([^\*]+)\*', r'\1', result["answer_text"])
        # Entferne Markdown-Überschriften (###, ##, #)
        result["answer_text"] = re.sub(r'^#{1,6}\s+', '', result["answer_text"], flags=re.MULTILINE)
        # Entferne andere Markdown-Elemente
        result["answer_text"] = result["answer_text"].replace("___", "")
        result["answer_text"] = result["answer_text"].replace("---", "")
        
        # Bereinige mehrfache Leerzeichen, die durch Entfernung entstanden sind
        result["answer_text"] = re.sub(r'\s+', ' ', result["answer_text"])
        result["answer_text"] = re.sub(r'\n\s*\n\s*\n+', '\n\n', result["answer_text"])  # Max 2 Zeilenumbrüche
        
        # Extrahiere die Supports (Paragraphen und Figuren)
        supports = response.get("supports", [])
        
        # Sammle Quellen mit vollständigen Informationen
        sources_dict = {}  # paper_title -> full citation info
        
        for support in supports:
            paper_title = support.get("paper_title", "")
            
            if support.get("type") == "paragraph":
                result["paragraphs"].append({
                    "text": support.get("text", ""),
                    "paper_title": paper_title,
                    "section": support.get("section_title", ""),
                    "score": support.get("score", 0)
                })
            elif support.get("type") == "figure":
                result["figures"].append({
                    "caption": support.get("caption", ""),
                    "label": support.get("figure_label", ""),
                    "paper_title": paper_title
                })
            
            # Sammle eindeutige Quellen
            if paper_title and paper_title not in sources_dict:
                sources_dict[paper_title] = {
                    "title": paper_title,
                    "authors": support.get("authors", ""),
                    "year": support.get("year", ""),
                    "doi": support.get("doi", ""),
                    "source": support.get("source", "")
                }
        
        result["sources"] = list(sources_dict.values())
        
        print(f"DEBUG: Retrieved answer length: {len(result['answer_text'])} chars")
        print(f"DEBUG: Found {len(result['paragraphs'])} paragraphs, {len(result['figures'])} figures")
        
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

def generate_course_pdf(course: dict, output_path: str, neo: Neo4jClient = None, include_content: bool = True) -> str:
    """
    Generiert eine strukturierte PDF aus der Kursstruktur mit Inhalten aus dem Wissensgraphen.
    
    Args:
        course: Dict mit Kursname und Kapiteln
        output_path: Pfad für die Ausgabedatei
        neo: Neo4j Client für Inhaltsabruf (optional)
        include_content: Ob Inhalte aus dem Graph eingebunden werden sollen
        
    Returns:
        Pfad zur erstellten PDF
    """
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
        textColor=colors.HexColor('#1f4788'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    # Kapitel-Style
    chapter_style = ParagraphStyle(
        'ChapterTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#1f4788'),
        spaceAfter=12,
        spaceBefore=20,
        fontName='Helvetica-Bold'
    )
    
    # Unterkapitel-Style
    section_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#2c5aa0'),
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
    
    # Titelseite
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph(course.get("Kursname", "Kurs"), title_style))
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(f"Erstellt am: {datetime.datetime.now().strftime('%d.%m.%Y')}", styles['Normal']))
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
    
    # Content-Style
    content_style = ParagraphStyle(
        'Content',
        parent=styles['BodyText'],
        fontSize=11,
        spaceAfter=10,
        alignment=TA_LEFT,
        fontName='Helvetica'
    )
    
    citation_style = ParagraphStyle(
        'Citation',
        parent=styles['Italic'],
        fontSize=9,
        textColor=colors.HexColor('#666666'),
        leftIndent=15,
        spaceAfter=8
    )
    
    # Sammle alle Quellen während der Kapitelgenerierung mit vollständigen Informationen
    all_sources = {}  # paper_title -> citation info
    source_counter = {}  # paper_title -> citation number
    
    # Kapitel
    for idx, kapitel in enumerate(course.get("Kapitel", []), 1):
        chapter_title = kapitel.get('Titel', '')
        story.append(Paragraph(f"Kapitel {idx}: {chapter_title}", chapter_style))
        story.append(Spacer(1, 0.3*cm))
        
        # Hole Inhalte aus dem Wissensgraphen
        content = {}
        if include_content and neo:
            print(f"DEBUG: Fetching content for chapter {idx}: '{chapter_title}' (neo={neo}, include_content={include_content})")
            content = fetch_content_for_chapter(neo, chapter_title)
            print(f"DEBUG: Content retrieved: answer_text length={len(content.get('answer_text', ''))}, {len(content.get('paragraphs', []))} paragraphs, {len(content.get('figures', []))} figures")
            
            # Sammle Quellen für das Quellenverzeichnis
            if content.get("sources"):
                for source in content["sources"]:
                    title = source.get("title", "")
                    if title and title not in all_sources:
                        all_sources[title] = source
                        # Weise eine Zitationsnummer zu (sortiert alphabetisch später)
                        source_counter[title] = 0  # Wird später neu nummeriert
        else:
            print(f"DEBUG: Skipping content fetch (include_content={include_content}, neo={neo})")
        
        # Konzeptbeschreibung (falls vorhanden)
        if content.get("concept_description"):
            story.append(Paragraph("Überblick:", section_style))
            story.append(Paragraph(content["concept_description"], content_style))
            story.append(Spacer(1, 0.3*cm))
        
        # Lernziele
        if kapitel.get("Lernziele"):
            story.append(Paragraph("Lernziele:", section_style))
            for ziel in kapitel.get("Lernziele", []):
                if ziel.strip():
                    story.append(Paragraph(f"• {ziel}", objective_style))
            story.append(Spacer(1, 0.5*cm))
        
        # Abschnitte mit Inhalten
        answer_text = content.get("answer_text", "")
        abschnitte = kapitel.get("Abschnitte", [])
        
        # Erstelle Mapping von Quellentiteln zu Zitationsnummern
        chapter_sources = {}
        chapter_refs = []  # Liste der Referenznummern für dieses Kapitel
        if content.get("sources"):
            sorted_all_sources = sorted(all_sources.keys())
            for source in content["sources"]:
                title = source.get("title", "")
                if title and title in sorted_all_sources:
                    ref_num = sorted_all_sources.index(title) + 1
                    chapter_sources[title] = ref_num
                    if ref_num not in chapter_refs:
                        chapter_refs.append(ref_num)
        
        if abschnitte and answer_text:
            # Verteile den Inhalt auf die Abschnitte
            # Teile den generierten Text grob in Teile entsprechend der Anzahl der Abschnitte
            paragraphs_text = [p.strip() for p in answer_text.split('\n\n') if p.strip()]
            
            # Berechne, wie viele Absätze pro Abschnitt
            num_sections = len(abschnitte)
            paras_per_section = max(1, len(paragraphs_text) // num_sections)
            
            for aidx, abschnitt in enumerate(abschnitte, 1):
                if abschnitt.strip():
                    # Abschnittstitel
                    story.append(Paragraph(f"{idx}.{aidx} {abschnitt}", section_style))
                    
                    # Weise diesem Abschnitt entsprechende Absätze zu
                    start_idx = (aidx - 1) * paras_per_section
                    end_idx = start_idx + paras_per_section if aidx < num_sections else len(paragraphs_text)
                    
                    section_paras = paragraphs_text[start_idx:end_idx]
                    for para_text in section_paras:
                        # Füge Zitationen am Ende des Absatzes ein
                        if chapter_refs and aidx == num_sections:
                            # Nur beim letzten Abschnitt des Kapitels die Quellen anfügen
                            refs_str = ','.join(map(str, sorted(chapter_refs)))
                            para_with_citation = f"{para_text} [{refs_str}]"
                            story.append(Paragraph(para_with_citation, content_style))
                        else:
                            story.append(Paragraph(para_text, content_style))
                    
                    story.append(Spacer(1, 0.3*cm))
        
        elif abschnitte and not answer_text:
            # Nur Abschnitte ohne Inhalt
            story.append(Paragraph("Abschnitte:", section_style))
            for aidx, abschnitt in enumerate(abschnitte, 1):
                if abschnitt.strip():
                    story.append(Paragraph(f"{idx}.{aidx} {abschnitt}", objective_style))
            story.append(Spacer(1, 0.5*cm))
        
        elif answer_text and not abschnitte:
            # Inhalt ohne Abschnitte - als Ganzes einfügen
            story.append(Paragraph("Inhalt:", section_style))
            paragraphs_text = answer_text.split('\n\n')
            for para_idx, para_text in enumerate(paragraphs_text):
                if para_text.strip():
                    # Füge Zitationen am Ende des letzten Absatzes ein
                    if chapter_refs and para_idx == len(paragraphs_text) - 1:
                        refs_str = ','.join(map(str, sorted(chapter_refs)))
                        para_with_citation = f"{para_text.strip()} [{refs_str}]"
                        story.append(Paragraph(para_with_citation, content_style))
                    else:
                        story.append(Paragraph(para_text.strip(), content_style))
            story.append(Spacer(1, 0.5*cm))
        
        # Pagebreak nach jedem Kapitel (außer dem letzten)
        if idx < len(course.get("Kapitel", [])):
            story.append(PageBreak())
    
    # Quellenverzeichnis am Ende
    story.append(PageBreak())
    story.append(Paragraph("Quellenverzeichnis", chapter_style))
    story.append(Spacer(1, 0.5*cm))
    
    # Erstelle wissenschaftliches Literaturverzeichnis
    reference_style = ParagraphStyle(
        'Reference',
        parent=styles['BodyText'],
        fontSize=10,
        leftIndent=20,
        firstLineIndent=-20,  # Hängender Einzug
        spaceAfter=10,
        fontName='Helvetica'
    )
    
    if all_sources:
        # Sortiere alphabetisch nach Titel
        sorted_sources = sorted(all_sources.items(), key=lambda x: x[0])
        
        for idx, (title, source_info) in enumerate(sorted_sources, 1):
            # Erstelle bibliographische Angabe im wissenschaftlichen Format
            citation_parts = [f"[{idx}]"]
            
            # Autoren (falls vorhanden)
            authors = source_info.get("authors", "")
            if authors:
                citation_parts.append(authors)
            
            # Jahr (falls vorhanden)
            year = source_info.get("year", "")
            if year:
                citation_parts.append(f"({year})")
            
            # Titel (fett)
            citation_parts.append(f"<b>{title}</b>")
            
            # Quelle/Publikation (falls vorhanden)
            source_pub = source_info.get("source", "")
            if source_pub:
                citation_parts.append(f"<i>{source_pub}</i>")
            
            # DOI (falls vorhanden)
            doi = source_info.get("doi", "")
            if doi:
                citation_parts.append(f"DOI: {doi}")
            
            # Füge zusammen
            if len(citation_parts) > 1:
                citation_text = " ".join(citation_parts)
            else:
                citation_text = f"[{idx}] {title}"
            
            story.append(Paragraph(citation_text, reference_style))
    else:
        story.append(Paragraph("Keine Quellen aus dem Wissensgraph verwendet.", objective_style))
    
    # PDF erstellen
    doc.build(story)
    return output_path

def show_course_generator():
    # Neo4j Client initialisieren (wird für Graph-Zugriff und PDF-Generierung benötigt)
    neo = Neo4jClient()
    
    # Topic-Auswahl und Konzept-Vorschläge
    st.subheader("Kapitelvorschläge aus Wissensgraph")
    topics = [r["name"] for r in neo.run("MATCH (t:Topic) RETURN t.name AS name ORDER BY name")]
    selected_topic = st.selectbox("Topic aus Wissensgraph wählen", topics, key="coursegen_topic_select")
    concepts = []
    if selected_topic:
        concepts = neo.list_concepts_for_topic(selected_topic)
        st.markdown(f"**{len(concepts)} Konzepte für Topic '{selected_topic}'**")
        concept_names = [c["name"] for c in concepts]
        selected_concept = st.selectbox("Konzept auswählen", concept_names, key="coursegen_concept_select")
        st.caption("Ausgewähltes Konzept: " + selected_concept if selected_concept else "")
        if concepts and st.button("Alle Konzepte als Kapitel übernehmen"):
            # Nur übernehmen, wenn explizit geklickt
            st.session_state["course_struct"]["Kapitel"] = [
                {"Titel": c["name"], "Lernziele": [], "Abschnitte": []} for c in concepts
            ]
            st.rerun()
    
    # Initialisiere Session State
    if "course_struct" not in st.session_state:
        st.session_state["course_struct"] = {
            "Kursname": "Mein Kurs",
            "Kapitel": []
        }

    course = st.session_state["course_struct"]
    
    st.markdown("---")
    st.info("💡 **Tipp:** Um Inhalte aus dem Wissensgraphen zu nutzen, benenne deine Kapitel nach Konzeptnamen oder übernimm Konzepte direkt als Kapitel (Button oben).")

    # Kursname bearbeiten
    course["Kursname"] = st.text_input("Kursname", value=course["Kursname"])

    # Kapitel hinzufügen
    st.subheader("Kapitel")
    if st.button("Neues Kapitel hinzufügen"):
        course["Kapitel"].append({
            "Titel": f"Kapitel {len(course['Kapitel'])+1}",
            "Lernziele": [],
            "Abschnitte": []
        })

    # Kapitel bearbeiten

    for idx, kapitel in enumerate(course["Kapitel"]):
        with st.expander(f"Kapitel {idx+1}: {kapitel['Titel']}", expanded=True):
            kapitel["Titel"] = st.text_input(f"Kapitel-Titel {idx+1}", value=kapitel["Titel"], key=f"titel_{idx}")

            # Lernziele bearbeiten
            st.markdown("**Lernziele:**")
            for lidx, ziel in enumerate(kapitel["Lernziele"]):
                kapitel["Lernziele"][lidx] = st.text_input(f"Lernziel {lidx+1} (Kapitel {idx+1})", value=ziel, key=f"ziel_{idx}_{lidx}")

            col_z1, col_z2 = st.columns(2)
            with col_z1:
                if st.button(f"Lernziel hinzufügen (Kapitel {idx+1})", key=f"add_ziel_{idx}"):
                    kapitel["Lernziele"].append("")
                    st.rerun()
            with col_z2:
                if kapitel["Lernziele"]:
                    if st.button(f"Letztes Lernziel entfernen (Kapitel {idx+1})", key=f"del_ziel_{idx}"):
                        kapitel["Lernziele"].pop()
                        st.rerun()

            # Abschnitte bearbeiten
            st.markdown("**Abschnitte (Unterkapitel):**")
            for aidx, abschnitt in enumerate(kapitel["Abschnitte"]):
                kapitel["Abschnitte"][aidx] = st.text_input(f"Abschnitt {aidx+1} (Kapitel {idx+1})", value=abschnitt, key=f"abs_{idx}_{aidx}")

            col_a1, col_a2 = st.columns(2)
            with col_a1:
                if st.button(f"Abschnitt hinzufügen (Kapitel {idx+1})", key=f"add_abs_{idx}"):
                    kapitel["Abschnitte"].append("")
                    st.rerun()
            with col_a2:
                if kapitel["Abschnitte"]:
                    if st.button(f"Letzten Abschnitt entfernen (Kapitel {idx+1})", key=f"del_abs_{idx}"):
                        kapitel["Abschnitte"].pop()
                        st.rerun()

            # Kapitel entfernen
            if st.button(f"Kapitel entfernen (Kapitel {idx+1})", key=f"del_kap_{idx}"):
                course["Kapitel"].pop(idx)
                st.rerun()

    st.markdown("---")
    st.subheader("Vorschau Kursstruktur")
    st.write(course)
    
    st.markdown("---")
    st.subheader("📄 Kurs als PDF exportieren")
    
    col_pdf1, col_pdf2, col_pdf3 = st.columns([2, 1, 1])
    with col_pdf1:
        pdf_filename = st.text_input(
            "PDF-Dateiname", 
            value=f"{course['Kursname'].replace(' ', '_')}_Kurs.pdf",
            help="Name der zu erstellenden PDF-Datei"
        )
    with col_pdf2:
        include_content = st.checkbox(
            "Inhalte aus Wissensgraph",
            value=True,
            help="Automatisch passende Inhalte aus dem Wissensgraphen einfügen"
        )
    with col_pdf3:
        st.write("")  # Spacer
        if st.button("PDF generieren", type="primary"):
            if not course.get("Kapitel"):
                st.warning("Bitte füge mindestens ein Kapitel hinzu, bevor du die PDF generierst.")
            else:
                from pathlib import Path
                exports_dir = Path(__file__).parent.parent / "exports"
                exports_dir.mkdir(exist_ok=True)
                output_path = exports_dir / pdf_filename
                
                try:
                    with st.spinner("Generiere PDF..."):
                        result_path = generate_course_pdf(
                            course, 
                            str(output_path),
                            neo=neo if include_content else None,
                            include_content=include_content
                        )
                    st.success(f"✅ PDF erfolgreich erstellt: {output_path.name}")
                    
                    # Download-Button
                    with open(result_path, "rb") as f:
                        st.download_button(
                            label="📥 PDF herunterladen",
                            data=f.read(),
                            file_name=output_path.name,
                            mime="application/pdf"
                        )
                except Exception as e:
                    st.error(f"❌ Fehler beim Erstellen der PDF: {e}")
                    import traceback
                    st.code(traceback.format_exc())
