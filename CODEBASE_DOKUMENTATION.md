# Vollständige Codebase-Dokumentation: GraphRAG-System für automatisierte Kursgenerierung
> Masterthesis-Kontextdokument · Stand: April 2026  
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
15. [Skripte](#15-skripte)
16. [RAGAS Evaluation Framework](#16-ragas-evaluation-framework)
17. [Teststrategie & Testcode](#17-teststrategie--testcode)
18. [Retrieval-Architektur: Detailbeschreibung](#18-retrieval-architektur-detailbeschreibung)
19. [Pipeline-Abläufe (Sequenzdiagramme)](#19-pipeline-abläufe-sequenzdiagramme)
20. [Datenbankabfragen (Cypher)](#20-datenbankabfragen-cypher)
21. [Evolutionsgeschichte & Designentscheidungen](#21-evolutionsgeschichte--designentscheidungen)

---

## 1. Systemüberblick & Forschungskontext

### 1.1 Gegenstand

Das vorliegende System implementiert ein **GraphRAG-Hybridsystem** (Graph-augmented Retrieval-Augmented Generation) zur automatisierten Extraktion, Strukturierung und didaktischen Aufbereitung wissenschaftlicher Literatur. Ziel ist die Generierung von Schulungskursen aus einer Menge wissenschaftlicher PDFs, die in einer Neo4j-Wissensgraphdatenbank vorgehalten werden.

Das System kombiniert:
- **Graph-basierte Wissensrepräsentation** (Neo4j AuraDB) mit expliziter Dokumenthierarchie
- **Vektor-basiertes semantisches Retrieval** (OpenAI `text-embedding-3-large`, 3072 Dimensionen)
- **Hybride Konzeptextraktion** (NER via spaCy/SciSpacy + LLM via GPT-4o-mini) mit semantischer Deduplizierung
- **Semantische Relationsextraktion** (LLM-Triplets + statistisches Ko-Okkurrenz-Tracking)
- **Deterministische Paragraph-Konzept-Verlinkung** (Substring-Matching zur Ingest-Zeit)
- **Didaktische Antwortgenerierung** mit Quellenbelegen und automatischer Zitationsvalidierung
- **Präsentations- und Videogenerierung** via externe APIs (Gamma, Synthesia)

### 1.2 Wissenschaftliche Relevanz

RAG-Systeme (Lewis et al., 2020) verbinden parametrisches Wissen großer Sprachmodelle mit nicht-parametrischem Dokumentwissen. GraphRAG (Edge et al., 2024) erweitert diesen Ansatz durch explizite Wissensgrafen, die semantische Beziehungen zwischen Entitäten repräsentieren und komplexere Retrievalstrategien ermöglichen.

Die vorliegende Implementierung adressiert dabei spezifisch:
1. **Skalierbarkeit**: Batch-Ingest mit automatischer Duplikaterkennung
2. **Reliabilität**: fehlertolerante JSON-Verarbeitung von LLM-Ausgaben
3. **Didaktische Qualität**: rollenbasierte Personalisierung und Zitatvalidierung
4. **Vollständige Graph-Nutzung**: MENTIONS-Kanten, SEMANTIC_RELATION und CO_OCCURS_WITH werden aktiv beim Ingest befüllt

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
│  Gamma API (Präsentationsgenerierung)                   │
│  Synthesia API (KI-Videogenerierung)                    │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Graphschema (konzeptuell)

```
(Topic)-[:HAS_CONCEPT]->(Concept)-[:SEMANTIC_RELATION {relation_type}]->(Concept)
                                 -[:CO_OCCURS_WITH {count, strength}]-(Concept)
                                 ↑[:MENTIONS {confidence}]
(Paper)-[:HAS_SECTION]->(Section)-[:HAS_PARAGRAPH]->(Paragraph)
       |-[:HAS_FIGURE]-->(Figure)<-[:CAPTIONS|REFERS_TO|NEAR]-(Paragraph)
       |-[:ABOUT {weight}]------>(Concept)
```

**Knotentypen:**
| Knoten | Schlüsseleigenschaften |
|--------|----------------------|
| `Paper` | `paper_id`, `title`, `doi`, `url`, `file_sha256` |
| `Section` | `section_id`, `title`, `level`, `page_start`, `page_end` |
| `Paragraph` | `paragraph_id`, `text`, `page`, `bbox`, `embedding` (3072-D) |
| `Figure` | `figure_id`, `caption`, `image_uri`, `figure_label`, `figure_type`, `entities`, `ocr_hints`, `embedding` (3072-D) |
| `Concept` | `concept_id`, `name`, `alt_labels`, `description`, `type`, `source`, `embedding` (3072-D) |
| `Topic` | `name` |

**Kantentypen:**
| Kante | Von → Nach | Eigenschaften |
|-------|-----------|--------------|
| `HAS_SECTION` | Paper → Section | — |
| `HAS_PARAGRAPH` | Paper/Section → Paragraph | — |
| `HAS_FIGURE` | Paper/Section → Figure | — |
| `MENTIONS` | Paragraph → Concept | `confidence` (0.75 bei Substring-Match) |
| `HAS_CONCEPT` | Topic → Concept | — |
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
openai>=1.51.0          # OpenAI API (Embeddings, Chat, Vision)
fastapi>=0.111.0        # REST-API (optional)
uvicorn>=0.30.0         # ASGI-Server
pillow>=10.3.0          # Bildverarbeitung
imagehash>=4.3.1        # Perceptual Hashing für Duplikaterkennung
pydantic>=2.8.2         # Datenvalidierung
tqdm>=4.66.4            # Fortschrittsanzeige (Terminal)
duckduckgo-search>=6.2.9 # Web-Fallback-Suche
reportlab>=3.6.12       # PDF-Export
streamlit>=1.37.0       # Web-GUI
pyvis>=0.3.2            # Graph-Visualisierung
plotly>=5.18.0          # Interaktive Diagramme
networkx>=3.2.1         # Graph-Algorithmen
scipy>=1.11.0           # Wissenschaftliche Berechnungen
python-pptx>=0.6.21     # PowerPoint-Generierung (Fallback)
httpx>=0.24.1           # HTTP-Client
spacy>=3.7.0            # NLP / Named Entity Recognition
scispacy>=0.5.4         # Wissenschaftliches NER
scikit-learn>=1.3.0     # Machine-Learning-Utilities
numpy>=1.26.0           # Vektor-Arithmetik (Embedding-Mittelung)
ragas>=0.4.3            # Evaluation-Metriken für RAG-Systeme
datasets>=4.8.5         # HuggingFace Dataset-Format (von RAGAS benötigt)
```

### 3.2 spaCy-Modelle (installiert)

```bash
# Allgemeines NLP (Fallback)
python -m spacy download en_core_web_sm          # v3.7.1, 12.8 MB

# Wissenschaftliches NLP (bevorzugt für wissenschaftliche PDFs)
pip install https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz
```

Beide Modelle werden **lazy-geladen** (erst beim ersten NER-Aufruf). `en_core_sci_sm` wird bevorzugt; wenn nicht verfügbar, Fallback auf `en_core_web_sm`.

### 3.3 Externe Dienste

| Dienst | Zweck | Modell / Endpunkt |
|--------|-------|------------------|
| OpenAI Embeddings | Vektorisierung von Text | `text-embedding-3-large` (3072-D) |
| OpenAI Chat | Konzeptextraktion, Antwortgenerierung, Relationen | `gpt-4o-mini` |
| OpenAI Vision | Bildbeschreibung | `gpt-4o-mini` (multimodal) |
| OpenAI Web Search | Web-Fallback | `responses.create` mit `web_search`-Tool |
| Neo4j AuraDB | Persistenz, Vektorindizes | Neo4j ≥ 5.19 |
| Gamma API | Präsentationsgenerierung | REST-API |
| Synthesia API | KI-Videogenerierung | `https://api.synthesia.io/v1` |
| spaCy | Standardisiertes NER | `en_core_web_sm` |
| SciSpacy | Wissenschaftliches NER | `en_core_sci_sm` |
| RAGAS | Evaluation (Faithfulness, Relevanz, Precision, Recall) | `ragas>=0.4.3` + `datasets>=4.8.5` |

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

**Hinweis:** Der `data/`-Ordner (inklusive `data/images/`) ist in `.gitignore` eingetragen und wird nicht versioniert.

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
def extract_doi_and_url(doc) -> tuple[str|None, str|None]:
    """Zweistufige DOI/URL-Extraktion: PDF-Metadaten → Regex auf ersten 2 Seiten."""

def extract_sections(doc) -> list[dict]:
    """TOC-Extraktion; berechnet Seitenbereiche (page_start, page_end) pro Section."""

def find_caption_for_image(image_bbox, text_blocks, y_tolerance=50, x_tolerance=20):
    """
    Caption-Suche: Textblöcke unterhalb des Bildes, sortiert nach Abstand.
    Extrahiert figure_label via Regex: "Figure 1:", "Fig. 2.3:", "Abb. 5"
    """

def extract_paragraph_blocks(page) -> list[dict]:
    """
    Robuste Textblock-Extraktion via page.get_text('rawdict').
    Fallback: Plain-Text-Chunking (chunk_size=1200, overlap=150)
    """
```

### 6.3 Embed-Funktionen mit Fortschritts-Callback

```python
def embed_paragraphs(
    paragraphs: List[Dict[str, Any]],
    progress_fn=None,           # Neu: progress_fn(label: str, pct: int)
) -> List[Dict[str, Any]]:
    """
    Batch-Embedding aller Paragraphen via text-embedding-3-large.
    
    progress_fn: Wird bei jedem Paragraphen aufgerufen mit:
      label = "Paragraph einbetten {i+1}/{n}"
      pct   = 0–100 (relativ zur Gesamtzahl der Paragraphen)
    Wenn progress_fn=None: tqdm-Fortschrittsbalken im Terminal.
    """

def analyze_and_embed_figures(
    figures: List[Dict[str, Any]],
    progress_fn=None,           # Neu: progress_fn(label: str, pct: int)
) -> List[Dict[str, Any]]:
    """
    Für jede Figur:
    1. describe_image(path) via GPT-4o-mini Vision
       → caption, figure_type, entities, ocr_hints
    2. Reiches Embedding: caption + figure_type + entities + ocr_hints + section_title + label
    
    Rückgabe je Figur: figure_id, page, image_uri, image_filename,
                       caption, figure_type, entities, ocr_hints,
                       analysis_json, embedding
    
    progress_fn: Wird bei jeder Abbildung aufgerufen mit:
      label = "Abbildung analysieren {i+1}/{n}"
      pct   = 0–100
    Wenn progress_fn=None: tqdm-Fortschrittsbalken im Terminal.
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
    Verknüpft Figures mit Concepts basierend auf den extrahierten entities.
    Erstellt: (Figure)-[:MENTIONS {confidence: 0.8, source: 'vision_analysis'}]->(Concept)
    Matching: case-insensitiver Name-Vergleich.
    """
```

### 7.6 Semantische Relationen

```python
def add_semantic_relations(self, paper_id: str, relations: list[dict]) -> dict:
    """
    Schreibt semantische Relationen als SEMANTIC_RELATION-Kanten.
    
    relations-Format: [{subject, predicate, object, confidence, context, source}]
    
    Erstellt beide Concept-Knoten via MERGE falls noch nicht vorhanden.
    MERGE auf (subject)-[:SEMANTIC_RELATION {relation_type: predicate}]->(object)
    SET: confidence, context, source, paper_id, updated_at
    
    Returns: {created, updated, skipped}
    """

def add_cooccurrence_relations(self, paper_id: str, cooccurrences: list[dict]) -> dict:
    """
    Schreibt Ko-Okkurrenz-Relationen als CO_OCCURS_WITH-Kanten (ungerichtet).
    
    cooccurrences-Format: [{concept1, concept2, count, strength}]
    
    SET rel.count akkumuliert (bestehende + neue Zählungen).
    Returns: {created, updated}
    """
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
    """Batch-Embedding via text-embedding-3-large."""

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
    
    Hinweis: Verwendet chat.completions.create (statt responses.create) für
    Kompatibilität mit allen openai SDK-Versionen.
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
    6. Optional: persist_to_topic
    
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
    progress_fn=None,           # Neu: Fortschritts-Callback
) -> Dict[str, Any]:
    """
    Vollständige Hybrid-Pipeline (NER + LLM + Relationen):

    Step 1 — extract_entities_and_relations(paper_text, paragraphs, ...):
      a. NER via SciSpacy (en_core_sci_sm) bevorzugt, Fallback: spaCy (en_core_web_sm)
         Relevante Typen: SCIENTIFIC_TERM, CHEMICAL, DISEASE, ORG
         Ausgeschlossen: PERSON, DATE, GPE
      b. LLM-Konzeptextraktion für abstrakte Konzepte (methodology, theory, technique, ...)
      c. Embeddings für alle extrahierten Entitäten
      d. Semantische Triplet-Extraktion via LLM (is_a, part_of, uses, causes, ...)
      e. Ko-Okkurrenz-Analyse (Fenster: 50 Zeichen, min. 2 Vorkommen)

    Step 2 — Konvertierung zu Concept-Dicts:
      {concept_id (slug), name, type, source, description, alt_labels, embedding}
      Deduplizierung via slug-Set.

    Step 3 — Paragraph → Concept Links (deterministisch):
      Substring-Matching: cname_lower in paragraph_text.lower()
      Mindestlänge: 3 Zeichen (verhindert Falsch-Positive)
      confidence: 0.75 (einheitlich für alle Text-Matches)

    Step 4 — Optional: Persistenz in Neo4j
      neo_client.upsert_topic(topic_hint)
      neo_client.add_concepts(topic_hint, concepts)

    progress_fn-Aufrufe:
      0%   → "NER + LLM Extraktion läuft …"
      40%  → "{n_entities} Entitäten, {n_relations} Relationen extrahiert …"
      60%  → "{n_concepts} Konzepte aufgebaut – Paragraph-Links erstellen …"
      80%  → "{n_links} Para-Links erstellt – in Neo4j schreiben …"
      100% → "Konzeptextraktion fertig: {n} Konzepte, {n} Links, {n} Relationen"

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
    Text-Truncation: max. 100.000 Zeichen.
    Deduplizierung der Ergebnisse.
    """

def extract_concepts_llm(text, max_concepts=15, existing_entities=None) -> list:
    """
    LLM-Konzeptextraktion für abstrakte Konzepte.
    Typen: methodology | theory | technique | domain_term | abstract_concept
    JSON-Mode (response_format={"type": "json_object"}), temperature=0.3
    Text-Truncation: max. 4000 Zeichen.
    Informiert LLM über bereits gefundene NER-Entitäten (Duplikatvermeidung).
    """

def extract_entities_hybrid(text, max_llm_concepts=15, use_scispacy=True) -> dict:
    """
    Fusion: NER + LLM, nur relevante NER-Typen (SCIENTIFIC_TERM, CHEMICAL, DISEASE, ORG).
    Validierung: min. 3 Zeichen, keine reinen Zahlen.
    Deduplizierung (case-insensitive).
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
    JSON-Mode, temperature=0.3
    """

def extract_cooccurrence_relations(entities, paragraphs, window_size=50) -> list:
    """
    Statistisches Ko-Okkurrenz-Tracking:
    - Fenster: 50 Zeichen
    - Minimum: 2 gemeinsame Vorkommen
    - confidence: min(0.9, 0.5 + count × 0.1)
    - predicate: "co_occurs_with"
    - Kanonische Paare: alphabetisch sortiert
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
    2. _embed() auf Entity-Namen         → 3072-D Embeddings
    3. extract_relations_llm()           → Semantische Triplets
    4. extract_cooccurrence_relations()  → Statistische Relationen (optional)
    5. Deduplizierung der Relationen
    
    Returns: {entities, relations, stats}
    """
```

---

## 10. Kernmodul: Retrieval (src/retriever.py)

### 10.1 Vektorsuchfunktionen

```python
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")  # 3072-D

def _vsearch_paragraphs(neo, embedding, k=24) -> list:
    """
    Suche auf 'paragraph_embedding_index'.
    Liefert: paragraph_id, text, page, paper_title, doi, url, authors, year,
             section_id, section_title, score
    """

def _vsearch_figures(neo, embedding, k=8) -> list:
    """
    Suche auf 'figure_embedding_index'.
    Liefert: figure_id, caption, figure_label, page, image_uri, image_filename,
             figure_type, entities, ocr_hints, analysis_json, paper_title, score
    """

def _vsearch_concepts(neo, embedding, k=10, min_score=0.6) -> list:
    """
    Suche auf 'concept_embedding_index'. Filter: score >= min_score.
    min_score=0.6: Konzeptnamen sind kurz → geringere Ähnlichkeitswerte als Paragraphen.
    """
```

### 10.2 Konzept-zu-Paragraph Überbrückung

```python
def _paragraphs_via_concepts(neo, concept_ids, limit=20) -> list:
    """
    Vektorbasierter Ansatz (keine MENTIONS-Traversierung nötig):
    1. Concept-Embeddings aus Neo4j abrufen
    2. numpy.mean(embeddings, axis=0) → semantisches Zentroid
    3. Vektorsuche auf paragraph_embedding_index mit Durchschnittsvektor
    
    Findet semantisch ähnliche Paragraphen auch ohne exakte Namensübereinstimmung.
    """

def _expand_via_semantic_relations(neo, concept_ids, k=5) -> list:
    """
    Erweitert Konzeptliste über SEMANTIC_RELATION-Kanten.
    Erlaubte Typen: IS_A, PART_OF, RELATED_TO
    """

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

    FORCE → Websuche sofort (Graph übersprungen)
    OFF   → Nur Graph → grounded_answer()
    AUTO  → Graph → _effective_supports() zählt valide Belege
              eff >= min_supports → Antwort aus Graph (mode: "graph")
              eff <  min_supports → Web-Fallback (mode: "web")

    Zitationsvalidierung: validate_citations(answer, supports)
    Returns: {mode, answer, supports, debug, citation_validation}
    """
```

---

## 12. Kernmodul: OpenAI-Client (src/openai_client.py)

```python
def embed_text(text: str, model="text-embedding-3-large") -> List[float]:
    """Einzeltext-Embedding. Lazy-initialisierter Singleton-Client."""

def describe_image(path: str) -> dict:
    """
    GPT-4o-mini Vision. Base64-Encoding. Kein Emoji (Regex-Filter).
    Output: {caption, figure_type, entities, ocr_hints}
    """

def grounded_answer(query: str, supports: List[dict]) -> str:
    """
    Didaktische Antwort mit Quellenbelegen. Rolle: "Erfahrener Dozent".
    Struktur: Definition → Erklärung → Beispiele → Zusammenfassung.
    Zitationsformat: [Pxxx] / [Fxxx] direkt im Fließtext (keine Metadaten in Klammern).
    Figure-Anreicherung: Typ, Schlüsselbegriffe, erkannter Text im Kontext.
    Post-Processing: Zitationen nach Listenmarkierungen ans Zeilenende verschieben.
    """
```

---

## 13. Kernmodul: Zitationsvalidierung (src/citation_validator.py)

```python
def validate_citations(generated_text, supports, similarity_threshold=0.65) -> dict:
    """
    Semantische Zitationsvalidierung via Kosinus-Ähnlichkeit (OpenAI-Embeddings).
    
    Formate:
    - Numerisch [1] [2]: Nur Vorhandenseins-Check (keine semantische Prüfung)
    - ID-basiert [Pxxx] [Fxxx]: Ähnlichkeit von Zitationskontext vs. Quelltext
      status: "valid" (≥ threshold) | "warning" | "invalid"
    
    Returns: {total_citations, valid_count, invalid_count, warning_count, details}
    """
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

def validate_ingestion_quality(report: dict) -> dict:
    """
    Qualitätsprüfung nach Ingest:
    - n_concepts == 0           → Issue: "Keine Konzepte extrahiert"
    - n_links == 0 bei n_concepts > 0 → Issue: "Konzepte nicht mit Paragraphen verknüpft"
    - n_figures == 0 bei n_paragraphs > 10 → Warning
    
    Returns: {quality_score (0–100), quality_label, issues, warnings, recommendations}
    """

def extract_enhanced_metadata(pdf_path, paper_meta) -> dict:
    """Ergänzt: file_size, ingested_at, pdf_author/subject/keywords, page_count."""
```

**Hinweis:** `filter_low_quality_concepts` wird im GUI-Ingest-Flow nicht mehr aufgerufen, da `extract_and_embed_concepts_hybrid` deterministische Konfidenz (0.75) verwendet und kein LLM-basiertes Link-Scoring mehr nötig ist.

---

## 15. Skripte

### 15.1 scripts/ingest.py

CLI-Einstiegspunkt für PDF-Ingest. Vollständige Pipeline je PDF:

```
1. read_pdf_text_and_images()          → paper_meta, sections, paragraphs, figures
2. neo.upsert_paper()                  → Paper-Knoten
3. neo.add_sections()
4. embed_paragraphs()                  → Embeddings
5. neo.add_paragraphs()
6. analyze_and_embed_figures()         → VLM-Analyse + Embeddings
7. neo.add_figures()
8. neo.link_figures_to_concepts()      → Figure→Concept MENTIONS
9. extract_and_embed_concepts_hybrid() → NER + LLM + Relationen
   ├─ neo.link_paragraphs_to_concepts()  → MENTIONS-Kanten
   ├─ neo.add_semantic_relations()       → SEMANTIC_RELATION-Kanten
   └─ neo.add_cooccurrence_relations()   → CO_OCCURS_WITH-Kanten
10. neo.stitch_document_hierarchy()
11. neo.stitch_figures_to_paragraphs()
```

Semantic Relations werden getrennt geschrieben:
```python
cooc_rels = [r for r in relations if r.get("predicate") == "co_occurs_with"]
sem_rels  = [r for r in relations if r.get("predicate") != "co_occurs_with"]
neo.add_semantic_relations(paper_id, sem_rels)
neo.add_cooccurrence_relations(paper_id, cooc_fmt)
```

### 15.2 scripts/gui_app.py

Streamlit-GUI mit vollständigem Echtzeit-Fortschritt:

**Tab-Struktur:**
| Tab | Funktion |
|-----|---------|
| Ingest | PDF-Upload, Konzeptextraktion, Duplikaterkennung |
| Kurs | Interaktive Kursgenerierung mit Kapiteln/Lernzielen |
| Export | PDF, Präsentation (Gamma), Video (Synthesia) |
| Graph | Graphvisualisierung mit Cypher-Queries |
| Diagnose | Datenbankstatistiken, Debug-Informationen |
| Evaluation | RAGAS-Metriken, Halluzinationstest, Cloud-vs.-Lokal-Vergleich |

**Fortschritts-Architektur (ingest_one_pdf_enhanced):**

```python
# Drei Ebenen der Fortschrittsanzeige:
paper_progress_bar = st.progress(0)    # Gesamtfortschritt 0–100%
paper_status       = st.empty()        # Aktueller Schritt-Text
paper_percent      = st.empty()        # Prozentzahl als Metric
paper_detail       = st.empty()        # Granularer Sub-Step (caption)

def update_paper_progress(step: str, pct: int):
    """Callback der von allen Sub-Funktionen aufgerufen wird."""
    paper_progress_bar.progress(pct / 100)
    paper_percent.metric("Paper-Fortschritt", f"{pct}%")
    paper_status.text(f"{filename} — {step}")
    paper_detail.caption(f"↳ {step}")

def _sub(base: int, span: int):
    """Mappt internen [0, 100] auf absoluten [base, base+span]."""
    def fn(label, pct):
        report_progress(label, min(100, base + int(pct * span / 100)))
    return fn if progress_callback else None

# Prozentuale Zuordnung der Sub-Bereiche:
embed_paragraphs(...,          progress_fn=_sub(40, 10))  # 40–50%
analyze_and_embed_figures(..., progress_fn=_sub(60, 10))  # 60–70%
extract_and_embed_concepts_hybrid(..., progress_fn=_sub(70, 20))  # 70–90%
```

**Sichtbares Ergebnis:** Der Fortschrittsbalken bewegt sich kontinuierlich (z.B. "Paragraph einbetten 23/67 — 43%") statt in großen Sprüngen. Alle Sub-Schritte aus Terminal werden in der GUI sichtbar.

**GUI nutzt `extract_and_embed_concepts_hybrid`** (identisch mit CLI, inkl. semantischer Relationen):
```python
extraction_result = extract_and_embed_concepts_hybrid(...)
# → neo.link_paragraphs_to_concepts()
# → neo.add_semantic_relations()
# → neo.add_cooccurrence_relations()
```

### 15.3 scripts/ask.py

```
CLI: python scripts/ask.py "Erkläre den Unterschied zwischen supervised und unsupervised learning."
→ Antwort + Belegquellen auf der Konsole
```

---

## 16. RAGAS Evaluation Framework

### 16.1 Überblick

`scripts/ragas_eval.py` implementiert die quantitative Qualitätsmessung der GraphRAG-Pipeline mit dem [RAGAS](https://docs.ragas.io)-Framework. Das Skript wird über den **Evaluation-Tab der Streamlit-GUI** ausgeführt und schreibt Ergebnisse nach `data/eval/ragas_results.json`.

**Abhängigkeiten:**
```
ragas>=0.4.3
datasets>=4.8.5
langchain-openai  (ChatOpenAI + OpenAIEmbeddings als RAGAS-Backend)
```

### 16.2 Metriken

| Metrik | Was wird gemessen | Wertebereich |
|--------|------------------|--------------|
| **Faithfulness** | Sind alle Aussagen in der Antwort durch den Kontext belegt? | 0–1 (höher = besser) |
| **Answer Relevancy** | Beantwortet die Antwort tatsächlich die gestellte Frage? | 0–1 |
| **Context Precision** | Wie präzise ist der abgerufene Kontext (wenig Rauschen)? | 0–1 |
| **Context Recall** | Enthält der abgerufene Kontext alle nötigen Informationen? | 0–1 |

LLM- und Embedding-Backend für RAGAS: `gpt-4o-mini` + `text-embedding-3-large` (identisch mit der Produktiv-Pipeline).

### 16.3 Testdatensatz (DEFAULT_TEST_QUESTIONS)

15 vordefinierte Fragen in vier Kategorien:

| Typ | Anzahl | Zweck |
|-----|--------|-------|
| `factual` | 5 | Einzelne Faktenfragen (Transformer, Self-Attention, RAG, Knowledge Graph, NER) |
| `cross_topic` | 5 | Themenübergreifende Fragen (GraphRAG vs. RAG, Embeddings in Graphen, …) |
| `visual` | 3 | Fragen zu Abbildungen (Transformer-Architektur, RAG-Pipeline, Attention-Matrix) |
| `false_context` | 2 | Halluzinationstest-Kandidaten (BERT vs. GPT, GPT-Funktionsweise) |

### 16.4 Drei Evaluationsläufe

#### run_ragas_evaluation()
Vollständige RAGAS-Evaluation über alle 15 Testfragen mit allen vier Metriken. Retrieval via `concept_based_retrieve()`, Generierung via `grounded_answer()`.

#### run_hallucination_test()
Vergleicht Faithfulness mit **echtem** vs. **bewusst falschem** Kontext (`_FALSE_FACTS`). Die Differenz ist das Maß der Halluzinationsanfälligkeit:

```python
"interpretation": (
    "hoch"   if differenz > 0.3
    else "mittel" if differenz > 0.1
    else "niedrig"
)
```

#### run_llm_comparison()
Vergleicht **GPT-4o-mini (Cloud)** mit **LM Studio (Lokal)** auf denselben 5 Fragen:
- Retrieval läuft immer im Cloud-Modus (OpenAI 3072-D Embeddings)
- Nur die Generierungsphase (`grounded_answer`) wird per `cfg.LLM_MODE` umgeschaltet
- Ausgabe je Modus: RAGAS-Scores (Faithfulness + Answer Relevancy), Inferenzzeit, geschätzte Kosten (USD)

```python
# Kostenschätzung GPT-4o-mini (Stand 2025)
_GPT4O_MINI_INPUT_PER_1K_USD  = 0.000150
_GPT4O_MINI_OUTPUT_PER_1K_USD = 0.000600
_CHARS_PER_TOKEN = 4
```

### 16.5 Ausgabe

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
    "cloud": { "modell": "gpt-4o-mini", "ragas_scores": {...}, "inferenz_zeit_sek": 42.1, "geschaetzte_kosten_usd": 0.00031 },
    "local": { "modell": "llama-3-...", "ragas_scores": {...}, "inferenz_zeit_sek": 118.4, "geschaetzte_kosten_usd": "n/a (lokal)" }
  }
}
```

---

## 17. Teststrategie & Testcode

### 16.1 Testphilosophie

Die Testsuite (`tests/test_concept_extract.py`) folgt:
- **Vollständige API-Isolation**: Kein OpenAI-, kein Neo4j-Aufruf
- **AAA-Muster** (Arrange – Act – Assert)
- **Deterministische Stubs** mit vorhersehbarem Verhalten
- **Grenzwerttests** via `@pytest.mark.parametrize`

### 16.2 Vollständiger Testcode

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

def _llm_stub(concepts, links):
    def fake_llm(title, paragraphs, topic_hint, max_concepts, seed_names, allow_new):
        return {"concepts": concepts, "links": links}
    return fake_llm

@pytest.fixture
def fake_neo(): return FakeNeo()

@pytest.fixture
def sample_paragraphs():
    return [
        {"paragraph_id": "p1", "text": "Text über Existing Concept."},
        {"paragraph_id": "p2", "text": "Text über New Interesting."},
        {"paragraph_id": "p3", "text": "Weiterer Text zu New Interesting."},
    ]

@pytest.fixture(autouse=True)
def mock_embed(monkeypatch):
    def fake_embed(texts):
        return [[1.0] if "existing" in t.lower() else [0.0] for t in texts]
    monkeypatch.setattr(ce, '_embed', fake_embed)

# --- Deduplizierungs-Tests ---

def test_dedupe_maps_to_existing(monkeypatch, fake_neo, sample_paragraphs):
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[{"name": "Existing Concept", "alt_labels": [], "description": "d"},
                  {"name": "New Interesting",  "alt_labels": [], "description": "d2"}],
        links=[{"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9},
               {"paragraph_id": "p2", "concept_name": "New Interesting",  "confidence": 0.8}],
    ))
    concepts, links = ce.extract_and_embed_concepts(
        paper_title="T", paragraphs=sample_paragraphs,
        neo_client=fake_neo, dedupe_threshold=0.9, min_confidence=0.0,
    )
    p1_links = [l for l in links if l["paragraph_id"] == "p1"]
    assert p1_links and all(l["concept_id"] == "existing-1" for l in p1_links)

@pytest.mark.parametrize("threshold,expect_mapped", [(0.90, True), (0.99, False)])
def test_dedupe_threshold_boundary(monkeypatch, fake_neo, sample_paragraphs,
                                   threshold, expect_mapped):
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[{"name": "Existing Concept", "alt_labels": [], "description": "d"}],
        links=[{"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9}],
    ))
    _, links = ce.extract_and_embed_concepts(
        paper_title="T", paragraphs=sample_paragraphs,
        neo_client=fake_neo, dedupe_threshold=threshold, min_confidence=0.0,
    )
    assert (any(l.get("concept_id") == "existing-1" for l in links)) == expect_mapped

# --- Parsing-Tests ---

def test_parse_valid_json():
    assert ce._try_parse_llm_response('{"concepts":[{"name":"KI"}],"links":[]}')["concepts"]

def test_parse_empty_string():
    assert ce._try_parse_llm_response("") == {"concepts": [], "links": []}

def test_parse_json_with_trailing_comma():
    result = ce._try_parse_llm_response('{"concepts":[{"name":"KI",}],"links":[],}')
    assert isinstance(result, dict) and "concepts" in result
```

---

## 18. Retrieval-Architektur: Detailbeschreibung

### 17.1 Architekturevolution

#### v2: MENTIONS-basiertes GraphRAG (Problem)

```
Ingest: Paper → [O(n×LLM)] → MENTIONS-Kanten → Timeouts
Retrieval: Concept → [:MENTIONS] ← Paragraph
Problem: 82% Paragraphen ohne Links, 15–20% Abdeckung
```

#### v3: Vektorbasiert, kein Linking beim Ingest

```
Ingest: 1 LLM-Aufruf → Concept-Knoten + Embeddings
Retrieval: Query → avg(concept_embeddings) → Vektorsuche Paragraphen
```

#### v4 (aktuell): Vektorbasiert + vollständiges Linking beim Ingest

```
Ingest:    NER + LLM → Concepts + Embeddings
           Substring-Matching → MENTIONS-Kanten (confidence=0.75)
           LLM-Triplets → SEMANTIC_RELATION-Kanten
           Ko-Okkurrenz → CO_OCCURS_WITH-Kanten
           
Retrieval: Query → concept_embedding_index
                 → SEMANTIC_RELATION-Expansion
                 → avg(concept_embeddings) → paragraph_embedding_index
                 + direkte Paragraphen-Vektorsuche
                 + figure_embedding_index
```

**Ergebnisvergleich:**

| Metrik | MENTIONS-basiert (v2) | Vektorbasiert (v3) | Hybrid (v4, aktuell) |
|--------|----------------------|--------------------|----------------------|
| Ingest-Aufwand | O(n×LLM), Timeouts | O(1×LLM) | O(1×LLM+NER) |
| MENTIONS-Abdeckung | 15–20% | 0% | ~Substring-Match-Rate |
| Graph-Relationen | MENTIONS | keine | MENTIONS + SEMANTIC + CO_OCCURS |
| Retrieval-Semantik | String-Matching | Vektor-Ähnlichkeit | Vektor + Graph-Expansion |

### 17.2 Vollständiger Retrieval-Pfad

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

## 19. Pipeline-Abläufe (Sequenzdiagramme)

### 18.1 PDF-Ingest-Pipeline (vollständig)

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

### 18.2 GUI-Fortschritts-Pipeline

```
Ingest-Button geklickt
    │
    ├─ paper_progress_bar (0–100%)
    ├─ paper_status (aktueller Schritt)
    ├─ paper_percent (Metric)
    └─ paper_detail (granularer Sub-Step, caption)

_sub(base, span) → mappt [0,100] → [base, base+span]:
    40–50%: embed_paragraphs    → "Paragraph einbetten 12/45"
    60–70%: analyze_figures     → "Abbildung analysieren 3/7"
    70–90%: extract_hybrid      → "42 Entitäten extrahiert – Konzepte aufbereiten …"
```

---

## 20. Datenbankabfragen (Cypher)

### 19.1 Diagnostik

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

### 19.2 Vektorsuche

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

### 19.3 Semantische Relationen

```cypher
-- IS_A-Taxonomie
MATCH (sub:Concept)-[r:SEMANTIC_RELATION {relation_type: 'is_a'}]->(obj:Concept)
RETURN sub.name, obj.name, r.confidence ORDER BY r.confidence DESC;

-- Ko-Okkurrenz-Netzwerk (stark korreliert)
MATCH (c1:Concept)-[r:CO_OCCURS_WITH]-(c2:Concept)
WHERE r.strength > 0.7
RETURN c1.name, c2.name, r.strength, r.count ORDER BY r.strength DESC LIMIT 20;
```

### 19.4 Graph-Visualisierung (GUI Preset)

```cypher
MATCH p1 = (t:Topic {name: $topic})-[:HAS_CONCEPT]->(c:Concept)
OPTIONAL MATCH p2 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
OPTIONAL MATCH p3 = (paper)-[:HAS_FIGURE]->(fig:Figure)
OPTIONAL MATCH p4 = (c)-[:SEMANTIC_RELATION]->(c2:Concept)
RETURN p1, p2, p3, p4 LIMIT 500;
```

---

## 21. Evolutionsgeschichte & Designentscheidungen

### 20.1 Chronologie der Architekturänderungen

| Phase | Beschreibung | Problem | Lösung |
|-------|-------------|---------|--------|
| v1 | Reines Vektor-RAG | Keine strukturelle Wissensrepräsentation | Graph-Integration |
| v2 | MENTIONS-basiertes GraphRAG | Ingest-Timeouts, 82% Paragraphen ohne Links | Vektorbasiertes Retrieval |
| v3 | Vektorbasiertes GraphRAG (kein Linking beim Ingest) | Leerer Graph, keine MENTIONS/Relationen | Hybrid-Extraktion reaktiviert |
| v4 (aktuell) | Vollständiges GraphRAG: Vektor + aktives Linking | — | NER+LLM+Relationen beim Ingest |

### 20.2 Zentrale Designentscheidungen

**1. Hybride NER: SciSpacy + spaCy**
- `en_core_sci_sm` ist für wissenschaftliche Texte optimiert (erkennt Fachbegriffe)
- `en_core_web_sm` als Fallback für nicht-wissenschaftliche Texte
- Beide lazy-geladen: kein Performance-Overhead bei Import

**2. Deterministisches Paragraph-Linking**
- Substring-Matching statt LLM-Guessing → immer reproduzierbar
- Feste confidence=0.75 → kein Quality-Filter nötig
- O(n_paragraphs × n_concepts) statt O(n×LLM-Aufrufe)

**3. Trennung semantischer Relationstypen**
- `co_occurs_with`-Prädikate → `CO_OCCURS_WITH`-Kanten (ungerichtet, akkumulierend)
- Alle anderen Prädikate → `SEMANTIC_RELATION` mit `relation_type`-Eigenschaft
- Ermöglicht gezielte Queries nach Relationstyp

**4. Echtzeit-Fortschritt in der GUI via _sub(base, span)**
- Jede langsame Sub-Funktion bekommt einen skalierten Callback
- Fortschrittsbalken bewegt sich kontinuierlich statt in Sprüngen
- Terminal: tqdm (wenn `progress_fn=None`); GUI: callback

**5. chat.completions.create statt responses.create**
- `responses.create` (OpenAI Responses API) erfordert SDK ≥ 1.23.0
- `output_text` kann bei Versionsinkompatibilität still `None` zurückgeben
- `chat.completions.create` ist etabliert und SDK-versionsunabhängig

**6. Drei Vektorindizes (3072-D)**
- Separate Indizes für Paragraphen, Figures, Konzepte
- Concept-Embedding-Mittelung = semantisches Zentroid der Anfrage

**7. Web-Fallback-Kaskade**
- AUTO: Graph → bei < 3 validen Belegen → Web
- FORCE/OFF: direkte Weiche
- mode-Feld für Transparenz

### 20.3 Bekannte Limitierungen & offene Punkte

1. **Chunk-Kohärenz**: Feste Chunk-Größe (1200 Zeichen) ignoriert semantische Grenzen
2. **Mehrsprachigkeit**: Prompts auf Deutsch, NER-Modelle auf Englisch → gemischte Ergebnisse bei deutschen PDFs
3. **Substring-Matching-Grenzen**: Morphologische Varianten (Plural, Kasus) werden nicht erkannt
4. **MENTIONS-Vollständigkeit**: Substring-Matching findet nur exakte Namensvorkommen
5. **Embedding-Konsistenz**: Modellwechsel würde alle bestehenden Embeddings invalidieren
6. **Sitzungsgedächtnis**: Kein Konversationsgedächtnis im Chat-Interface

---

*Ende der Codebase-Dokumentation*  
*Generiert für: Masterthesis-Ausarbeitung*  
*Stand: April 2026 | Umfang: ~18 Quelldateien, ~6.000+ Zeilen produktiver Code*
