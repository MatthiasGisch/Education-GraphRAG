# GraphRAG Knowledge Management System - Vollständige Dokumentation

## 📋 Inhaltsverzeichnis
1. [Überblick](#überblick)
2. [Systemarchitektur](#systemarchitektur)
3. [Installation & Setup](#installation--setup)
4. [Hauptfunktionen](#hauptfunktionen)
5. [GUI-Bedienung](#gui-bedienung)
6. [CLI-Scripts](#cli-scripts)
7. [Datenbank-Schema](#datenbank-schema)
8. [Retrieval-System](#retrieval-system)
9. [Kurs- & Präsentationsgenerierung](#kurs--präsentationsgenerierung)
10. [Wartung & Diagnose](#wartung--diagnose)

---

## Überblick

Ein **GraphRAG-Hybridsystem** zur intelligenten Wissensverarbeitung wissenschaftlicher Dokumente mit:
- **PDF-Ingestion** (Text + Bilder)
- **Neo4j Knowledge Graph** (Konzepte, Relationen, Dokumente)
- **Vector-basiertes Retrieval** (semantische Suche)
- **LLM-gestützte Antworten** (OpenAI GPT-4)
- **Automatische Kursgenerierung** (mit Bildern und Quellen)
- **Video- & Präsentationserzeugung** (Gamma.app, Synthesia.io)
- **Interaktive Graph-Visualisierung** (Plotly)

### Technologie-Stack
- **Datenbank:** Neo4j AuraDB (Cloud) mit Vector-Indizes
- **LLM:** OpenAI GPT-4 / GPT-4o-mini
- **Embeddings:** text-embedding-3-small
- **NLP:** spaCy + SciSpacy (optional)
- **Frontend:** Streamlit
- **Präsentation:** Gamma.app API, python-pptx
- **Video:** Synthesia.io API (optional)
- **PDF:** PyMuPDF (fitz)

---

## Systemarchitektur

### Datenfluss

```
PDF-Upload
    ↓
PDF-Ingestion (src/pdf_ingest.py)
    ├─ Text-Extraktion (PyMuPDF)
    ├─ Bild-Extraktion → data/images/
    ├─ Struktur-Analyse (Sections, Paragraphs)
    └─ Embedding-Generierung (OpenAI)
    ↓
Konzept-Extraktion (src/concept_extract.py)
    ├─ LLM-basiert: Konzepte + Beschreibungen
    ├─ NER (optional): Named Entities (spaCy)
    ├─ Relationen: SEMANTIC_RELATION
    └─ Ko-Okkurrenz-Analyse
    ↓
Neo4j Knowledge Graph
    ├─ Nodes: Paper, Section, Paragraph, Concept, Figure, Topic
    ├─ Relations: HAS_SECTION, HAS_PARAGRAPH, HAS_FIGURE, HAS_CONCEPT, SEMANTIC_RELATION
    └─ Vector Indices: concept_embedding_index, paragraph_embedding_index
    ↓
Retrieval (src/retriever.py) ← VECTOR-BASED!
    ├─ Concept-basiert: Vector-Similarity auf Konzepten
    ├─ Paragraph-basiert: Direct Vector-Search
    ├─ Hybrid: Kombination + Deduplizierung
    └─ Image-Retrieval: Vision-LLM Analyse
    ↓
Answer Generation (src/agent.py)
    ├─ LLM-Antwort mit Quellennachweisen
    ├─ Citation-Validierung
    └─ Formatierung (Markdown)
    ↓
Output
    ├─ GUI: Interaktive Antworten + Graph-Viz
    ├─ PDF: Exportierte Antworten mit Quellen
    ├─ Kurs: Strukturierte Lerneinheiten
    ├─ Präsentation: Gamma/PPTX
    └─ Video: Synthesia (optional)
```

### Kernkomponenten

```
src/
├─ config.py              # Umgebungsvariablen (.env)
├─ neo.py                 # Neo4j Client (Datenbankoperationen)
├─ openai_client.py       # OpenAI API (LLM, Embeddings, Vision)
├─ pdf_ingest.py          # PDF-Verarbeitung (Text + Bilder)
├─ concept_extract.py     # Konzeptextraktion (LLM + NER)
├─ entity_relation_extract.py  # Semantische Relationen
├─ retriever.py           # VECTOR-BASED Retrieval
├─ agent.py               # Answer Orchestration
├─ citation_validator.py  # Quellenvalidierung
├─ gamma_client.py        # Gamma.app API Integration
├─ synthesia_client.py    # Synthesia Video Generation
├─ speaker_script.py      # Video-Skript-Generierung
├─ pdf_export.py          # PDF-Export
└─ graph_schema.cypher    # Neo4j Constraints & Indices
```

---

## Installation & Setup

### 1. Voraussetzungen
- Python 3.10+
- Neo4j AuraDB Account (kostenlos)
- OpenAI API Key
- (Optional) Gamma.app Account
- (Optional) Synthesia.io Account

### 2. Installation

```powershell
# Repository klonen
git clone <repository-url>
cd masterthesis_neu

# Virtuelle Umgebung erstellen
python -m venv venv
.\venv\Scripts\Activate.ps1

# Abhängigkeiten installieren
pip install -r requirements.txt

# spaCy-Modelle installieren (für NER)
python scripts/install_spacy_models.py
```

### 3. Konfiguration

**`.env` Datei erstellen** (Vorlage: `.env.example`):

```env
# Neo4j AuraDB
NEO4J_URI=neo4j+s://xxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# OpenAI
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Gamma.app (optional)
GAMMA_API_KEY=your_gamma_key

# Synthesia.io (optional)
SYNTHESIA_API_KEY=your_synthesia_key

# Optionen
WEB_SEARCH_ENABLED=false
```

### 4. Datenbank initialisieren

```powershell
# Schema & Indizes anlegen
python scripts/create_schema.py
```

Dies erstellt:
- Constraints (Unique IDs für Paper, Paragraph, Concept, etc.)
- Vector-Indizes (concept_embedding_index, paragraph_embedding_index)
- Relationshipindizes für Performance

---

## Hauptfunktionen

### 1. PDF-Ingestion

**Funktion:** PDFs werden analysiert, strukturiert und in den Knowledge Graph eingefügt.

**Prozess:**
1. **Textextraktion:** PyMuPDF extrahiert Fließtext pro Seite
2. **Bildextraktion:** Bilder werden nach `data/images/` exportiert
3. **Strukturierung:** KI erkennt Sections + Paragraphs
4. **Embedding-Generierung:** Jeder Paragraph → Vector (1536 Dimensionen)
5. **Konzeptextraktion:** LLM identifiziert Hauptkonzepte
6. **Graph-Aufbau:** Nodes + Relations werden in Neo4j gespeichert
7. **Auto-Stitching:** Verknüpfung Section↔Paragraph, Figure↔Paragraph

**CLI:**
```powershell
python scripts/ingest.py path/to/file.pdf [more.pdf ...]
```

**GUI:** Upload-Bereich → "PDFs hochladen und verarbeiten"

---

### 2. Vector-Based Retrieval

**⚡ Wichtigste Änderung 2024:** Keine MENTIONS-Relations mehr!

**Alt (problematisch):**
- Konzepte wurden beim Ingest mit Paragraphen verlinkt (→ LLM-Timeouts)
- String-Matching für Verknüpfungen (→ 82% fehlende Links)

**Neu (VECTOR-BASED):**
- Konzepte werden OHNE Paragraph-Links extrahiert
- Matching erfolgt zur **Abfragezeit** via Vector-Similarity
- Semantische Ähnlichkeit statt exakte Strings

**Retrieval-Strategie:**
```python
# 1) Finde relevante Konzepte (Vector-Search)
concepts = vector_search(concept_index, query_embedding, k=10, min_score=0.6)

# 2) Durchschnitts-Embedding der Konzepte bilden
avg_embedding = np.mean([c.embedding for c in concepts], axis=0)

# 3) Suche Paragraphen ähnlich zum Konzept-Cluster
paragraphs = vector_search(paragraph_index, avg_embedding, limit=20)

# 4) Kombiniere mit direkter Suche
direct_paragraphs = vector_search(paragraph_index, query_embedding, k=48)

# 5) Deduplizierung + Ranking
return merge_and_rank(paragraphs, direct_paragraphs)
```

**Vorteile:**
- ✅ Schnelle Ingestion (1 LLM-Call pro Paper)
- ✅ Semantische Suche (findet verwandte Inhalte)
- ✅ Robust bei Variationen (Plural, Umlaute, Synonyme)
- ✅ Keine Timeouts mehr

**Siehe:** [VECTOR_BASED_RETRIEVAL.md](VECTOR_BASED_RETRIEVAL.md)

---

### 3. Konzeptextraktion

**Zwei Modi:**

#### A) LLM-basiert (Standard)
```python
from src.concept_extract import extract_and_embed_concepts_llm

result = extract_and_embed_concepts_llm(
    paper_title="Titel",
    paper_text=full_text,
    paragraphs=paragraphs,
    neo_client=neo,
    persist_to_topic=True
)
# → Extrahiert ~30 Konzepte mit Beschreibungen
```

#### B) Hybrid (LLM + NER + Relationen)
```python
from src.concept_extract import extract_and_embed_concepts_hybrid

result = extract_and_embed_concepts_hybrid(
    paper_title="Titel",
    paper_text=full_text,
    paragraphs=paragraphs,
    max_entities=30,
    max_relations=20,
    use_scispacy=True,  # Wissenschaftliche Entitäten
    neo_client=neo,
    persist_to_topic=True
)
# → Extrahiert: Konzepte + Named Entities + Semantische Relationen
```

**Extrahierte Daten:**
- **Konzepte:** Name + Beschreibung + Embedding
- **Named Entities:** PERSON, ORG, SCIENTIFIC_TERM, CHEMICAL, etc.
- **Relationen:** IS_A, PART_OF, CAUSES, REQUIRES, USES, etc.
- **Ko-Okkurrenz:** Statistische Verbindungen zwischen Konzepten

---

### 4. Semantische Relationen

**Relationstypen im Graph:**

```cypher
// Beispiel: Konzept-Relationen
(c1:Concept)-[:SEMANTIC_RELATION {
    relation_type: "IS_A",
    confidence: 0.85,
    context: "Neural networks are a type of machine learning model",
    source: "llm"
}]->(c2:Concept)

// Ko-Okkurrenz
(c1:Concept)-[:CO_OCCURS_WITH {
    count: 15,
    strength: 0.72
}]->(c2:Concept)
```

**Verfügbare Relationstypen:**
- `IS_A` - Taxonomie (z.B. "CNN ist ein neuronales Netz")
- `PART_OF` - Komposition
- `CAUSES` - Kausalität
- `REQUIRES` - Abhängigkeit
- `USES` - Anwendung
- `ENABLES` - Ermöglicht
- `IMPROVES` - Verbesserung
- `COMPARES_TO` - Vergleich

**Siehe:** [docs/SEMANTIC_RELATIONS.md](docs/SEMANTIC_RELATIONS.md)

---

## GUI-Bedienung

**Start:**
```powershell
streamlit run scripts/gui_app.py
```

**URL:** http://localhost:8501

### Hauptbereiche

#### 1. 📂 PDF-Upload & Ingestion
- PDFs hochladen (Drag & Drop oder Browse)
- Einstellungen:
  - Konzept-Extraktionsmodus (LLM / Hybrid)
  - Max. Entities (bei Hybrid)
  - Max. Relations (bei Hybrid)
  - SciSpacy aktivieren (wissenschaftliche Texte)
- Button: "PDFs hochladen und verarbeiten"

#### 2. 💬 Frage-Antwort-System
- **Eingabe:** Frage in natürlicher Sprache
- **Einstellungen:**
  - Web-Suche: Aus / Auto / Erzwingen
  - Concept-Retrieval: An/Aus (empfohlen: An)
  - Max. Paragraphen: 30-60
  - Max. Bilder: 5-10
- **Ausgabe:**
  - Antwort mit Quellenverweisen
  - Gefundene Konzepte
  - Relevante Paragraphen
  - Bilder mit Beschreibungen
- **Export:** Als PDF mit vollständigen Quellen

#### 3. 📊 Graph-Visualisierung
- **3D-Interaktiv (Plotly):**
  - Nodes: Paper, Concept, Paragraph, Section, Figure
  - Relations: farbkodiert
  - Interaktiv: Zoom, Rotate, Hover-Info
- **Filter:**
  - Node-Typen ein-/ausblenden
  - Relationstypen filtern
  - Max. Nodes begrenzen
- **Export:** HTML (vollständig interaktiv)

#### 4. 🎓 Kursgenerierung
- **Eingabe:**
  - Kurstitel
  - Zielgruppe (optional)
  - Themenbeschreibung
- **Einstellungen:**
  - Anzahl Hauptthemen: 3-6
  - Absätze pro Thema: 40-60
  - Bilder pro Thema: 6-10
  - Mit Lernzielen: Ja/Nein
  - Mit Quiz: Ja/Nein
- **Ausgabe:**
  - Strukturierter Kurs (JSON)
  - PDF-Export
  - Gamma-Präsentation (optional)

#### 5. 🎬 Video-Generierung
- **PPTX hochladen** (von Gamma exportiert)
- **Synthesia-Einstellungen:**
  - Avatar-ID
  - Sprache (de-DE, en-US, etc.)
  - Sekunden pro Folie
- **Oder:** Lokale Video-Generierung (PNG-Slides + FFmpeg)
- **Ausgabe:** Video-Download-Link oder lokale MP4

#### 6. 🔧 Wartung & Diagnose
- **Datenbank-Statistiken:** Anzahl Nodes/Relations
- **Paper-Übersicht:** Liste aller ingestierten Dokumente
- **Diagnose-Scripts ausführen:**
  - `check_paragraphs.py` - Paragraph-Verknüpfungen prüfen
  - `diagnose_db.py` - Vollständige DB-Analyse
  - `fix_paragraphs.py` - Orphan-Paragraphs reparieren
- **Reingest:** Komplette Datenbank neu aufbauen

---

## CLI-Scripts

### Kernfunktionen

#### `scripts/ingest.py` - PDF-Ingestion
```powershell
python scripts/ingest.py path/to/file.pdf [more.pdf ...]

# Mit Optionen
python scripts/ingest.py papers/*.pdf --hybrid --scispacy
```

#### `scripts/ask.py` - Fragen stellen
```powershell
python scripts/ask.py "Was ist Deep Learning?"

# Mit Optionen
python scripts/ask.py "Erkläre CNN" --max-paragraphs 50 --max-figures 10
```

#### `scripts/ask_to_pdf.py` - Frage → PDF
```powershell
python scripts/ask_to_pdf.py "Erkläre Transformer-Modelle"
# Erstellt: exports/answer_<id>.pdf
```

#### `scripts/course_generator.py` - Kurs erstellen
```powershell
python scripts/course_generator.py "Deep Learning Grundkurs" --topics 5 --with-quiz

# Interaktiv
python scripts/course_generator.py
```

### Wartung

#### `scripts/create_schema.py` - Schema anlegen
```powershell
python scripts/create_schema.py
```

#### `scripts/reingest_all.py` - Alle PDFs neu ingestieren
```powershell
python scripts/reingest_all.py
# Löscht DB, re-ingestiert alle PDFs aus data/uploads/
```

#### `scripts/diagnose_db.py` - Datenbank-Diagnose
```powershell
python scripts/diagnose_db.py
```
**Zeigt:**
- Alle Node-Typen + Anzahl
- Topics + Konzepte
- Papers + Komponenten
- Orphan Nodes
- Relation-Statistiken

#### `scripts/check_paragraphs.py` - Paragraph-Links prüfen
```powershell
python scripts/check_paragraphs.py
```

#### `scripts/fix_paragraphs.py` - Orphan-Paragraphs reparieren
```powershell
python scripts/fix_paragraphs.py
```

#### `scripts/clear_all.py` - Datenbank leeren
```powershell
python scripts/clear_all.py
# WARNUNG: Löscht ALLE Daten!
```

#### `scripts/clear_concepts.py` - Nur Konzepte löschen
```powershell
python scripts/clear_concepts.py
```

#### `scripts/update_paper_metadata.py` - Metadaten aktualisieren
```powershell
python scripts/update_paper_metadata.py papers_metadata.csv
```

---

## Datenbank-Schema

### Node-Typen

#### Paper
```cypher
(:Paper {
    paper_id: STRING (UNIQUE),
    title: STRING,
    author: STRING (optional),
    year: INTEGER (optional),
    doi: STRING (optional),
    source_path: STRING,
    total_pages: INTEGER,
    created_at: DATETIME
})
```

#### Topic
```cypher
(:Topic {
    topic_id: STRING (UNIQUE),
    name: STRING,
    description: STRING (optional)
})
```

#### Concept
```cypher
(:Concept {
    concept_id: STRING (UNIQUE),
    name: STRING,
    description: STRING,
    entity_type: STRING (optional, z.B. "PERSON", "ORG"),
    embedding: LIST<FLOAT> (1536 dimensions)
})
```

#### Section
```cypher
(:Section {
    section_id: STRING (UNIQUE),
    title: STRING,
    level: INTEGER,
    page_start: INTEGER,
    page_end: INTEGER,
    section_type: STRING
})
```

#### Paragraph
```cypher
(:Paragraph {
    paragraph_id: STRING (UNIQUE),
    text: STRING,
    page_number: INTEGER,
    position: INTEGER,
    embedding: LIST<FLOAT> (1536 dimensions)
})
```

#### Figure
```cypher
(:Figure {
    figure_id: STRING (UNIQUE),
    caption: STRING (optional),
    image_path: STRING,
    page_number: INTEGER,
    position: FLOAT,
    analysis: STRING (optional, Vision-LLM Beschreibung)
})
```

### Relations

```cypher
// Dokumentstruktur
(Paper)-[:HAS_SECTION]->(Section)
(Paper)-[:HAS_PARAGRAPH]->(Paragraph)
(Paper)-[:HAS_FIGURE]->(Figure)
(Section)-[:HAS_PARAGRAPH]->(Paragraph)
(Section)-[:HAS_FIGURE]->(Figure)

// Konzepte
(Topic)-[:HAS_CONCEPT]->(Concept)
(Paragraph)-[:NEAR_FIGURE]->(Figure)

// Semantische Relationen
(Concept)-[:SEMANTIC_RELATION {
    relation_type: STRING,
    confidence: FLOAT,
    context: STRING,
    source: STRING
}]->(Concept)

// Ko-Okkurrenz
(Concept)-[:CO_OCCURS_WITH {
    count: INTEGER,
    strength: FLOAT
}]->(Concept)
```

### Vector-Indizes

```cypher
// Concept-Embedding-Index
CREATE VECTOR INDEX concept_embedding_index IF NOT EXISTS
FOR (c:Concept) ON (c.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}};

// Paragraph-Embedding-Index
CREATE VECTOR INDEX paragraph_embedding_index IF NOT EXISTS
FOR (p:Paragraph) ON (p.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 1536,
  `vector.similarity_function`: 'cosine'
}};
```

**Siehe auch:** [src/graph_schema.cypher](src/graph_schema.cypher)

---

## Retrieval-System

### Konzept-basiertes Retrieval (empfohlen)

**Funktion:** `concept_based_retrieve()` in [src/retriever.py](src/retriever.py)

**Ablauf:**
```python
def concept_based_retrieve(neo, query, k_concepts=10, k_paragraphs_via_concepts=20, 
                          k_paragraphs_direct=48, k_figures=8, min_concept_score=0.6):
    # 1) Query-Embedding
    query_emb = embed_text(query)
    
    # 2) Finde relevante Konzepte (Vector-Similarity)
    concepts = neo.vector_search_concepts(query_emb, k=k_concepts, min_score=min_concept_score)
    
    # 3) Durchschnitts-Embedding der Konzepte
    concept_embeddings = [c['embedding'] for c in concepts]
    avg_concept_emb = np.mean(concept_embeddings, axis=0)
    
    # 4) Suche Paragraphen ähnlich zum Konzept-Cluster
    paragraphs_via_concepts = neo.vector_search_paragraphs(avg_concept_emb, limit=k_paragraphs_via_concepts)
    
    # 5) Direkte Paragraph-Suche
    paragraphs_direct = neo.vector_search_paragraphs(query_emb, limit=k_paragraphs_direct)
    
    # 6) Bild-Suche
    figures = neo.vector_search_figures(query, k=k_figures)
    
    # 7) Kombiniere + Dedupliziere
    all_paragraphs = merge_unique(paragraphs_via_concepts, paragraphs_direct)
    
    return {
        'supports': all_paragraphs + figures,
        'concepts': concepts,
        'debug': {...}
    }
```

**Parameter-Tuning:**
- `k_concepts=10`: Mehr → breitere Suche, aber potenziell off-topic
- `min_concept_score=0.6`: Höher → präziser, niedriger → mehr Recall
- `k_paragraphs_via_concepts=20`: Anzahl Paragraphen via Konzepte
- `k_paragraphs_direct=48`: Anzahl Paragraphen direkt

**Performance:**
- ✅ 5-10 relevante Konzepte
- ✅ 10-20 Paragraphen via Konzepte
- ✅ 15-30 Paragraphen direkt
- ✅ 29+ deduplizierte Supports
- ✅ Keine LLM-Timeouts

### Hybrid-Retrieval (Fallback)

**Funktion:** `hybrid_retrieve()` - Kombiniert Keyword + Vector-Search

```python
def hybrid_retrieve(neo, query, k_paragraphs=48, k_figures=8):
    # Keyword-Matching (Fulltext)
    keyword_results = neo.fulltext_search(query)
    
    # Vector-Search
    vector_results = neo.vector_search_paragraphs(embed_text(query), limit=k_paragraphs)
    
    # Merge mit Scoring
    return merge_with_scores(keyword_results, vector_results)
```

**Siehe detailliert:** [RETRIEVAL_FLOW.md](RETRIEVAL_FLOW.md)

---

## Kurs- & Präsentationsgenerierung

### 1. Kursstruktur

**Generierung:** [scripts/course_generator.py](scripts/course_generator.py)

**Workflow:**
1. **LLM generiert Outline:** Titel → Hauptthemen → Unterthemen
2. **Für jedes Thema:**
   - Retrieval (Konzept-basiert, 40-60 Paragraphen)
   - LLM schreibt Erklärungstext
   - Bildauswahl (relevanteste Figures)
   - Lernziele generieren (optional)
   - Quiz erstellen (optional)
3. **Export:**
   - JSON (strukturierte Daten)
   - PDF (lesbar mit Bildern)
   - Gamma-Präsentation (optional)

**Beispiel-Output:**
```json
{
  "title": "Deep Learning Grundlagen",
  "target_audience": "Studenten ohne Vorkenntnisse",
  "sections": [
    {
      "title": "Was ist Deep Learning?",
      "content": "Deep Learning ist...",
      "images": ["path/to/img1.png", ...],
      "supports": [...],
      "learning_objectives": ["Verstehen, was...", ...],
      "quiz": [
        {
          "question": "Was ist ein CNN?",
          "options": ["A", "B", "C", "D"],
          "correct": 1,
          "explanation": "..."
        }
      ]
    },
    ...
  ]
}
```

### 2. Gamma-Präsentation

**Integration:** [src/gamma_client.py](src/gamma_client.py)

**Workflow:**
1. Kurs-JSON generieren
2. Gamma-API aufrufen:
   - Text pro Slide
   - Bild-Upload (optional)
   - Theme auswählen
3. Gamma erstellt interaktive Präsentation
4. Link zum Bearbeiten/Teilen

**CLI:**
```powershell
python scripts/course_generator.py "Mein Kurs" --gamma
```

**GUI:** Kurs-Generator → "Als Gamma-Präsentation erstellen"

**Verfügbare Themes:**
- ISTE
- Corporate
- Minimal
- Oasis
- Schulung T-Systems Lvl-2

**Siehe:** [docs/GAMMA_API_OPTIONS.md](docs/GAMMA_API_OPTIONS.md)

### 3. Video-Generierung

#### Option A: Synthesia.io (Cloud, hochwertig)

**Integration:** [src/synthesia_client.py](src/synthesia_client.py)

**Workflow:**
1. PPTX hochladen (von Gamma exportiert)
2. Folien → Bilder extrahieren
3. Für jede Folie:
   - Text extrahieren
   - Synthesia-Avatar spricht Text
   - Video-Szene erstellen
4. Synthesia rendert finales Video

**Settings:**
- Avatar: z.B. "anna_costume1_cameraA"
- Sprache: de-DE, en-US, fr-FR, etc.
- Dauer pro Folie: 3-8 Sekunden

**CLI:**
```powershell
python -c "from src.synthesia_client import generate_video_from_pptx_via_synthesia; generate_video_from_pptx_via_synthesia('presentation.pptx', 'exports/', avatar='anna_costume1_cameraA')"
```

**GUI:** Video-Generator → PPTX hochladen

#### Option B: Lokale Generierung (FFmpeg)

**Integration:** [src/video_from_pptx.py](src/video_from_pptx.py)

**Workflow:**
1. PPTX → PNG-Bilder (pro Folie)
2. FFmpeg erstellt Video aus Bildern
3. Keine Voiceover (nur visuelle Slides)

**CLI:**
```powershell
python -c "from src.video_from_pptx import generate_video_from_pptx; generate_video_from_pptx('presentation.pptx', 'exports/', duration_per_slide=4.0)"
```

---

## Wartung & Diagnose

### Datenbank-Checks

#### 1. Vollständige Diagnose
```powershell
python scripts/diagnose_db.py
```
**Zeigt:**
- Node-Statistiken (Paper, Concept, Paragraph, etc.)
- Topics + Konzepte
- Papers + Struktur
- Orphan Nodes (ohne Verbindungen)
- Empty Sections
- Relation-Zählung

#### 2. Paragraph-Verknüpfungen prüfen
```powershell
python scripts/check_paragraphs.py
```
**Zeigt:**
- Papers mit Paragraph-Anzahl
- Orphan Paragraphs (ohne Paper/Section)
- Total Paragraphs

#### 3. Orphan-Paragraphs reparieren
```powershell
python scripts/fix_paragraphs.py
```
**Funktion:**
- Findet Paragraphs ohne Section-Verbindung
- Verknüpft sie mit erster Section des Papers
- Basiert auf paragraph_id-Präfix (enthält paper_id)

### Re-Ingestion

**Wann nötig:**
- Neue Extraction-Logik implementiert
- Datenbank korrumpiert
- Vector-Indizes neu aufbauen

**Prozess:**
```powershell
# 1) Alle PDFs in data/uploads/ ablegen
# 2) Re-Ingest starten
python scripts/reingest_all.py

# Bestätigung: yes
```

**Ablauf:**
1. Datenbank komplett leeren
2. Schema neu anlegen
3. Alle PDFs aus `data/uploads/` ingestieren
4. Auto-Stitching durchführen
5. Diagnose-Report ausgeben

**Dauer:** 30-60 Minuten (abhängig von PDF-Anzahl)

**Siehe:** [REINGEST_GUIDE.md](REINGEST_GUIDE.md)

### Neo4j Browser Queries

**Nützliche Queries:** [NEO4J_VISUALIZATION_QUERIES.md](NEO4J_VISUALIZATION_QUERIES.md)

**Beispiele:**

```cypher
// Übersicht: Alle Node-Typen
MATCH (n)
RETURN labels(n) AS NodeType, count(n) AS Count
ORDER BY Count DESC;

// Paper + Struktur
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (p)-[:HAS_FIGURE]->(fig:Figure)
RETURN p.title, count(para) AS Paragraphs, count(fig) AS Figures;

// Konzept-Netzwerk
MATCH (c1:Concept)-[r:SEMANTIC_RELATION]->(c2:Concept)
RETURN c1.name, r.relation_type, c2.name, r.confidence
ORDER BY r.confidence DESC
LIMIT 50;

// Orphan Paragraphs finden
MATCH (para:Paragraph)
WHERE NOT EXISTS ((:Paper)-[:HAS_PARAGRAPH]->(para))
  AND NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
RETURN count(para) AS OrphanCount;
```

---

## Bekannte Limitationen & Lösungen

### 1. LLM-Timeouts beim Ingest (GELÖST)

**Problem:** Alte Konzept-Linking-Logik versuchte, Konzepte während Ingestion mit Paragraphen zu verlinken → Timeouts

**Lösung:** Vector-based Retrieval
- Konzepte werden OHNE Paragraph-Links extrahiert
- Matching zur Abfragezeit via Embeddings
- 1 LLM-Call pro Paper statt hunderte

### 2. Niedrige Konzept-Coverage (GELÖST)

**Problem:** String-Matching fand nur 15-20% der Konzepte in Paragraphen

**Lösung:** Semantische Ähnlichkeit
- Vector-Search findet auch verwandte Inhalte
- Robust bei Variationen (Plural, Synonyme)

### 3. Empty Sections

**Problem:** Sections ohne Paragraphs/Figures

**Ursache:** PDF-Struktur-Erkennung unvollständig

**Lösung:** Auto-Stitching
- `neo.stitch_document_hierarchy()` verknüpft automatisch
- Bei Bedarf manuell: `scripts/fix_paragraphs.py`

### 4. Orphan Paragraphs

**Problem:** Paragraphs ohne Section-Zuordnung

**Lösung:**
- Auto-Stitching läuft automatisch beim Ingest
- Manuell: `python scripts/fix_paragraphs.py`

### 5. Gamma API-Fehler

**Problem:** Theme nicht verfügbar oder API-Limit erreicht

**Lösung:**
- Verfügbare Themes prüfen: [docs/GAMMA_API_OPTIONS.md](docs/GAMMA_API_OPTIONS.md)
- Fallback: Lokale PPTX-Generierung (python-pptx)

### 6. Synthesia Quota

**Problem:** Synthesia hat monatliches Video-Minuten-Limit

**Lösung:**
- Lokale Video-Generierung nutzen (FFmpeg)
- Oder: Kürzere Präsentationen erstellen

---

## Best Practices

### PDF-Ingestion
1. **Dateigröße:** Max. 50 MB pro PDF (sonst splitten)
2. **Format:** Text-PDFs bevorzugt (keine gescannten Bilder)
3. **Sprache:** Deutsch/Englisch optimal (spaCy-Modelle)
4. **Metadaten:** Author, Year, DOI in PDF-Properties → automatisch extrahiert

### Konzeptextraktion
1. **LLM-Mode:** Standard für allgemeine Texte
2. **Hybrid-Mode:** Wissenschaftliche Papers (SciSpacy!)
3. **max_entities:** 20-40 (zu viele → Noise)
4. **max_relations:** 15-30

### Retrieval
1. **Concept-Retrieval:** Immer aktivieren (bessere Ergebnisse)
2. **k_paragraphs:** 40-60 für Kurse, 20-30 für kurze Antworten
3. **min_concept_score:** 0.6 (Standard), 0.7 (präziser)

### Kursgenerierung
1. **Topics:** 3-6 Hauptthemen (nicht zu viele)
2. **Paragraphs pro Topic:** 40-60 (genug Kontext)
3. **Bilder:** 5-8 pro Thema (nicht überladen)
4. **Lernziele:** Ja (strukturiert besser)
5. **Quiz:** Optional (mehr Zeit)

### Datenbank-Wartung
1. **Regelmäßige Backups:** Neo4j Aura macht automatische Snapshots
2. **Diagnose:** Wöchentlich `diagnose_db.py` laufen lassen
3. **Re-Ingest:** Bei größeren Code-Änderungen

---

## Troubleshooting

### "No concepts found"
- **Ursache:** Query zu spezifisch oder keine passenden PDFs
- **Lösung:** Breitere Frage stellen oder mehr PDFs ingestieren

### "Vector index not found"
- **Ursache:** Schema nicht angelegt
- **Lösung:** `python scripts/create_schema.py`

### "OpenAI API Error: Rate limit"
- **Ursache:** Zu viele API-Calls
- **Lösung:** Warten oder API-Tier upgraden

### "Neo4j connection failed"
- **Ursache:** Falsche Credentials oder DB offline
- **Lösung:** `.env` prüfen, Neo4j Aura Console checken

### "Synthesia API Error"
- **Ursache:** Quota erschöpft oder ungültiger Avatar
- **Lösung:** Account prüfen, lokale Generierung nutzen

### "Empty answer / No supports"
- **Ursache:** Keine relevanten Dokumente in DB
- **Lösung:** Passende PDFs ingestieren oder Web-Mode aktivieren

---

## Weiterführende Dokumentation

### Detaillierte Guides
- [VECTOR_BASED_RETRIEVAL.md](VECTOR_BASED_RETRIEVAL.md) - Vector-Retrieval-Architektur
- [RETRIEVAL_FLOW.md](RETRIEVAL_FLOW.md) - Detaillierter Retrieval-Ablauf
- [REINGEST_GUIDE.md](REINGEST_GUIDE.md) - Re-Ingestion-Prozess
- [NEO4J_VISUALIZATION_QUERIES.md](NEO4J_VISUALIZATION_QUERIES.md) - Nützliche Cypher-Queries

### Spezial-Themen
- [docs/CONCEPT_BASED_RETRIEVAL.md](docs/CONCEPT_BASED_RETRIEVAL.md) - Konzept-Retrieval-Details
- [docs/SEMANTIC_RELATIONS.md](docs/SEMANTIC_RELATIONS.md) - Semantische Relationen
- [docs/PLOTLY_IMPLEMENTATION.md](docs/PLOTLY_IMPLEMENTATION.md) - Graph-Visualisierung
- [docs/GAMMA_API_OPTIONS.md](docs/GAMMA_API_OPTIONS.md) - Gamma.app Integration
- [docs/IMAGE_EXTRACTION_IMPROVEMENTS.md](docs/IMAGE_EXTRACTION_IMPROVEMENTS.md) - Bild-Extraktion
- [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) - Systemarchitektur-Diagramme

---

## Lizenz & Credits

**Entwickelt als:** Master-Thesis-Projekt
**Technologien:**
- Neo4j AuraDB
- OpenAI GPT-4
- Streamlit
- spaCy / SciSpacy
- PyMuPDF
- Gamma.app
- Synthesia.io

---

**Letzte Aktualisierung:** Januar 2026
**Version:** 2.0 (Vector-Based Architecture)
