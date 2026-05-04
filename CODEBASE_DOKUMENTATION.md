# Vollständige Codebase-Dokumentation: GraphRAG-System für automatisierte Kursgenerierung
> Masterthesis-Kontextdokument · Stand: Mai 2026  
> Eingabe für nachfolgende KI-gestützte Ausarbeitung

---

## Inhaltsverzeichnis

1. [Systemüberblick & Forschungskontext](#1-systemüberblick--forschungskontext)
2. [Systemarchitektur](#2-systemarchitektur)
3. [Technologie-Stack & Abhängigkeiten](#3-technologie-stack--abhängigkeiten)
4. [Datenbankschema (Neo4j)](#4-datenbankschema-neo4j)
5. [Kernmodul: Konfiguration](#5-kernmodul-konfiguration-srcconfigpy)
6. [Kernmodul: PDF-Ingestion](#6-kernmodul-pdf-ingestion-srcpdf_ingestpy)
7. [Kernmodul: Neo4j-Client](#7-kernmodul-neo4j-client-srcneopy)
8. [Kernmodul: Konzeptextraktion](#8-kernmodul-konzeptextraktion-srcconcept_extractpy)
9. [Kernmodul: Entity & Relation Extraction](#9-kernmodul-entity--relation-extraction-srcentity_relation_extractpy)
10. [Kernmodul: Retrieval](#10-kernmodul-retrieval-srcretrieverpy)
11. [Kernmodul: Agent-Orchestrierung](#11-kernmodul-agent-orchestrierung-srcagentpy)
12. [Kernmodul: OpenAI-Client](#12-kernmodul-openai-client-srcopenai_clientpy)
13. [Kernmodul: Zitationsvalidierung](#13-kernmodul-zitationsvalidierung-srccitation_validatorpy)
14. [Kernmodul: Erweiterter Ingest](#14-kernmodul-erweiterter-ingest-srcingest_enhancedpy)
15. [Kernmodul: PDF-Export](#15-kernmodul-pdf-export-srcpdf_exportpy)
16. [Kernmodul: Präsentationsgenerierung](#16-kernmodul-präsentationsgenerierung)
17. [Kernmodul: Videogenerierung](#17-kernmodul-videogenerierung)
18. [Kernmodul: FastAPI (presentation_api.py)](#18-kernmodul-fastapi-srcpresentation_apipy)
19. [Skripte](#19-skripte)
20. [RAGAS Evaluation Framework](#20-ragas-evaluation-framework)
21. [Teststrategie & Testcode](#21-teststrategie--testcode)
22. [Retrieval-Architektur: Detailbeschreibung](#22-retrieval-architektur-detailbeschreibung)
23. [Pipeline-Abläufe (Sequenzdiagramme)](#23-pipeline-abläufe-sequenzdiagramme)
24. [Datenbankabfragen (Cypher)](#24-datenbankabfragen-cypher)
25. [Evolutionsgeschichte & Designentscheidungen](#25-evolutionsgeschichte--designentscheidungen)
26. [Bekannte Fehler & offene Punkte](#26-bekannte-fehler--offene-punkte)

---

## 1. Systemüberblick & Forschungskontext

### 1.1 Gegenstand

Das vorliegende System implementiert ein **GraphRAG-Hybridsystem** (Graph-augmented Retrieval-Augmented Generation) zur automatisierten Extraktion, Strukturierung und didaktischen Aufbereitung wissenschaftlicher Literatur. Ziel ist die Generierung von Schulungskursen aus einer Menge wissenschaftlicher PDFs, die in einer Neo4j-Wissensgraphdatenbank vorgehalten werden.

Das System kombiniert:
- **Graph-basierte Wissensrepräsentation** (Neo4j AuraDB) mit expliziter Dokumenthierarchie
- **Vektor-basiertes semantisches Retrieval** (OpenAI `text-embedding-3-large`, 3072 Dimensionen)
- **Hybride Konzeptextraktion** (NER via spaCy/SciSpacy + LLM via GPT-4o-mini) mit semantischer Deduplizierung
- **Semantische Relationsextraktion** (LLM-Triplets + statistisches Ko-Okkurrenz-Tracking)
- **Deterministisches Paragraph-Konzept-Linking** (Substring-Matching zur Ingest-Zeit)
- **Didaktische Antwortgenerierung** mit Quellenbelegen und automatischer Zitationsvalidierung
- **Präsentations- und Videogenerierung** via externe APIs (Gamma, Synthesia) oder lokal (python-pptx, moviepy)
- **Lokale LLM-Unterstützung** via LM Studio (Branch: feature/local-lmstudio-support)

### 1.2 Wissenschaftliche Relevanz

RAG-Systeme (Lewis et al., 2020) verbinden parametrisches Wissen großer Sprachmodelle mit nicht-parametrischem Dokumentwissen. GraphRAG (Edge et al., 2024) erweitert diesen Ansatz durch explizite Wissensgrafen, die semantische Beziehungen zwischen Entitäten repräsentieren und komplexere Retrievalstrategien ermöglichen.

Die vorliegende Implementierung adressiert dabei spezifisch:
1. **Skalierbarkeit**: Batch-Ingest mit automatischer Duplikaterkennung
2. **Reliabilität**: fehlertolerante JSON-Verarbeitung von LLM-Ausgaben
3. **Didaktische Qualität**: rollenbasierte Personalisierung und Zitatvalidierung
4. **Vollständige Graph-Nutzung**: MENTIONS-Kanten, SEMANTIC_RELATION und CO_OCCURS_WITH werden aktiv beim Ingest befüllt
5. **Lokale Ausführbarkeit**: Vollständiger Betrieb ohne Cloud-APIs via LM Studio möglich

---

## 2. Systemarchitektur

### 2.1 Schichtenmodell

```
┌─────────────────────────────────────────────────────────┐
│                  User Interface Layer                   │
│   Streamlit GUI (gui_app.py)   │   CLI (ask.py, ingest) │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Agent Orchestration Layer                  │
│                   src/agent.py                          │
│    Query-Routing · Tool-Selektion · Response-Synthesis  │
└──┬──────────┬──────────┬──────────┬────────────┬───────┘
   │          │          │          │            │
   ▼          ▼          ▼          ▼            ▼
Ingest    Konzept-   Retrieval   Antwort-    Entity/
Agent     agent      Agent       Agent      Relation
pdf_      concept_   retriever   openai_    entity_
ingest    extract    .py         client     relation_
.py       .py                    .py        extract.py
   │          │          │          │            │
   └──────────┴──────────┴──────────┴────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                Knowledge Storage Layer                  │
│               Neo4j AuraDB  (src/neo.py)                │
│    Papers · Sections · Paragraphs · Figures · Concepts  │
│    Vector Indexes (3 × 3072-D cosine similarity)        │
│    MENTIONS · SEMANTIC_RELATION · CO_OCCURS_WITH        │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                  External Services                      │
│  OpenAI API (GPT-4o-mini, text-embedding-3-large)       │
│  LM Studio (lokal, beliebiges Modell, wenn LLM_MODE=local)│
│  Gamma API (Präsentationsgenerierung)                   │
│  Synthesia API (KI-Videogenerierung)                    │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Verzeichnisstruktur

```
masterthesis_neu/
├── src/                          # Kernmodule (~6.500 Zeilen)
│   ├── __init__.py
│   ├── config.py                 # Zentrale Konfiguration
│   ├── neo.py                    # Neo4j-Client (Kernmodul, ~52 KB)
│   ├── openai_client.py          # LLM + Vision + Embedding API
│   ├── retriever.py              # Hybrid-Retrieval
│   ├── agent.py                  # Query-Orchestrierung
│   ├── pdf_ingest.py             # PDF-Parsing, Chunking, Embedding
│   ├── concept_extract.py        # LLM-basierte Konzextextraktion
│   ├── entity_relation_extract.py # Hybride NER + Relationsextraktion
│   ├── ingest_enhanced.py        # Erweiterter Ingest mit Qualitätsprüfung
│   ├── citation_validator.py     # Zitationsvalidierung
│   ├── pdf_export.py             # PDF-Export (ReportLab)
│   ├── gamma_client.py           # Gamma Präsentation (High-Level-Wrapper)
│   ├── gamma.py                  # Gamma API Client (Low-Level HTTP)
│   ├── synthesia_client.py       # Synthesia Video API
│   ├── speaker_script.py         # Sprecherskripte für TTS
│   ├── local_tts_video.py        # Lokale Video-Generierung (edge-tts + moviepy)
│   ├── video_from_pptx.py        # PPTX → Video-Konvertierung
│   └── presentation_api.py       # FastAPI REST-Endpunkte
├── scripts/                       # Utility-Skripte & Einstiegspunkte
│   ├── gui_app.py                # Streamlit GUI (Haupteinstieg, ~2.475 Zeilen)
│   ├── ingest.py                 # CLI-Batch-Ingest
│   ├── ask.py                    # CLI Q&A
│   ├── ask_to_pdf.py             # Q&A → PDF-Export
│   ├── course_generator.py       # Kursgenerierungs-Modul (GUI-Tab-Plugin)
│   ├── ragas_eval.py             # RAGAS-Evaluation
│   ├── reingest_all.py           # Bulk Re-Ingest aller Papiere
│   ├── clear_all.py              # Gesamten Graphen löschen
│   ├── clear_concepts.py         # Nur Konzepte löschen
│   ├── create_schema.py          # Datenbankschema anlegen
│   ├── diagnose_db.py            # DB-Diagnostik (Knotenanzahlen etc.)
│   ├── check_paragraphs.py       # Paragraph-Integrität prüfen
│   ├── fix_paragraphs.py         # Paragraphen reparieren/aktualisieren
│   ├── update_paper_metadata.py  # Paper-Metadaten aktualisieren
│   ├── install_spacy_models.py   # spaCy-Modelle installieren
│   └── test_plotly_viz.py        # Plotly-Visualisierung testen
├── kursgenerierung/              # Experimentelle Subprojekte (unversioniert)
│   ├── Bilderkennung/            # Bilderkennungs-Experimente
│   ├── hybrid_test/              # Hybrid-RAG-Tests (LCRAG, sentence_transformers)
│   ├── knowledge_graph/          # Knowledge-Graph-Experimente
│   └── ragGraph/                 # Graph-basierte RAG-Experimente
├── data/                         # Input-PDFs + Bilder (.gitignore)
├── exports/                      # Ausgaben: PDF, PPTX, MP4 (.gitignore)
├── docs/                         # Zusätzliche Dokumentation
├── archive/                      # Archivierte Dateien
├── lib/                          # Hilfsbibliotheken
├── outputs/                      # Weitere Ausgaben
├── src/graph_schema.cypher       # Neo4j-Schemadef. (Constraints + Indizes)
├── .env                          # Lokale Secrets (in .gitignore)
├── .env.example                  # Konfigurationsvorlage (Platzhalter!)
├── requirements.txt              # Python-Abhängigkeiten
├── check_titles.py               # Root-Level Hilfsskript
└── merger.py                     # Root-Level Hilfsskript (Konzeptmerging)
```

### 2.3 Graphschema (konzeptuell)

```
(Topic)-[:HAS_CONCEPT]->(Concept)-[:SEMANTIC_RELATION {relation_type}]->(Concept)
                                 -[:CO_OCCURS_WITH {count, strength}]-(Concept)
                                 ↑[:MENTIONS {confidence}]
(Paper)-[:HAS_SECTION]->(Section)-[:HAS_PARAGRAPH]->(Paragraph)
       |-[:HAS_FIGURE]-->(Figure)<-[:CAPTIONS|REFERS_TO|NEAR]-(Paragraph)
       |-[:ABOUT {weight}]------>(Concept)
(Umbrella)-[:HAS_CONCEPT]->(Concept)
```

**Knotentypen:**
| Knoten | Schlüsseleigenschaften |
|--------|----------------------|
| `Paper` | `paper_id`, `title`, `doi`, `url`, `file_sha256`, `ingested_at` |
| `Section` | `section_id`, `title`, `level`, `page_start`, `page_end`, `order` |
| `Paragraph` | `paragraph_id`, `text`, `page`, `bbox`, `embedding` (3072-D) |
| `Figure` | `figure_id`, `caption`, `image_uri`, `figure_label`, `figure_type`, `entities`, `ocr_hints`, `embedding` (3072-D) |
| `Concept` | `concept_id`, `name`, `alt_labels`, `description`, `type`, `source`, `embedding` (3072-D) |
| `Topic` | `name` |
| `Umbrella` | `umbrella_id`, `name`, `size`, `keywords` |

**Kantentypen:**
| Kante | Von → Nach | Eigenschaften |
|-------|-----------|--------------|
| `HAS_SECTION` | Paper → Section | — |
| `HAS_PARAGRAPH` | Paper/Section → Paragraph | — |
| `HAS_FIGURE` | Paper/Section → Figure | — |
| `MENTIONS` | Paragraph → Concept | `confidence` (0.75 bei Substring-Match) |
| `HAS_CONCEPT` | Topic/Umbrella → Concept | — |
| `HAS_UMBRELLA` | Topic → Umbrella | — |
| `NARROWER` | Umbrella → Umbrella | — |
| `ABOUT` | Paper → Concept | `weight` (akkumulierte confidence) |
| `SEMANTIC_RELATION` | Concept → Concept | `relation_type`, `confidence`, `context`, `source`, `paper_id` |
| `CO_OCCURS_WITH` | Concept ↔ Concept | `count`, `strength`, `paper_id` |
| `CAPTIONS` | Paragraph → Figure | `confidence` (1.0/0.9), `match_type` |
| `REFERS_TO` | Paragraph → Figure | `confidence` (0.9/0.8), `match_type` |
| `NEAR` | Paragraph → Figure | `confidence` (0.5), `match_type` |

---

## 3. Technologie-Stack & Abhängigkeiten

### 3.1 requirements.txt

```
neo4j>=5.19.0           # Neo4j Python-Treiber
pymupdf>=1.24.10        # PDF-Parsing (fitz)
python-dotenv>=1.0.1    # Umgebungsvariablen
openai>=1.51.0          # OpenAI API (Embeddings, Chat, Vision, Web)
fastapi>=0.111.0        # REST-API (optional)
uvicorn>=0.30.0         # ASGI-Server
pillow>=10.3.0          # Bildverarbeitung
imagehash>=4.3.1        # Perceptual Hashing für Duplikaterkennung
pydantic>=2.8.2         # Datenvalidierung
tqdm>=4.66.4            # Fortschrittsanzeige (Terminal)
duckduckgo-search>=6.2.9 # Web-Fallback-Suche
reportlab>=3.6.12       # PDF-Export
streamlit>=1.37.0       # Web-GUI
streamlit-agraph>=0.0.45 # Graph-Visualisierung (agraph)
pyvis>=0.3.2            # Alternative Graph-Visualisierung
plotly>=5.18.0          # Interaktive Diagramme
networkx>=3.2.1         # Graph-Algorithmen
scipy>=1.11.0           # Wissenschaftliche Berechnungen
python-pptx>=0.6.21     # PowerPoint-Generierung (Fallback)
httpx>=0.24.1           # Async HTTP-Client (Gamma API)
spacy>=3.7.0            # NLP / Named Entity Recognition
scispacy>=0.5.4         # Wissenschaftliches NER
scikit-learn>=1.3.0     # Machine-Learning-Utilities
numpy>=1.26.0           # Vektor-Arithmetik (Embedding-Mittelung)
moviepy>=1.0.3          # Lokale Videogenerierung
edge-tts>=6.1.9         # TTS (neural, Microsoft Edge, kostenlos)
pyttsx3>=2.90           # TTS (offline, kein Internet nötig)
ragas>=0.4.3            # Evaluation-Metriken für RAG-Systeme
datasets>=4.8.5         # HuggingFace Dataset-Format (von RAGAS benötigt)
```

### 3.2 spaCy-Modelle (manuell zu installieren)

```bash
# Allgemeines NLP (Fallback)
python -m spacy download en_core_web_sm          # v3.7.1, 12.8 MB

# Wissenschaftliches NLP (bevorzugt für wissenschaftliche PDFs)
pip install https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz

# Schnelle Installation via Hilfsskript:
python scripts/install_spacy_models.py
```

Beide Modelle werden **lazy-geladen** (erst beim ersten NER-Aufruf). `en_core_sci_sm` wird bevorzugt; wenn nicht verfügbar, Fallback auf `en_core_web_sm`.

### 3.3 Externe Dienste

| Dienst | Zweck | Modell / Endpunkt |
|--------|-------|------------------|
| OpenAI Embeddings | Vektorisierung von Text | `text-embedding-3-large` (3072-D) |
| OpenAI Chat | Konzeptextraktion, Antwortgenerierung, Relationen | `gpt-4o-mini` |
| OpenAI Vision | Bildbeschreibung | `gpt-4o-mini` (multimodal) |
| OpenAI Web Search | Web-Fallback | `responses.create` mit `web_search`-Tool |
| LM Studio (lokal) | Chat + Vision + Embedding (wenn `LLM_MODE=local`) | Konfigurierbar via `.env` |
| Neo4j AuraDB | Persistenz, Vektorindizes | Neo4j ≥ 5.19 |
| Gamma API | Präsentationsgenerierung | REST-API |
| Synthesia API | KI-Videogenerierung | `https://api.synthesia.io/v1` |
| spaCy | Standardisiertes NER | `en_core_web_sm` |
| SciSpacy | Wissenschaftliches NER | `en_core_sci_sm` |
| RAGAS | Evaluation (Faithfulness, Relevanz, Precision, Recall) | `ragas>=0.4.3` |

---

## 4. Datenbankschema (Neo4j)

### 4.1 Vollständiges Schema (src/graph_schema.cypher)

```cypher
// ---------- Constraints ----------
CREATE CONSTRAINT paper_id IF NOT EXISTS
FOR (p:Paper) REQUIRE p.paper_id IS UNIQUE;

CREATE CONSTRAINT section_id IF NOT EXISTS
FOR (s:Section) REQUIRE s.section_id IS UNIQUE;

CREATE CONSTRAINT paragraph_id IF NOT EXISTS
FOR (p:Paragraph) REQUIRE p.paragraph_id IS UNIQUE;

CREATE CONSTRAINT figure_id IF NOT EXISTS
FOR (f:Figure) REQUIRE f.figure_id IS UNIQUE;

// ---------- Vector Indexes (3072-D, text-embedding-3-large) ----------
CREATE VECTOR INDEX paragraph_embedding_index IF NOT EXISTS
FOR (p:Paragraph) ON (p.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX figure_embedding_index IF NOT EXISTS
FOR (f:Figure) ON (f.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

// ---------- Concepts & Topics ----------
CREATE CONSTRAINT concept_id IF NOT EXISTS
FOR (c:Concept) REQUIRE c.concept_id IS UNIQUE;

CREATE CONSTRAINT topic_name IF NOT EXISTS
FOR (t:Topic) REQUIRE t.name IS UNIQUE;

CREATE VECTOR INDEX concept_embedding_index IF NOT EXISTS
FOR (c:Concept) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 3072,
  `vector.similarity_function`: 'cosine'
}};

// ---------- Semantic Relations ----------
CREATE INDEX relation_type_index IF NOT EXISTS
FOR ()-[r:SEMANTIC_RELATION]-() ON (r.relation_type);

CREATE INDEX relation_confidence_index IF NOT EXISTS
FOR ()-[r:SEMANTIC_RELATION]-() ON (r.confidence);

CREATE INDEX cooccurrence_strength_index IF NOT EXISTS
FOR ()-[r:CO_OCCURS_WITH]-() ON (r.strength);
```

**Wichtig:** Der Schema-Befehl wird via `scripts/create_schema.py` oder dem GUI-Reiter "Cypher" ausgeführt. Das Schema ist idempotent (`IF NOT EXISTS`).

---

## 5. Kernmodul: Konfiguration (src/config.py)

```python
NEO4J_URI       = os.getenv("NEO4J_URI")
NEO4J_USERNAME  = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD  = os.getenv("NEO4J_PASSWORD")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
GAMMA_API_KEY   = os.getenv("GAMMA_API_KEY")
GAMMA_API_URL   = os.getenv("GAMMA_API_URL")
SYNTHESIA_API_KEY  = os.getenv("SYNTHESIA_API_KEY")
SYNTHESIA_API_BASE = os.getenv("SYNTHESIA_API_BASE", "https://api.synthesia.io/v1")

# LLM-Modus: "cloud" (OpenAI, Standard) oder "local" (LM Studio)
LLM_MODE = os.getenv("LLM_MODE", "cloud").lower()

# LM Studio Konfiguration (nur wenn LLM_MODE=local)
LMSTUDIO_BASE_URL    = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
LMSTUDIO_CHAT_MODEL  = os.getenv("LMSTUDIO_CHAT_MODEL", "mistral-7b-instruct")
LMSTUDIO_VISION_MODEL = os.getenv("LMSTUDIO_VISION_MODEL", "llava-v1.5-7b")
LMSTUDIO_EMBED_MODEL  = os.getenv("LMSTUDIO_EMBED_MODEL", "nomic-embed-text")
LMSTUDIO_EMBED_DIM    = int(os.getenv("LMSTUDIO_EMBED_DIM", "768"))

DEFAULT_CHUNK_SIZE    = int(os.getenv("DEFAULT_CHUNK_SIZE",    "1200"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("DEFAULT_CHUNK_OVERLAP", "150"))

DATA_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
IMAGES_DIR = os.path.join(DATA_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

def resolve_image_path(uri: str) -> str:
    """
    Portabler Bildpfad-Resolver:
    1. Absoluter Pfad + existiert → direkt zurückgeben
    2. Nur Dateiname → mit IMAGES_DIR kombinieren
    3. Sonst → uri unverändert zurückgeben
    """
```

**Hinweis:** Der `data/`-Ordner (inklusive `data/images/`) und `exports/` sind in `.gitignore` eingetragen. `.env.example` enthält nur Platzhalter — **niemals echte Credentials einchecken**.

**LM Studio Modus:** Wenn `LLM_MODE=local`, leitet `openai_client.py` alle LLM-Calls an den lokalen LM Studio Server um. Embeddings werden via das in `.env` konfigurierte Modell erstellt. Beachte: Die Vektorindizes in Neo4j sind auf 3072 Dimensionen angelegt; bei lokalen Embedding-Modellen mit abweichender Dimensionalität (Standard: 768) muss das Schema neu angelegt werden.

---

## 6. Kernmodul: PDF-Ingestion (src/pdf_ingest.py)

### 6.1 Überblick

Die PDF-Ingestion extrahiert aus wissenschaftlichen PDFs:
1. **Textblöcke** (Paragraphen) mit Layoutinformationen (BBox, Seitenposition)
2. **Bilder** (Figures) mit automatischer Caption-Erkennung und Duplikatfilterung
3. **Dokumentstruktur** (Sections) aus dem PDF-Inhaltsverzeichnis
4. **Metadaten** (DOI, URL, Autor, Titel) aus PDF-Metadaten und Heuristiken

### 6.2 Schlüsselfunktionen

```python
def sha256(text: str) -> str
def file_sha256(path: str) -> str
    """SHA256-Hashing für Duplikaterkennung."""

def extract_doi_and_url(doc) -> tuple[str|None, str|None]:
    """Zweistufige DOI/URL-Extraktion: PDF-Metadaten → Regex auf ersten 2 Seiten."""

def extract_sections(doc) -> list[dict]:
    """TOC-Extraktion; berechnet Seitenbereiche (page_start, page_end) pro Section."""

def extract_title_from_first_page(doc) -> str:
    """Titelextraktion via Schriftgröße-Heuristik (größter Text auf Seite 1)."""

def find_caption_for_image(image_bbox, text_blocks, y_tolerance=50, x_tolerance=20):
    """
    Caption-Suche: Textblöcke unterhalb des Bildes, sortiert nach Abstand.
    Extrahiert figure_label via Regex: "Figure 1:", "Fig. 2.3:", "Abb. 5"
    """

def extract_paragraph_blocks(page) -> list[dict]:
    """
    Robuste Textblock-Extraktion via page.get_text('rawdict').
    Fallback: Plain-Text-Chunking (chunk_size=1200, overlap=150).
    Filtert: <50 Zeichen, spaltet: >600 Zeichen.
    """

def extract_images_with_bbox(page) -> list[dict]:
    """Bild-Extraktion mit Bounding Boxes, perceptual hashing für Duplikate."""

def read_pdf_text_and_images(path) -> tuple:
    """KERNFUNKTION: Gibt (paper_meta, sections, paragraphs, figures) zurück."""
```

### 6.3 Embed-Funktionen mit Fortschritts-Callback

```python
def embed_paragraphs(
    paragraphs: List[Dict[str, Any]],
    progress_fn=None,           # progress_fn(label: str, pct: int)
) -> List[Dict[str, Any]]:
    """
    Batch-Embedding aller Paragraphen via text-embedding-3-large (cloud)
    oder konfiguriertem LM-Studio-Modell (lokal).
    Wenn progress_fn=None: tqdm-Fortschrittsbalken im Terminal.
    """

def analyze_and_embed_figures(
    figures: List[Dict[str, Any]],
    progress_fn=None,
) -> List[Dict[str, Any]]:
    """
    Für jede Figur:
    1. describe_image(path) via GPT-4o-mini Vision (oder LM Studio Vision)
       → caption, figure_type, entities, ocr_hints
    2. Reiches Embedding: caption + figure_type + entities + ocr_hints + label
    """
```

---

## 7. Kernmodul: Neo4j-Client (src/neo.py)

### 7.1 Verbindungsmanagement

```python
class Neo4jClient:
    def __init__(self) -> None:
        self.driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
            max_connection_lifetime=3600,       # 1 Stunde
            max_connection_pool_size=50,
            connection_acquisition_timeout=120,
            connection_timeout=30,
            keep_alive=True
        )

    def run(self, cypher: str, params=None) -> List[Dict[str, Any]]:
        """Retry-Logik (max. 2 Versuche). Timeout: 300 s. Retry bei connection-Fehlern."""
```

### 7.2 Batch-Upsert-Methoden

```python
def upsert_paper(self, paper_meta: dict) -> None
def add_sections(self, paper_id: str, sections: list) -> None

def add_paragraphs(self, paper_id: str, paragraphs: list[dict]) -> None:
    """Batch-Größe: 25. MERGE-Semantik (idempotent). Verknüpft mit Sections."""

def add_figures(self, paper_id: str, figures: list[dict]) -> None:
    """
    Batch-Größe: 10. Speichert auch:
    - figure_type (chart|diagram|photo|table-scan|other)
    - entities   (Liste von Schlüsselbegriffen aus VLM-Analyse)
    - ocr_hints  (erkannter Text aus VLM-Analyse)
    - image_filename
    """
```

### 7.3 Vektorsuche

```python
def vector_search_paragraphs(self, embedding, k=12) -> list[dict]
def vector_search_figures(self, embedding, k=6) -> list[dict]
def vector_search_concepts(self, embedding, k=10, min_score=0.0) -> list[dict]
```

### 7.4 Auto-Stitching

```python
def stitch_document_hierarchy(self) -> dict:
    """
    1. Dummy-Sections für Papers ohne TOC.
    2. Paragraphen → passende Sections (page-range).
    3. Figures → passende Sections (page-range).
    """

def stitch_figures_to_paragraphs(self, prefix_length=60, page_tolerance=1) -> dict:
    """
    Drei-Pass-Algorithmus (alle MERGE, idempotent):
    PASS 1a: CAPTIONS (prefix 60 Zeichen, ±1 Seite) → confidence=1.0
    PASS 1b: CAPTIONS (prefix 25 Zeichen, gleiche Seite) → confidence=0.9
    PASS 2a: REFERS_TO (figure_label direkt) → confidence=0.9
    PASS 2b: REFERS_TO (Varianten: fig/figure/abb + Ziffern) → confidence=0.8
    PASS 3:  NEAR (nächster Paragraph auf gleicher Seite) → confidence=0.5
    """
```

### 7.5 Konzept-Management

```python
def upsert_topic(self, name: str) -> None
def add_concepts(self, topic_name: str, concepts: list[dict]) -> None:
    """MERGE-Semantik. Hängt Concepts an Topic via HAS_CONCEPT."""

def link_paragraphs_to_concepts(self, paper_id: str, links: list[dict]) -> None:
    """
    Erstellt:
    1. (Paragraph)-[:MENTIONS {confidence}]->(Concept)
    2. (Paper)-[:ABOUT {weight}]->(Concept)  ← akkumulierte confidence
    links-Format: [{paragraph_id, concept_id, confidence}]
    """

def link_figures_to_concepts(self, figures: list[dict]) -> dict:
    """
    Verknüpft Figures mit Concepts basierend auf entities aus VLM-Analyse.
    Erstellt: (Figure)-[:MENTIONS {confidence: 0.8, source: 'vision_analysis'}]->(Concept)
    """

def list_concepts(self, topic: str = None) -> list[dict]
def update_concept(self, concept_id: str, updates: dict) -> None
def delete_concept(self, concept_id: str) -> None
def merge_concepts(self, source_id: str, target_id: str) -> dict
```

### 7.6 Umbrella-Clustering

```python
def cluster_concepts_into_umbrellas(self, topic: str, threshold: float = 0.75) -> dict:
    """
    Greedy-Clustering via Embedding-Similarity.
    Erstellt Umbrella-Knoten und verknüpft Konzepte.
    LLM-basierte Umbrella-Namensgebung (gpt-4o-mini).
    """

def list_umbrellas(self, topic: str = None) -> list[dict]
def rename_umbrella(self, umbrella_id: str, new_name: str) -> None
def delete_umbrella(self, umbrella_id: str) -> None
def merge_umbrellas(self, source_id: str, target_id: str) -> dict
```

### 7.7 Paper-Management

```python
def list_papers(self) -> list[dict]
def delete_paper(self, paper_id: str) -> dict:
    """Löscht Paper und alle verknüpften Nodes (Sections, Paragraphs, Figures)."""
```

### 7.8 Semantische Relationen

```python
def add_semantic_relations(self, paper_id: str, relations: list[dict]) -> dict:
    """
    MERGE auf (subject)-[:SEMANTIC_RELATION {relation_type: predicate}]->(object)
    relations-Format: [{subject, predicate, object, confidence, context, source}]
    Returns: {created, updated, skipped}
    """

def add_cooccurrence_relations(self, paper_id: str, cooccurrences: list[dict]) -> dict:
    """
    Schreibt CO_OCCURS_WITH-Kanten (ungerichtet). Akkumuliert count.
    cooccurrences-Format: [{concept1, concept2, count, strength}]
    Returns: {created, updated}
    """

def get_concept_relations(self, concept_id: str) -> list[dict]
```

---

## 8. Kernmodul: Konzeptextraktion (src/concept_extract.py)

### 8.1 Überblick

Das Modul implementiert zwei Extraktions-Pipelines:
1. **`extract_and_embed_concepts`** (Legacy, LLM-only) — für spezielle Anwendungsfälle mit Seed-Konzepten und semantischer Deduplizierung
2. **`extract_and_embed_concepts_hybrid`** (Produktiv) — vollständige NER+LLM+Relationen-Pipeline

### 8.2 Hilfsfunktionen

```python
def _slug(s: str) -> str:
    """Lowercase → Sonderzeichen zu '-' → max. 80 Zeichen. Fallback: UUID."""

def _embed(texts: List[str]) -> List[List[float]]:
    """Batch-Embedding via text-embedding-3-large oder lokalem LM-Studio-Modell."""

def _try_parse_llm_response(raw: str) -> dict:
    """
    Drei Fallback-Strategien:
    1. Direktes json.loads()
    2. Bereinigung (Kommentare, trailing commas) + json.loads()
    3. Regex-Extraktion {.*} aus umgebendem Text
    Fallback: {"concepts": [], "links": []}
    """
```

### 8.3 LLM-Konzeptextraktion (Legacy)

```python
def _ask_llm_for_concepts(title, paragraphs, topic_hint, max_concepts, seed_names, allow_new):
    """
    LLM-Aufruf via client.chat.completions.create (gpt-4o-mini, temperature=0.2).
    Input: max. 80 Paragraphen, je max. 500 Zeichen.
    Output: {"concepts":[{name, alt_labels, description}], "links":[{paragraph_id, concept_name, confidence}]}
    """
```

### 8.4 Legacy-Funktion: extract_and_embed_concepts

```python
def extract_and_embed_concepts(
    paper_title, paragraphs, topic_hint="Künstliche Intelligenz",
    max_concepts=30, seed_names=None, allow_new=True,
    neo_client=None, dedupe_threshold=0.92, min_confidence=0.0,
    persist_to_topic=False
) -> Tuple[List[Dict], List[Dict]]:
    """
    Vollständige LLM-basierte Pipeline mit semantischer Deduplizierung:
    1. Seeds vorbereiten (mit Embeddings)
    2. LLM-Vorschläge abrufen
    3. Konzepte kanonisieren (Duplikate gegen Seeds filtern)
    4. Embeddings für neue Konzepte + Deduplizierung gegen Neo4j
       (dedupe_threshold=0.92: score >= threshold → mapped_existing)
    5. Links Paragraph→Concept per Name matchen
    Returns: (concepts, links)
    """
```

### 8.5 Hybride Hauptfunktion: extract_and_embed_concepts_hybrid

```python
def extract_and_embed_concepts_hybrid(
    paper_title: str,
    paper_text: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str = "Künstliche Intelligenz",
    max_entities: int = 30,
    max_relations: int = 20,
    use_scispacy: bool = True,
    neo_client: Optional['Neo4jClient'] = None,
    persist_to_topic: bool = False,
    progress_fn=None,
) -> Dict[str, Any]:
    """
    Vollständige Hybrid-Pipeline (NER + LLM + Relationen):

    Step 1 — extract_entities_and_relations(paper_text, paragraphs, ...):
      a. NER via SciSpacy (en_core_sci_sm) bevorzugt, Fallback: spaCy (en_core_web_sm)
      b. LLM-Konzeptextraktion für abstrakte Konzepte
      c. Embeddings für alle extrahierten Entitäten
      d. Semantische Triplet-Extraktion via LLM
      e. Ko-Okkurrenz-Analyse (Fenster: 50 Zeichen, min. 2 Vorkommen)

    Step 2 — Konvertierung zu Concept-Dicts (Deduplizierung via slug-Set)

    Step 3 — Paragraph → Concept Links (deterministisch):
      Substring-Matching: cname_lower in paragraph_text.lower()
      Mindestlänge: 3 Zeichen. confidence: 0.75

    Step 4 — Optional: Persistenz in Neo4j

    Returns:
      {
        "concepts":        [{concept_id, name, type, source, description, alt_labels, embedding}],
        "relations":       [{subject, predicate, object, confidence, context}],
        "paragraph_links": [{paragraph_id, concept_id, confidence}],
        "stats":           {total_entities, semantic_relations, cooccurrence_relations,
                            total_paragraphs, concepts_created, paragraph_links, relations_total}
      }
    """
```

---

## 9. Kernmodul: Entity & Relation Extraction (src/entity_relation_extract.py)

### 9.1 Hybride NER-Pipeline

```python
def extract_entities_ner(text: str, use_scispacy: bool = True) -> Dict[str, List[str]]:
    """
    Zweistufige NER (beide Modelle lazy-geladen):
    1. Primär: SciSpacy (en_core_sci_sm) → wissenschaftliche Fachbegriffe
    2. Fallback: spaCy (en_core_web_sm) → allgemeines NER
    Entity-Typen: PERSON, ORG, GPE, DATE, SCIENTIFIC_TERM, CHEMICAL, DISEASE, OTHER
    """

def extract_concepts_llm(text, max_concepts=15, existing_entities=None) -> list:
    """
    LLM-Konzeptextraktion für abstrakte Konzepte.
    Typen: methodology | theory | technique | domain_term | abstract_concept
    """

def extract_entities_hybrid(text, max_llm_concepts=15, use_scispacy=True) -> dict:
    """
    Fusion: NER + LLM, nur relevante NER-Typen (SCIENTIFIC_TERM, CHEMICAL, DISEASE, ORG).
    Returns: {ner_entities, llm_concepts, all_entities}
    """
```

### 9.2 Semantische Relationsextraktion

```python
def extract_relations_llm(text, entities, max_relations=20) -> list:
    """
    Triplet-Extraktion: (Subject, Predicate, Object)
    Standardisierte Prädikate: is_a, part_of, uses, requires, causes, leads_to,
                               improves, evaluates, applies_to, based_on, extends
    Output: [{subject, predicate, object, confidence, context (max. 200 Zeichen)}]
    """

def extract_cooccurrence_relations(entities, paragraphs, window_size=50) -> list:
    """
    Statistisches Ko-Okkurrenz-Tracking:
    - Fenster: 50 Zeichen
    - Minimum: 2 gemeinsame Vorkommen
    - confidence: min(0.9, 0.5 + count × 0.1)
    - predicate: "co_occurs_with"
    """
```

### 9.3 Integrierte Pipeline

```python
def extract_entities_and_relations(
    text, paragraphs=None,
    max_entities=30, max_relations=20,
    use_scispacy=True, extract_cooccurrence=True
) -> dict:
    """
    1. extract_entities_hybrid()         → Entitäten (NER + LLM)
    2. _embed() auf Entity-Namen         → Embeddings (3072-D Cloud / LMSTUDIO_EMBED_DIM lokal)
    3. extract_relations_llm()           → Semantische Triplets
    4. extract_cooccurrence_relations()  → Statistische Relationen (optional)
    Returns: {entities, relations, stats}
    """
```

---

## 10. Kernmodul: Retrieval (src/retriever.py)

### 10.1 Vektorsuchfunktionen

```python
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")  # 3072-D

def _vsearch_paragraphs(neo, embedding, k=24) -> list
def _vsearch_figures(neo, embedding, k=8) -> list
def _vsearch_concepts(neo, embedding, k=10, min_score=0.6) -> list:
    """min_score=0.6: Konzeptnamen sind kurz → geringere Ähnlichkeitswerte."""
```

### 10.2 Konzept-zu-Paragraph Überbrückung

```python
def _paragraphs_via_concepts(neo, concept_ids, limit=20) -> list:
    """
    Vektorbasierter Ansatz (keine MENTIONS-Traversierung nötig):
    1. Concept-Embeddings aus Neo4j abrufen
    2. numpy.mean(embeddings, axis=0) → semantisches Zentroid
    3. Vektorsuche auf paragraph_embedding_index mit Durchschnittsvektor
    """

def _expand_via_semantic_relations(neo, concept_ids, k=5) -> list:
    """Erweitert Konzeptliste über SEMANTIC_RELATION-Kanten (IS_A, PART_OF, RELATED_TO)."""

def _expand_figure_context_with_paragraphs(neo, supports, limit=200) -> None:
    """
    Hängt zu Figures passende Paragraphen via REFERS_TO|CAPTIONS|NEAR.
    Score: 0.99 (CAPTIONS/REFERS_TO), 0.75 (NEAR). In-place Modifikation.
    """
```

### 10.3 Öffentliche Retrieval-API

```python
def hybrid_retrieve(neo, query, *, k_paragraphs=18, k_figures=6,
                    add_figure_context=True) -> dict:
    """Einfaches Hybrid-Retrieval: Query-Embedding → Paragraphen + Figures."""

def concept_based_retrieve(neo, query, *, k_concepts=10, k_paragraphs_direct=15,
                           k_paragraphs_via_concepts=20, k_figures=6,
                           expand_semantic=True, add_figure_context=True,
                           min_concept_score=0.6) -> dict:
    """
    Intelligentes Concept-basiertes Retrieval:
    1. Concept-Vektorsuche (k=10, min_score=0.6)
    2. Expansion via SEMANTIC_RELATION (IS_A, PART_OF, RELATED_TO)
    3. Paragraphen via gemitteltes Concept-Embedding
    4. Direkte Paragraphen-Vektorsuche (Ergänzung)
    5. Figure-Vektorsuche
    6. Deduplizierung (Concept-basiert hat Priorität)
    7. Figure-Kontext ergänzen
    Returns: {supports, matched_concepts, debug}
    """
```

**Return-Format (supports-Einträge):**
```python
# Paragraph:
{"type": "paragraph", "paragraph_id", "text", "page", "paper_id", "paper_title",
 "doi", "url", "authors", "year", "source", "section_id", "section_title",
 "score", "matched_concept"}

# Figure:
{"type": "figure", "figure_id", "caption", "page", "image_uri", "bbox",
 "figure_type", "entities", "ocr_hints", "score"}
```

---

## 11. Kernmodul: Agent-Orchestrierung (src/agent.py)

### 11.1 Konfiguration

```python
USE_SERP = bool(os.getenv("SERPAPI_API_KEY", ""))
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "openai").lower()
OPENAI_WEB_MODEL    = os.getenv("OPENAI_WEB_MODEL", "gpt-4o-mini")
MIN_SUPPORTS_COUNT_DEFAULT = int(os.getenv("MIN_SUPPORTS_COUNT", "3"))
MIN_SUPPORTS_SCORE_DEFAULT = float(os.getenv("MIN_SUPPORTS_SCORE", "0.0"))
```

### 11.2 Orchestrierung (answer_query)

```python
def answer_query(query, neo, min_supports=3, min_supports_score=0.0,
                 web_mode=None, k_paragraphs=24, k_figures=8,
                 use_concept_retrieval=True) -> dict:
    """
    Drei-Wege-Entscheidungslogik:

    "force" → Websuche sofort (Graph übersprungen)
    "off"   → Nur Graph → grounded_answer()
    "auto"  → Graph → _effective_supports() zählt valide Belege
                eff >= min_supports → mode: "graph"
                eff <  min_supports → Web-Fallback → mode: "web"

    Returns: {mode, answer, supports, debug, citation_validation}
    """
```

### 11.3 Web-Fallback

```python
def answer_via_openai_web(query: str, lang="de", force_tool=None) -> dict:
    """OpenAI Web Search via responses.create (gpt-4o / gpt-4o-mini)."""

def answer_via_serp(query: str) -> dict:
    """STUB — nur wenn SERPAPI_API_KEY gesetzt. Aktuell nicht implementiert."""
```

**Wichtig:** SERP-Integration ist ein Stub. Bei `WEB_SEARCH_PROVIDER=serp` wird eine Fehlermeldung zurückgegeben. Nur `openai` ist produktiv nutzbar.

---

## 12. Kernmodul: OpenAI-Client (src/openai_client.py)

```python
# Client-Factories (LLM_MODE-aware)
def _make_openai_client() -> openai.OpenAI
def _make_lmstudio_client() -> openai.OpenAI   # base_url=LMSTUDIO_BASE_URL
def _chat_client() -> openai.OpenAI            # Cloud oder LM Studio
def _embed_client() -> openai.OpenAI

# Modell-Selektion (LLM_MODE-aware)
def _chat_model() -> str    # "gpt-4o-mini" oder LMSTUDIO_CHAT_MODEL
def _vision_model() -> str  # "gpt-4o-mini" oder LMSTUDIO_VISION_MODEL
def _embed_model() -> str   # "text-embedding-3-large" oder LMSTUDIO_EMBED_MODEL

def embed_text(text: str, model="text-embedding-3-large") -> List[float]:
    """Einzeltext-Embedding. Lazy-initialisierter Singleton-Client."""

def describe_image(path: str) -> dict:
    """
    GPT-4o-mini Vision (oder LM Studio Vision). Base64-Encoding.
    Output: {caption, figure_type, entities, ocr_hints}
    System Prompt: Wissenschaftliche Bild-Analyse, Deutsch, no emojis.
    """

def grounded_answer(query: str, supports: List[dict]) -> str:
    """
    Didaktische Antwort mit Quellenbelegen. Rolle: "Erfahrener Dozent".
    Struktur: Definition → Erklärung → Beispiele → Zusammenfassung.
    Zitationsformat: [Pxxx] / [Fxxx] direkt im Fließtext.
    """
```

---

## 13. Kernmodul: Zitationsvalidierung (src/citation_validator.py)

```python
def validate_citations(generated_text, supports, similarity_threshold=0.65) -> dict:
    """
    Semantische Zitationsvalidierung via Kosinus-Ähnlichkeit (OpenAI-Embeddings).
    
    Formate:
    - Numerisch [1] [2]: Nur Vorhandenseins-Check
    - ID-basiert [Pxxx] [Fxxx]: Ähnlichkeit von Zitationskontext vs. Quelltext
      status: "valid" (≥ threshold) | "warning" | "invalid"
    
    Returns: {total_citations, valid_count, invalid_count, warning_count, details}
    """

def calculate_semantic_similarity(text1: str, text2: str) -> float:
    """Cosine Similarity via OpenAI-Embeddings."""
```

---

## 14. Kernmodul: Erweiterter Ingest (src/ingest_enhanced.py)

```python
def calculate_dynamic_parameters(num_paragraphs: int) -> dict:
    """
    Dynamische Parameteranpassung an Dokumentgröße:
    max_entities  = min(50, max(20, n // 10))
    max_relations = min(30, max(10, n // 15))
    k_paragraphs  = min(40, max(15, n //  5))
    k_figures     = min(12, max( 5, n // 20))
    """

def infer_topic_from_title(title: str) -> str:
    """Keyword-basierte Topic-Inferenz. Fallback: 'Künstliche Intelligenz'."""

def check_for_duplicates(neo, paper_meta) -> Optional[dict]:
    """Drei-Stufen: file_sha256 → DOI → Titel (toLower)."""

def extract_enhanced_metadata(pdf_path, paper_meta) -> dict:
    """Ergänzt: file_size, ingested_at, pdf_author/subject/keywords, page_count."""

def validate_ingestion_quality(report: dict) -> dict:
    """
    Qualitätsprüfung nach Ingest:
    - n_concepts == 0           → Issue: "Keine Konzepte extrahiert"
    - n_links == 0 bei n_concepts > 0 → Issue: "Konzepte nicht verknüpft"
    - n_figures == 0 bei n_paragraphs > 10 → Warning
    Returns: {quality_score (0–100), quality_label, issues, warnings, recommendations}
    """

def create_ingestion_summary(paper_id, neo, paragraphs, concepts) -> dict
```

---

## 15. Kernmodul: PDF-Export (src/pdf_export.py)

```python
def write_answer_pdf(query: str, answer: str, supports: list, out_path: str) -> str:
    """
    Generiert eine wissenschaftlich formatierte PDF-Datei.
    
    Inhalt:
    - Titel: Fragestellung
    - Antworttext mit Zitationen [Pxxx]/[Fxxx]
    - Inline-Bilder (Figures) mit Auto-Skalierung (max. 45% Seitenhöhe)
    - Literaturverzeichnis [1], [2], ... im Anhang
    
    Technologie: ReportLab
    """
```

---

## 16. Kernmodul: Präsentationsgenerierung

### 16.1 src/gamma.py — Gamma API Client (Low-Level)

```python
class GammaClient:
    def __init__(self, api_key: str, base: str)
    
    def generate(self, body: dict) -> str:
        """POST /generations → generationId"""
    
    def poll(self, generation_id: str) -> dict:
        """GET /generations/{id} → {status, gammaUrl, datei-URLs}"""
    
    def download_file(self, file_url: str, out_dir: str, filename: str) -> str:
        """Lädt generierte Datei herunter → lokaler Pfad"""
    
    def list_themes(self) -> list:
        """Best-effort Theme-Liste (mehrere Endpunkte probiert)"""
```

### 16.2 src/gamma_client.py — High-Level-Wrapper

```python
def generate_presentation(
    title: str,
    answer_text: str,
    supports: list,
    use_gamma: bool = True,
    out_dir: str = "exports",
    template_path: str = None,
    font_sizes: dict = None
) -> dict:
    """
    Generiert eine Präsentation.
    
    Wenn use_gamma=True:
      → Gamma API → PPTX/PDF Download
    
    Fallback (use_gamma=False oder API-Fehler):
      → _create_local_pptx() via python-pptx
      → Template-Support, customizable Schriftgrößen
      → Bild-Download und Embedding in Slides
    
    Returns: {"method": "gamma"|"local", "result": {...}, "slides": [...]}
    """
```

---

## 17. Kernmodul: Videogenerierung

### 17.1 src/speaker_script.py — Sprecherskripte

```python
def create_speaker_script(answer_text: str, supports: list) -> dict:
    """
    Erstellt optimiertes TTS-Skript aus Antworttext.
    
    - _normalize_text(): Markdown/Leerzeichen bereinigen
    - _optimize_for_speech(): Zahlenwörter, Abkürzungen anpassen
    - _estimate_duration(words_per_minute): "1m 30s"
    - _split_into_sentences(max_length): Satzweise Aufteilung
    
    Returns: {"script": str, "duration": str, "metadata": {...}}
    """
```

### 17.2 src/local_tts_video.py — Lokale Video-Generierung

```python
def generate_video_with_local_tts(
    slides_data: list,
    out_dir: str = "exports",
    lang: str = "de",
    engine: str = "edge-tts"   # oder "pyttsx3"
) -> str:
    """
    Vollständige lokale Video-Generierung ohne externe APIs:
    1. _convert_text_to_speech(text, lang, engine) → audio.wav
       - edge-tts: Microsoft Neural TTS (kostenlos, Internetverbindung)
       - pyttsx3: Vollständig offline
    2. _create_video_from_slides(slides_images, audio, fps) via moviepy
    Returns: MP4-Pfad
    """
```

### 17.3 src/video_from_pptx.py — PPTX → Video

```python
# Konvertiert eine PPTX-Datei in ein MP4-Video.
# Jede Folie wird gerendert und mit Sprecher-Audio versehen.
```

### 17.4 src/synthesia_client.py — Synthesia API

```python
def generate_video_from_pptx_via_synthesia(
    pptx_path: str,
    out_dir: str,
    voice: str,
    api_key: str,
    api_base: str,
    wait: bool,
    total_minutes: int,
    per_slide_seconds: int,
    fallback_local: bool,
    width: int, height: int
) -> str:
    """
    Synthesia API-Integration:
    - PPTX → Slide-Texte extrahieren
    - Image-Rendering aus Slide-Text
    - API-Aufruf mit Timeout-Handling
    - Fallback zu localem Rendering wenn fallback_local=True
    Returns: MP4-Pfad
    """
```

---

## 18. Kernmodul: FastAPI (src/presentation_api.py)

```python
from fastapi import FastAPI
app = FastAPI()

@app.post("/presentations")
def create_presentation(req: CreateReq) -> dict:
    """CreateReq: {query, use_gamma, web_mode}"""

@app.get("/presentations/download")
def download_presentation(path: str):
    """File-Download via FileResponse."""
```

**Hinweis:** FastAPI ist optional und für den produktiven Einsatz als REST-Backend gedacht. Gestartet via `uvicorn src.presentation_api:app`.

---

## 19. Skripte

### 19.1 scripts/ingest.py — CLI-Batch-Ingest

CLI-Einstiegspunkt. Aufruf: `python scripts/ingest.py paper1.pdf paper2.pdf ...`

Vollständige Pipeline je PDF:
```
1. check_for_duplicates()              → Abbruch wenn bereits ingested
2. read_pdf_text_and_images()          → paper_meta, sections, paragraphs, figures
3. neo.upsert_paper()                  → Paper-Knoten
4. neo.add_sections()
5. embed_paragraphs()                  → Embeddings
6. neo.add_paragraphs()
7. analyze_and_embed_figures()         → VLM-Analyse + Embeddings
8. neo.add_figures()
9. neo.link_figures_to_concepts()      → Figure→Concept MENTIONS
10. extract_and_embed_concepts_hybrid() → NER + LLM + Relationen
    ├─ neo.link_paragraphs_to_concepts()  → MENTIONS-Kanten
    ├─ neo.add_semantic_relations()       → SEMANTIC_RELATION-Kanten
    └─ neo.add_cooccurrence_relations()   → CO_OCCURS_WITH-Kanten
11. neo.stitch_document_hierarchy()
12. neo.stitch_figures_to_paragraphs()
```

### 19.2 scripts/gui_app.py — Streamlit GUI

Streamlit-GUI (~2.475 Zeilen) mit 7 Tabs:

| Tab | Titel | Funktion |
|-----|-------|---------|
| 1 | **Dokumente aufnehmen** | PDF-Upload, Enhanced Ingest mit Echtzeit-Fortschrittsbalken, Duplikaterkennung |
| 2 | **Paperverwaltung** | Liste aller ingestierten Paper, Metadaten-Bearbeitung, Paper löschen |
| 3 | **Kursgenerator** | Kursstruktur generieren (Plugin: `scripts/course_generator.py`) |
| 4 | **Slideexport** | Gamma API oder lokaler PPTX-Export, Theme-Auswahl |
| 5 | **Videoexport** | Synthesia API oder lokaler TTS+moviepy-Export |
| 6 | **Cypher** | Direkte Cypher-Queries, Graph-Visualisierung via agraph/plotly |
| 7 | **Evaluation** | RAGAS-Metriken, Halluzinationstest, Cloud-vs.-Lokal-Vergleich |

**Fortschritts-Architektur (Tab 1, `ingest_one_pdf_enhanced`):**

```python
# Drei Ebenen der Fortschrittsanzeige:
paper_progress_bar = st.progress(0)    # Gesamtfortschritt 0–100%
paper_status       = st.empty()        # Aktueller Schritt-Text
paper_percent      = st.empty()        # Prozentzahl als Metric
paper_detail       = st.empty()        # Granularer Sub-Step (caption)

def _sub(base: int, span: int):
    """Mappt internen [0, 100] auf absoluten [base, base+span]."""

# Prozentuale Zuordnung:
embed_paragraphs(...,           progress_fn=_sub(40, 10))  # 40–50%
analyze_and_embed_figures(...,  progress_fn=_sub(60, 10))  # 60–70%
extract_and_embed_concepts_hybrid(..., progress_fn=_sub(70, 20))  # 70–90%
```

**Paperverwaltung (Tab 2):** Listet alle ingestierten Paper mit Metadaten (Titel, Autoren, Jahr, DOI, Seitenanzahl, Paragraphen, Figures, Konzepte). Bietet Inline-Bearbeitung der Metadaten und eine gesicherte Löschfunktion (Bestätigungs-Dialog).

**Cypher-Tab (Tab 6):**
- Preset-Queries für häufige Graphabfragen
- Parameter-Support via `$variable` Syntax
- Graph-Visualisierung (agraph) und Tabellen-Ansicht
- Fehlerbehandlung mit Stack-Trace-Anzeige

### 19.3 scripts/course_generator.py — Kursgenerator-Plugin

Wird via `importlib.import_module("scripts.course_generator")` in Tab 3 eingebunden. Exportiert `show_course_generator()` als Streamlit-Funktion.

### 19.4 scripts/ask.py — CLI Q&A

```bash
python scripts/ask.py "Erkläre den Unterschied zwischen supervised und unsupervised learning."
# → Antwort + Belegquellen auf der Konsole
```

### 19.5 scripts/ask_to_pdf.py — Q&A → PDF

Wie `ask.py`, schreibt Antwort zusätzlich als PDF via `write_answer_pdf()`.

### 19.6 Utility-Skripte

| Skript | Funktion |
|--------|---------|
| `reingest_all.py` | Alle Paper neu ingestieren (z.B. nach Schema-Änderung) |
| `clear_all.py` | Gesamten Graphen leeren (VORSICHT: unumkehrbar!) |
| `clear_concepts.py` | Nur Konzept-Knoten und Relationen löschen |
| `create_schema.py` | Datenbankschema anlegen (Constraints + Indizes) |
| `diagnose_db.py` | Knotenanzahlen, Index-Status, Verbindungstest |
| `check_paragraphs.py` | Paragraph-Integrität und Embedding-Abdeckung prüfen |
| `fix_paragraphs.py` | Paragraphen reparieren (z.B. fehlende Embeddings nachfüllen) |
| `update_paper_metadata.py` | Metadaten aller Paper aktualisieren |
| `install_spacy_models.py` | spaCy und SciSpacy-Modelle installieren |
| `test_plotly_viz.py` | Plotly-Graphvisualisierung isoliert testen |

---

## 20. RAGAS Evaluation Framework

### 20.1 Überblick

`scripts/ragas_eval.py` implementiert die quantitative Qualitätsmessung der GraphRAG-Pipeline mit dem [RAGAS](https://docs.ragas.io)-Framework. Wird über den **Evaluation-Tab der Streamlit-GUI** ausgeführt. Ergebnisse: `data/eval/ragas_results.json`.

**Abhängigkeiten:**
```
ragas>=0.4.3
datasets>=4.8.5
langchain-openai  (ChatOpenAI + OpenAIEmbeddings als RAGAS-Backend)
```

### 20.2 Metriken

| Metrik | Was wird gemessen | Wertebereich |
|--------|------------------|--------------|
| **Faithfulness** | Sind alle Aussagen durch den Kontext belegt? | 0–1 (höher = besser) |
| **Answer Relevancy** | Beantwortet die Antwort die Frage? | 0–1 |
| **Context Precision** | Wie präzise ist der abgerufene Kontext? | 0–1 |
| **Context Recall** | Enthält der Kontext alle nötigen Infos? | 0–1 |

### 20.3 Testdatensatz (DEFAULT_TEST_QUESTIONS)

15 vordefinierte Fragen in vier Kategorien:

| Typ | Anzahl | Zweck |
|-----|--------|-------|
| `factual` | 5 | Einzelne Faktenfragen (Transformer, Self-Attention, RAG, Knowledge Graph, NER) |
| `cross_topic` | 5 | Themenübergreifende Fragen (GraphRAG vs. RAG, Embeddings in Graphen, …) |
| `visual` | 3 | Fragen zu Abbildungen |
| `false_context` | 2 | Halluzinationstest-Kandidaten |

### 20.4 Drei Evaluationsläufe

#### run_ragas_evaluation()
Vollständige RAGAS-Evaluation über alle 15 Fragen mit allen vier Metriken. Retrieval via `concept_based_retrieve()`, Generierung via `grounded_answer()`.

#### run_hallucination_test()
Vergleicht Faithfulness mit echtem vs. bewusst falschem Kontext (`_FALSE_FACTS`):
```python
"interpretation": (
    "hoch"   if differenz > 0.3
    else "mittel" if differenz > 0.1
    else "niedrig"
)
```

#### run_llm_comparison()
Vergleicht **GPT-4o-mini (Cloud)** mit **LM Studio (Lokal)** auf 5 Fragen:
- Retrieval läuft immer im Cloud-Modus (OpenAI 3072-D Embeddings)
- Nur Generierungsphase wird per `cfg.LLM_MODE` umgeschaltet
- Ausgabe: RAGAS-Scores, Inferenzzeit, geschätzte Kosten (USD)

### 20.5 Ausgabeformat

```json
{
  "ragas_evaluation": {
    "faithfulness": 0.87,
    "answer_relevancy": 0.91,
    "context_precision": 0.83,
    "context_recall": 0.79
  },
  "halluzinationstest": {
    "faithfulness_echter_kontext": 0.88,
    "faithfulness_falscher_kontext": 0.52,
    "halluzinations_anfaelligkeit": 0.36,
    "interpretation": "hoch"
  },
  "llm_vergleich": {
    "cloud": {"modell": "gpt-4o-mini", "ragas_scores": {...}, "inferenz_zeit_sek": 42.1},
    "local": {"modell": "llama-3-...", "ragas_scores": {...}, "inferenz_zeit_sek": 118.4}
  }
}
```

---

## 21. Teststrategie & Testcode

### 21.1 Testphilosophie

Die Testsuite folgt:
- **Vollständige API-Isolation**: Kein OpenAI-, kein Neo4j-Aufruf
- **AAA-Muster** (Arrange – Act – Assert)
- **Deterministische Stubs** mit vorhersehbarem Verhalten
- **Grenzwerttests** via `@pytest.mark.parametrize`

**Testdateien:**
- `tests/test_concept_extract.py` — Unit-Tests für Konzeptextraktion
- `test_concept_coverage.py` (root) — Konzept-Abdeckungstest gegen Live-DB
- `test_concept_index.py` (root) — Vektorindex-Tests

### 21.2 Testcode (tests/test_concept_extract.py)

```python
"""Unit-Tests für src/concept_extract.py — vollständig isoliert von externen APIs."""
import pytest
from src import concept_extract as ce

class FakeNeo:
    def vector_search_concepts(self, embedding, k=1):
        if embedding == [1.0]:
            return [{"concept_id": "existing-1", "name": "Existing Concept", "score": 0.95}]
        return []
    def run(self, cypher, params=None):
        if "MATCH (c:Concept" in cypher:
            return [{"concept_id": "existing-1", "name": "Existing Concept",
                     "alt_labels": [], "description": ""}]
        return []

@pytest.fixture(autouse=True)
def mock_embed(monkeypatch):
    def fake_embed(texts):
        return [[1.0] if "existing" in t.lower() else [0.0] for t in texts]
    monkeypatch.setattr(ce, '_embed', fake_embed)

@pytest.mark.parametrize("threshold,expect_mapped", [(0.90, True), (0.99, False)])
def test_dedupe_threshold_boundary(monkeypatch, fake_neo, sample_paragraphs,
                                   threshold, expect_mapped):
    ...

def test_parse_valid_json():
    assert ce._try_parse_llm_response('{"concepts":[{"name":"KI"}],"links":[]}')["concepts"]

def test_parse_empty_string():
    assert ce._try_parse_llm_response("") == {"concepts": [], "links": []}

def test_parse_json_with_trailing_comma():
    result = ce._try_parse_llm_response('{"concepts":[{"name":"KI",}],"links":[],}')
    assert isinstance(result, dict) and "concepts" in result
```

---

## 22. Retrieval-Architektur: Detailbeschreibung

### 22.1 Architekturevolution

| Phase | Beschreibung | Problem | Lösung |
|-------|-------------|---------|--------|
| v1 | Reines Vektor-RAG | Keine strukturelle Wissensrepräsentation | Graph-Integration |
| v2 | MENTIONS-basiertes GraphRAG | Ingest-Timeouts, 82% Paragraphen ohne Links | Vektorbasiertes Retrieval |
| v3 | Vektorbasiert, kein Linking | Leerer Graph, keine MENTIONS/Relationen | Hybrid-Extraktion reaktiviert |
| v4 (aktuell) | Vollständiges GraphRAG: Vektor + aktives Linking | — | NER+LLM+Relationen beim Ingest |

### 22.2 Vollständiger Retrieval-Pfad

```
Query: "Was ist Deep Learning?"
    │
    ├─ 1. _embed_query(query) → 3072-D Vektor
    │
    ├─ 2. concept_embedding_index → k=10, min_score=0.6
    │      → ["Deep Learning" (0.91), "Neural Networks" (0.87), ...]
    │
    ├─ 3. _expand_via_semantic_relations()
    │      → "Machine Learning" (IS_A), "CNN" (PART_OF)
    │
    ├─ 4. avg(concept_embeddings) → semantisches Zentroid
    │      → paragraph_embedding_index, limit=20
    │
    ├─ 5. paragraph_embedding_index, query_embedding, k=15  (direkt)
    │
    ├─ 6. figure_embedding_index, k=6
    │
    ├─ 7. Deduplizierung (Concept-basierte Ergebnisse priorisiert)
    │
    ├─ 8. _expand_figure_context_with_paragraphs()
    │      → REFERS_TO|CAPTIONS|NEAR Traversierung
    │
    └─ 9. grounded_answer() → GPT-4o-mini Dozenten-Prompt
           → Antwort mit [Pxxx]/[Fxxx] Zitationen
```

---

## 23. Pipeline-Abläufe (Sequenzdiagramme)

### 23.1 PDF-Ingest-Pipeline (vollständig)

```
User → GUI/CLI → ingest_one(path)
                      │
          ┌───────────▼────────────┐
          │  Phase 1: Extraktion   │
          │  read_pdf_text_and_    │
          │  images() via PyMuPDF  │
          └───────────┬────────────┘
                      │
          ┌───────────▼────────────┐
          │  Phase 2: Embeddings   │
          │  embed_paragraphs()    │
          │  analyze_and_embed_    │
          │  figures() + GPT-4o    │
          └───────────┬────────────┘
                      │
          ┌───────────▼────────────┐
          │  Phase 3: Persistenz   │
          │  neo.upsert_paper()    │
          │  neo.add_sections()    │
          │  neo.add_paragraphs()  │ ← Batch: 25
          │  neo.add_figures()     │ ← Batch: 10
          └───────────┬────────────┘
                      │
          ┌───────────▼────────────┐
          │  Phase 4: Konzepte &   │
          │  Relationen            │
          │  extract_*_hybrid()    │
          │  ├─ NER (SciSpacy)     │
          │  ├─ LLM Konzepte       │
          │  ├─ LLM Relationen     │
          │  ├─ Ko-Okkurrenz       │
          │  ├─ add_concepts()     │
          │  ├─ link_para→concept  │ ← MENTIONS (confidence=0.75)
          │  ├─ add_semantic_rel.  │ ← SEMANTIC_RELATION
          │  └─ add_cooc_rel.      │ ← CO_OCCURS_WITH
          └───────────┬────────────┘
                      │
          ┌───────────▼────────────┐
          │  Phase 5: Stitching    │
          │  stitch_document_      │
          │  hierarchy()           │
          │  stitch_figures_to_    │
          │  paragraphs()          │
          └────────────────────────┘
```

### 23.2 Präsentations- und Video-Pipeline

```
Query → answer_query() → answer_text + supports
    │
    ├─ Gamma API (use_gamma=True)
    │   └─ GammaClient.generate() → poll() → download_file()
    │
    ├─ Lokaler PPTX-Fallback
    │   └─ _create_local_pptx() → python-pptx
    │
    └─ Video-Export
        ├─ Synthesia API → generate_video_from_pptx_via_synthesia()
        └─ Lokal → create_speaker_script() → generate_video_with_local_tts()
                   (edge-tts oder pyttsx3 + moviepy)
```

---

## 24. Datenbankabfragen (Cypher)

### 24.1 Diagnostik

```cypher
-- Alle Knotentypen zählen
MATCH (n) RETURN labels(n) AS NodeType, count(n) AS Count ORDER BY Count DESC;

-- MENTIONS-Abdeckung prüfen
MATCH (para:Paragraph)-[:MENTIONS]->(c:Concept)
RETURN count(DISTINCT para) AS LinkedParagraphs;

-- Semantische Relationen prüfen
MATCH ()-[r:SEMANTIC_RELATION]->()
RETURN r.relation_type AS Type, count(*) AS Count ORDER BY Count DESC;

-- Ko-Okkurrenz-Netzwerk
MATCH ()-[r:CO_OCCURS_WITH]-() RETURN count(r) AS CoOccurrenceEdges;
```

### 24.2 Vektorsuche

```cypher
-- Paragraph-Vektorsuche
CALL db.index.vector.queryNodes('paragraph_embedding_index', $k, $embedding)
YIELD node, score
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(node)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec)-[:HAS_PARAGRAPH]->(node)
RETURN node.text AS text, p.title AS paper, toFloat(score) AS score
ORDER BY score DESC LIMIT 10;

-- Konzept-Vektorsuche
CALL db.index.vector.queryNodes('concept_embedding_index', 10, $embedding)
YIELD node, score WHERE score >= 0.6
RETURN node.name AS concept, toFloat(score) AS score ORDER BY score DESC;
```

### 24.3 Semantische Relationen

```cypher
-- IS_A-Taxonomie
MATCH (sub:Concept)-[r:SEMANTIC_RELATION {relation_type: 'is_a'}]->(obj:Concept)
RETURN sub.name, obj.name, r.confidence ORDER BY r.confidence DESC;

-- Ko-Okkurrenz-Netzwerk (stark korreliert)
MATCH (c1:Concept)-[r:CO_OCCURS_WITH]-(c2:Concept)
WHERE r.strength > 0.7
RETURN c1.name, c2.name, r.strength, r.count ORDER BY r.strength DESC LIMIT 20;
```

### 24.4 Graph-Visualisierung (GUI Preset)

```cypher
MATCH p1 = (t:Topic {name: $topic})-[:HAS_CONCEPT]->(c:Concept)
OPTIONAL MATCH p2 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
OPTIONAL MATCH p3 = (paper)-[:HAS_FIGURE]->(fig:Figure)
OPTIONAL MATCH p4 = (c)-[:SEMANTIC_RELATION]->(c2:Concept)
RETURN p1, p2, p3, p4 LIMIT 500;
```

---

## 25. Evolutionsgeschichte & Designentscheidungen

### 25.1 Chronologie der Architekturänderungen

| Phase | Beschreibung | Problem | Lösung |
|-------|-------------|---------|--------|
| v1 | Reines Vektor-RAG | Keine strukturelle Wissensrepräsentation | Graph-Integration |
| v2 | MENTIONS-basiertes GraphRAG | Ingest-Timeouts, 82% Paragraphen ohne Links | Vektorbasiertes Retrieval |
| v3 | Vektorbasiertes GraphRAG (kein Linking beim Ingest) | Leerer Graph, keine MENTIONS/Relationen | Hybrid-Extraktion reaktiviert |
| v4 (aktuell) | Vollständiges GraphRAG: Vektor + aktives Linking | — | NER+LLM+Relationen beim Ingest |

### 25.2 Zentrale Designentscheidungen

**1. Hybride NER: SciSpacy + spaCy**
- `en_core_sci_sm` ist für wissenschaftliche Texte optimiert
- `en_core_web_sm` als Fallback; beide lazy-geladen

**2. Deterministisches Paragraph-Linking**
- Substring-Matching statt LLM → immer reproduzierbar
- Feste confidence=0.75 → kein Quality-Filter nötig
- O(n_paragraphs × n_concepts) statt O(n×LLM-Aufrufe)

**3. Trennung semantischer Relationstypen**
- `co_occurs_with` → `CO_OCCURS_WITH` (ungerichtet, akkumulierend)
- Alle anderen → `SEMANTIC_RELATION` mit `relation_type`

**4. Echtzeit-Fortschritt via _sub(base, span)**
- Jede langsame Sub-Funktion bekommt einen skalierten Callback
- Terminal: tqdm; GUI: callback

**5. chat.completions.create statt responses.create**
- `responses.create` erfordert SDK ≥ 1.23.0, kann silent `None` zurückgeben
- `chat.completions.create` ist SDK-versionsunabhängig

**6. Drei Vektorindizes (3072-D)**
- Separate Indizes für Paragraphen, Figures, Konzepte
- Concept-Embedding-Mittelung = semantisches Zentroid

**7. LM Studio Integration (feature/local-lmstudio-support)**
- `LLM_MODE=local` leitet alle LLM-Calls an lokalen Server um
- Embedding-Dimensionalität muss mit Neo4j-Index übereinstimmen
- RAGAS-Evaluation vergleicht Cloud vs. Lokal quantitativ

**8. Web-Fallback-Kaskade**
- AUTO: Graph → bei < 3 validen Belegen → Web (OpenAI)
- FORCE/OFF: direkte Weiche
- SERP-Provider: nur Stub, nicht produktiv nutzbar

---

## 26. Bekannte Fehler & offene Punkte

### 26.1 Sicherheit

| # | Problem | Status | Priorität |
|---|---------|--------|-----------|
| S1 | `.env` ist in Git-Tracking (trotz `.gitignore`) — lokale Secrets versioniert | Aufmerksam machen | HOCH |
| S2 | `.env.example` enthielt echte Credentials (Neo4j + OpenAI) — bereits in Git-Historie | **Credentials SOFORT rotieren** | KRITISCH |

**Empfohlene Maßnahmen für S2:**
1. Neo4j-Passwort in AuraDB zurücksetzen
2. OpenAI API Key in der OpenAI Console invalidieren und neu erstellen
3. Optional: Git-Historie bereinigen via `git filter-repo` oder BFG Repo Cleaner

### 26.2 Funktionale Mängel

| # | Problem | Betroffenes Modul | Auswirkung |
|---|---------|------------------|-----------|
| F1 | SERP-Websuche ist nur Stub | `src/agent.py:97` | Silent Fail wenn `WEB_SEARCH_PROVIDER=serp` |
| F2 | neo.py Batch-Kommentar sagt "1536-dim" → tatsächlich 3072-D (schema.cypher) | `src/neo.py:117` | Nur falscher Kommentar, kein Laufzeitfehler |
| F3 | spaCy-Ladefehler gibt nur Warning, liefert leeres Entity-Dict | `src/entity_relation_extract.py` | Silent Fail bei NER |
| F4 | Embedding-Dimensionen inkonsistent bei LLM_MODE=local (768 D) vs. Neo4j-Index (3072 D) | `src/config.py`, `src/graph_schema.cypher` | RuntimeError bei Vektorsuche wenn Dimcount falsch |

### 26.3 Architekturelle Limitierungen

1. **Chunk-Kohärenz**: Feste Chunk-Größe (1200 Zeichen) ignoriert semantische Grenzen
2. **Mehrsprachigkeit**: Prompts auf Deutsch, NER-Modelle auf Englisch → gemischte Ergebnisse bei deutschen PDFs
3. **Substring-Matching-Grenzen**: Morphologische Varianten (Plural, Kasus) nicht erkannt
4. **Kein Konversationsgedächtnis**: Kein Sitzungsgedächtnis im Chat-Interface
5. **Embedding-Konsistenz**: Modellwechsel invalidiert alle bestehenden Embeddings
6. **Kursgenerator als Plugin**: Tab 3 bricht stumm ab wenn `course_generator.py` fehlerhaft

### 26.4 Experimentelles (kursgenerierung/)

Der Ordner `kursgenerierung/` enthält experimentelle Subprojekte (unversioniert):
- **Bilderkennung/**: Bilderkennungs-Prototypen (`bilderkennung.py`, `bildkontextuierung.py`)
- **hybrid_test/**: LCRAG, RAG mit sentence_transformers, Faiss
- **knowledge_graph/**: KG-Pipeline und Graph-Retrieval-Experimente
- **ragGraph/**: Graph-basierte RAG-Experimente

Diese Module sind **nicht in die Hauptpipeline integriert** und laufen unabhängig.

---

*Ende der Codebase-Dokumentation*  
*Generiert für: Masterthesis-Ausarbeitung*  
*Stand: Mai 2026 | Umfang: ~19 Quelldateien in src/, ~17 Skripte, ~6.500+ Zeilen produktiver Code*
