
import streamlit as st
from src.neo import Neo4jClient

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
