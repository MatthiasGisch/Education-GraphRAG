
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

def generate_course_pdf(course: dict, output_path: str) -> str:
    """
    Generiert eine strukturierte PDF aus der Kursstruktur.
    
    Args:
        course: Dict mit Kursname und Kapiteln
        output_path: Pfad für die Ausgabedatei
        
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
    
    # Kapitel
    for idx, kapitel in enumerate(course.get("Kapitel", []), 1):
        story.append(Paragraph(f"Kapitel {idx}: {kapitel.get('Titel', '')}", chapter_style))
        story.append(Spacer(1, 0.3*cm))
        
        # Lernziele
        if kapitel.get("Lernziele"):
            story.append(Paragraph("Lernziele:", section_style))
            for ziel in kapitel.get("Lernziele", []):
                if ziel.strip():
                    story.append(Paragraph(f"• {ziel}", objective_style))
            story.append(Spacer(1, 0.5*cm))
        
        # Abschnitte
        if kapitel.get("Abschnitte"):
            story.append(Paragraph("Abschnitte:", section_style))
            for aidx, abschnitt in enumerate(kapitel.get("Abschnitte", []), 1):
                if abschnitt.strip():
                    story.append(Paragraph(f"{idx}.{aidx} {abschnitt}", objective_style))
            story.append(Spacer(1, 0.5*cm))
        
        # Pagebreak nach jedem Kapitel (außer dem letzten)
        if idx < len(course.get("Kapitel", [])):
            story.append(PageBreak())
    
    # PDF erstellen
    doc.build(story)
    return output_path

def show_course_generator():
    # Topic-Auswahl und Konzept-Vorschläge
    st.subheader("Kapitelvorschläge aus Wissensgraph")
    neo = Neo4jClient()
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
    
    col_pdf1, col_pdf2 = st.columns([2, 1])
    with col_pdf1:
        pdf_filename = st.text_input(
            "PDF-Dateiname", 
            value=f"{course['Kursname'].replace(' ', '_')}_Kurs.pdf",
            help="Name der zu erstellenden PDF-Datei"
        )
    with col_pdf2:
        st.write("")  # Spacer
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
                        result_path = generate_course_pdf(course, str(output_path))
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
