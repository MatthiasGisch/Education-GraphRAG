# GraphRAG Knowledge Management System

Ein **intelligentes Wissensmanagement-System** basierend auf GraphRAG mit Neo4j AuraDB:
- 📄 **PDF-Ingestion** (Text + Bilder mit automatischer Strukturierung)
- 🧠 **Vector-basiertes Retrieval** (semantische Konzeptsuche)
- 🎓 **Automatische Kursgenerierung** (mit Bildern und Quellen)
- 🎬 **Präsentations- & Video-Erstellung** (Gamma.app, Synthesia.io)
- 📊 **Interaktive Graph-Visualisierung** (Plotly 3D)
- 💬 **KI-gestützte Frage-Antwort** (OpenAI GPT-4 mit Quellenbelegen)

> **📖 Vollständige Dokumentation:** [DOCUMENTATION.md](DOCUMENTATION.md)

## ⚡ Schnellstart

### 1. Installation
```powershell
# Repository klonen
git clone <repository-url>
cd masterthesis_neu

# Virtuelle Umgebung
python -m venv venv
.\venv\Scripts\Activate.ps1

# Abhängigkeiten
pip install -r requirements.txt

# spaCy-Modelle (optional, für NER)
python scripts/install_spacy_models.py
```

### 2. Konfiguration
```powershell
# .env erstellen (von .env.example)
cp .env.example .env
# Dann editieren: NEO4J_URI, NEO4J_PASSWORD, OPENAI_API_KEY
```

### 3. Datenbank initialisieren
```powershell
python scripts/create_schema.py
```

### 4. PDFs ingestieren
```powershell
python scripts/ingest.py path/to/your.pdf
```

### 5. GUI starten
```powershell
streamlit run scripts/gui_app.py
```
**→ Öffnet im Browser:** http://localhost:8501

### 6. CLI-Nutzung
```powershell
# Fragen stellen
python scripts/ask.py "Was ist Deep Learning?"

# Kurs generieren
python scripts/course_generator.py "Deep Learning Grundlagen"
```

## 🚀 Hauptfunktionen

### 1. Vector-basiertes Retrieval (2024)
**Problem gelöst:** Alte MENTIONS-Relations führten zu LLM-Timeouts und niedriger Coverage (82% Paragraphen ohne Konzept-Links).

**Neue Lösung:**
- ✅ Konzepte werden OHNE Paragraph-Links extrahiert
- ✅ Matching zur Abfragezeit via Vector-Similarity
- ✅ Semantische Suche findet verwandte Inhalte
- ✅ 1 LLM-Call pro Paper (statt hunderte)

**Mehr Details:** [VECTOR_BASED_RETRIEVAL.md](VECTOR_BASED_RETRIEVAL.md)

### 2. Hybrid Konzept-Extraktion
Kombiniert mehrere Ansätze für optimale Ergebnisse:
- **LLM-basiert:** Abstrakte Konzepte + Beschreibungen
- **NER (spaCy/SciSpacy):** Named Entities (PERSON, ORG, SCIENTIFIC_TERM)
- **Semantische Relationen:** IS_A, PART_OF, CAUSES, REQUIRES, etc.
- **Ko-Okkurrenz:** Statistische Verbindungen

### 3. Automatische Kursgenerierung
- 🎯 KI generiert Kursstruktur aus Themenbeschreibung
- 📚 Retrieval-basierte Inhalte mit Quellenbelegen
- 🖼️ Automatische Bildauswahl
- ✅ Optionale Lernziele + Quiz
- 📤 Export: JSON, PDF, Gamma-Präsentation

### 4. Interaktive Visualisierung
- 📊 3D-Graph-Visualisierung (Plotly)
- 🔍 Filter nach Node-/Relationstypen
- 💾 HTML-Export (vollständig interaktiv)

## 📁 Projektstruktur

```
masterthesis_neu/
├─ scripts/               # Ausführbare Scripts
│  ├─ gui_app.py          # Streamlit Web-GUI (Hauptanwendung)
│  ├─ ingest.py           # PDF-Ingestion
│  ├─ ask.py              # Frage-Antwort CLI
│  ├─ course_generator.py # Kurs-Generator
│  ├─ create_schema.py    # DB-Schema anlegen
│  ├─ diagnose_db.py      # Datenbank-Diagnose
│  ├─ reingest_all.py     # Komplette Re-Ingestion
│  └─ ...
├─ src/                   # Core-Bibliothek
│  ├─ config.py           # Konfiguration (.env)
│  ├─ neo.py              # Neo4j Client
│  ├─ openai_client.py    # OpenAI API (LLM, Embeddings, Vision)
│  ├─ pdf_ingest.py       # PDF-Verarbeitung
│  ├─ concept_extract.py  # Konzept-Extraktion (LLM + NER)
│  ├─ retriever.py        # Vector-basiertes Retrieval
│  ├─ agent.py            # Answer Orchestration
│  ├─ gamma_client.py     # Gamma.app Integration
│  ├─ synthesia_client.py # Synthesia Video Generation
│  └─ ...
├─ data/
│  ├─ images/             # Extrahierte Bilder aus PDFs
│  └─ uploads/            # Hochgeladene PDFs (für Re-Ingest)
├─ exports/               # Generierte Outputs (PDFs, Kurse, Videos)
├─ docs/                  # Detaillierte Dokumentation
├─ .env.example           # Konfigurationsvorlage
├─ requirements.txt       # Python-Abhängigkeiten
├─ README.md              # Diese Datei
└─ DOCUMENTATION.md       # Vollständige Dokumentation
```

## 📖 Dokumentation

- **[DOCUMENTATION.md](DOCUMENTATION.md)** - Vollständige System-Dokumentation
- **[VECTOR_BASED_RETRIEVAL.md](VECTOR_BASED_RETRIEVAL.md)** - Vector-Retrieval-Architektur
- **[RETRIEVAL_FLOW.md](RETRIEVAL_FLOW.md)** - Detaillierter Retrieval-Ablauf
- **[REINGEST_GUIDE.md](REINGEST_GUIDE.md)** - Re-Ingestion-Prozess
- **[NEO4J_VISUALIZATION_QUERIES.md](NEO4J_VISUALIZATION_QUERIES.md)** - Nützliche Cypher-Queries
- **[docs/](docs/)** - Spezial-Themen (Gamma API, Plotly, Semantic Relations, etc.)

## 🛠️ Wichtige CLI-Befehle

```powershell
# PDF ingestieren
python scripts/ingest.py papers/*.pdf

# Fragen stellen
python scripts/ask.py "Was ist künstliche Intelligenz?"

# Frage → PDF exportieren
python scripts/ask_to_pdf.py "Erkläre Deep Learning"

# Kurs generieren
python scripts/course_generator.py "Mein Kurstitel"

# Datenbank-Diagnose
python scripts/diagnose_db.py

# Komplette Re-Ingestion
python scripts/reingest_all.py

# Datenbank leeren (VORSICHT!)
python scripts/clear_all.py
```

## 🌐 Web-GUI Features

**Start:** `streamlit run scripts/gui_app.py`

- 📂 **PDF-Upload:** Drag & Drop, Konzept-Extraktion konfigurierbar
- 💬 **Frage-Antwort:** Intelligente Suche mit Quellenbelegen
- 📊 **Graph-Visualisierung:** 3D-Interaktiv (Plotly), HTML-Export
- 🎓 **Kurs-Generator:** Automatische Lerneinheiten mit Bildern
- 🎬 **Video/Präsentation:** Gamma.app & Synthesia Integration
- 🔧 **Wartung:** DB-Statistiken, Diagnose, Re-Ingest

## 🧪 Technologie-Details

### Neo4j Graph-Schema

**Node-Typen:**
- `Paper` - Wissenschaftliche Dokumente
- `Topic` - Themengebiete
- `Concept` - Extrahierte Konzepte (mit Embeddings)
- `Section` - Dokumentabschnitte
- `Paragraph` - Textabsätze (mit Embeddings)
- `Figure` - Bilder/Abbildungen

**Relationen:**
- `HAS_SECTION`, `HAS_PARAGRAPH`, `HAS_FIGURE` - Dokumentstruktur
- `HAS_CONCEPT` - Topic → Concepts
- `SEMANTIC_RELATION` - Konzept-Verbindungen (IS_A, PART_OF, etc.)
- `CO_OCCURS_WITH` - Ko-Okkurrenz-Beziehungen
- `NEAR_FIGURE` - Paragraph ↔ Figure Proximity

**Vector-Indizes:**
- `concept_embedding_index` - Für semantische Konzeptsuche
- `paragraph_embedding_index` - Für Textsuche

### Retrieval-Strategie

```python
# 1) Query → Embedding
query_emb = openai.embed("Was ist Deep Learning?")

# 2) Finde relevante Konzepte (Vector-Search)
concepts = neo.vector_search_concepts(query_emb, k=10, min_score=0.6)

# 3) Durchschnitts-Embedding der Konzepte
avg_concept_emb = np.mean([c.embedding for c in concepts], axis=0)

# 4) Finde Paragraphen ähnlich zum Konzept-Cluster
paragraphs_via_concepts = neo.vector_search_paragraphs(avg_concept_emb, limit=20)

# 5) Direkte Paragraph-Suche
paragraphs_direct = neo.vector_search_paragraphs(query_emb, limit=48)

# 6) Merge + Deduplizierung
supports = merge_unique(paragraphs_via_concepts, paragraphs_direct)
```

**Vorteile:**
- ✅ Semantische Ähnlichkeit > String-Matching
- ✅ Findet verwandte Konzepte automatisch
- ✅ Robust bei Variationen (Plural, Synonyme)
- ✅ Keine LLM-Timeouts

## ⚙️ Konfiguration (.env)

```env
# Neo4j AuraDB (erforderlich)
NEO4J_URI=neo4j+s://xxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# OpenAI (erforderlich)
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4o-mini              # oder gpt-4
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Gamma.app (optional - für Präsentationen)
GAMMA_API_KEY=your_gamma_key

# Synthesia.io (optional - für Videos)
SYNTHESIA_API_KEY=your_synthesia_key

# Optionen
WEB_SEARCH_ENABLED=false               # Web-Fallback aktivieren
```