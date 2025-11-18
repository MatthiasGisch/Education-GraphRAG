
import streamlit as st
from src.neo import Neo4jClient
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
    
    Args:
        neo: Neo4j Client
        chapter_title: Titel des Kapitels (meist ein Konzeptname)
        topic: Optional das Topic für bessere Zuordnung
        
    Returns:
        Dict mit paragraphs, figures, related_concepts
    """
    result = {"paragraphs": [], "figures": [], "related_concepts": [], "concept_description": ""}
    
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
            # Strategie 2: Volltext-Suche in Paragraphen mit dem Kapiteltitel
            fallback_query = """
            MATCH (p:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
            WHERE toLower(p.text) CONTAINS toLower($keyword)
            OPTIONAL MATCH (paper)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(p)
            WITH p, paper, sec
            ORDER BY p.order_in_page
            RETURN collect(DISTINCT {text: p.text, paper_title: paper.title, section: sec.title})[..3] AS paragraphs
            LIMIT 1
            """
            fallback_result = neo.run(fallback_query, {"keyword": chapter_title})
            if fallback_result and len(fallback_result) > 0:
                result["paragraphs"] = fallback_result[0].get("paragraphs", [])
                print(f"DEBUG: Fallback search found {len(result['paragraphs'])} paragraphs with keyword '{chapter_title}'")
            else:
                print(f"DEBUG: No content found for '{chapter_title}' even with fallback search")
    
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
            print(f"DEBUG: Content retrieved: {len(content.get('paragraphs', []))} paragraphs, description exists: {bool(content.get('concept_description'))}")
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
        if kapitel.get("Abschnitte"):
            story.append(Paragraph("Abschnitte:", section_style))
            for aidx, abschnitt in enumerate(kapitel.get("Abschnitte", []), 1):
                if abschnitt.strip():
                    story.append(Paragraph(f"{idx}.{aidx} {abschnitt}", objective_style))
            story.append(Spacer(1, 0.5*cm))
        
        # Inhalte aus Wissensgraph
        if content.get("paragraphs"):
            story.append(Paragraph("Inhalte:", section_style))
            for para in content["paragraphs"][:3]:  # Max 3 Paragraphen pro Kapitel
                text = para.get("text", "")
                if text:
                    # Kürze zu lange Texte
                    if len(text) > 800:
                        text = text[:800] + "..."
                    story.append(Paragraph(text, content_style))
                    
                    # Quellenangabe
                    paper_title = para.get("paper_title", "")
                    section = para.get("section", "")
                    if paper_title:
                        citation = f"Quelle: {paper_title}"
                        if section:
                            citation += f" ({section})"
                        story.append(Paragraph(citation, citation_style))
            story.append(Spacer(1, 0.3*cm))
        
        # Verwandte Konzepte
        if content.get("related_concepts"):
            story.append(Paragraph("Verwandte Konzepte:", section_style))
            for rel in content["related_concepts"]:
                rel_name = rel.get("name", "")
                rel_type = rel.get("relation_type", "verwandt mit")
                if rel_name:
                    story.append(Paragraph(f"• {rel_name} ({rel_type})", objective_style))
            story.append(Spacer(1, 0.3*cm))
        
        # Abbildungen
        if content.get("figures"):
            story.append(Paragraph("Abbildungen:", section_style))
            for fig in content["figures"]:
                caption = fig.get("caption", "")
                label = fig.get("label", "")
                if caption or label:
                    fig_text = f"• {label}: {caption}" if label else f"• {caption}"
                    story.append(Paragraph(fig_text, objective_style))
            story.append(Spacer(1, 0.3*cm))
        
        # Pagebreak nach jedem Kapitel (außer dem letzten)
        if idx < len(course.get("Kapitel", [])):
            story.append(PageBreak())
    
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
