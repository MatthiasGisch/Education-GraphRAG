# GraphRAG Knowledge Management System

> **📖 Vollständige Dokumentation:** [DOCUMENTATION.md](DOCUMENTATION.md)

Ein intelligentes Wissensmanagement-System basierend auf GraphRAG mit Neo4j AuraDB.

## ⚡ Schnellstart

```powershell
# 1. Installation
pip install -r requirements.txt
python scripts/install_spacy_models.py

# 2. Konfiguration (.env erstellen)
cp .env.example .env
# NEO4J_URI, NEO4J_PASSWORD, OPENAI_API_KEY setzen

# 3. Datenbank initialisieren
python scripts/create_schema.py

# 4. PDFs ingestieren
python scripts/ingest.py your_file.pdf

# 5. GUI starten
streamlit run scripts/gui_app.py
```

## 🚀 Hauptfunktionen

- **📄 PDF-Ingestion** - Automatische Extraktion von Text + Bildern
- **🧠 Vector-Retrieval** - Semantische Suche (keine String-Matching!)
- **🎓 Kurs-Generator** - Automatische Lerneinheiten mit Bildern
- **🎬 Präsentationen/Videos** - Gamma.app & Synthesia Integration
- **📊 Graph-Visualisierung** - 3D-Interaktiv (Plotly)
- **💬 KI-Antworten** - GPT-4 mit Quellenbelegen

## 📚 Dokumentation

- **[DOCUMENTATION.md](DOCUMENTATION.md)** - Vollständige Dokumentation
- **[VECTOR_BASED_RETRIEVAL.md](VECTOR_BASED_RETRIEVAL.md)** - Retrieval-Architektur
- **[RETRIEVAL_FLOW.md](RETRIEVAL_FLOW.md)** - Retrieval-Ablauf
- **[NEO4J_VISUALIZATION_QUERIES.md](NEO4J_VISUALIZATION_QUERIES.md)** - Cypher-Queries
- **[docs/](docs/)** - Spezial-Themen

## 💻 CLI-Befehle

```powershell
# Fragen stellen
python scripts/ask.py "Was ist Deep Learning?"

# Kurs generieren
python scripts/course_generator.py "Kurstitel"

# Datenbank-Diagnose
python scripts/diagnose_db.py

# Re-Ingest aller PDFs
python scripts/reingest_all.py
```

## 🌐 Web-GUI

```powershell
streamlit run scripts/gui_app.py
```

**Features:**
- PDF-Upload mit Konzept-Extraktion
- Frage-Antwort mit Quellenbelegen
- 3D-Graph-Visualisierung
- Kurs-Generator
- Video/Präsentations-Erstellung
- DB-Wartung & Diagnose

## 🔧 Technologie-Stack

- **Datenbank:** Neo4j AuraDB (Cloud) + Vector-Indizes
- **LLM:** OpenAI GPT-4 / GPT-4o-mini
- **Embeddings:** text-embedding-3-small
- **NLP:** spaCy + SciSpacy
- **Frontend:** Streamlit
- **Präsentation:** Gamma.app, python-pptx
- **Video:** Synthesia.io
- **PDF:** PyMuPDF

## ⚙️ Konfiguration

**.env Datei:**
```env
NEO4J_URI=neo4j+s://xxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
OPENAI_API_KEY=sk-xxxxx
GAMMA_API_KEY=your_key  # optional
SYNTHESIA_API_KEY=your_key  # optional
```

---

**Version:** 2.0 (Vector-Based Architecture)  
**Letzte Aktualisierung:** Januar 2026
