# Vollständige Codebase-Dokumentation: GraphRAG-System für automatisierte Kursgenerierung
> Masterthesis-Kontextdokument · Stand: März 2026  
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
16. [Teststrategie & Testcode](#16-teststrategie--testcode)
17. [Retrieval-Architektur: Detailbeschreibung](#17-retrieval-architektur-detailbeschreibung)
18. [Pipeline-Abläufe (Sequenzdiagramme)](#18-pipeline-abläufe-sequenzdiagramme)
19. [Datenbankabfragen (Cypher)](#19-datenbankabfragen-cypher)
20. [Evolutionsgeschichte & Designentscheidungen](#20-evolutionsgeschichte--designentscheidungen)

---

## 1. Systemüberblick & Forschungskontext

### 1.1 Gegenstand

Das vorliegende System implementiert ein **GraphRAG-Hybridsystem** (Graph-augmented Retrieval-Augmented Generation) zur automatisierten Extraktion, Strukturierung und didaktischen Aufbereitung wissenschaftlicher Literatur. Ziel ist die Generierung von Schulungskursen aus einer Menge wissenschaftlicher PDFs, die in einer Neo4j-Wissensgraphdatenbank vorgehalten werden.

Das System kombiniert:
- **Graph-basierte Wissensrepräsentation** (Neo4j AuraDB) mit expliziter Dokumenthierarchie
- **Vektor-basiertes semantisches Retrieval** (OpenAI `text-embedding-3-large`, 3072 Dimensionen)
- **LLM-gestützte Konzeptextraktion** (GPT-4o-mini) mit Deduplizierungsmechanismus
- **Hybride Named-Entity-Recognition** (spaCy + SciSpacy) für wissenschaftliche Texte
- **Didaktische Antwortgenerierung** mit Quellenbelegen und automatischer Zitationsgenerierung
- **Präsentations- und Videogenerierung** via externe APIs (Gamma, Synthesia)

### 1.2 Wissenschaftliche Relevanz

RAG-Systeme (Lewis et al., 2020) verbinden parametrisches Wissen großer Sprachmodelle mit nicht-parametrischem Dokumentwissen. GraphRAG (Edge et al., 2024) erweitert diesen Ansatz durch explizite Wissensgrafen, die semantische Beziehungen zwischen Entitäten repräsentieren und komplexere Retrievalstrategien ermöglichen.

Die vorliegende Implementierung adressiert dabei spezifisch:
1. **Skalierbarkeit**: Batch-Ingest mit automatischer Duplikaterkennung
2. **Reliabilität**: fehlertolerante JSON-Verarbeitung von LLM-Ausgaben
3. **Didaktische Qualität**: rollenbasierte Personalisierung und Zitatvalidierung
4. **Architekturevolution**: Übergang von graphbasiertem MENTIONS-Retrieval zu vektorbasiertem Retrieval

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
(Topic)-[:HAS_CONCEPT]->(Concept)-[:SEMANTIC_RELATION]->(Concept)
                                 ↑[:MENTIONS]
(Paper)-[:HAS_SECTION]->(Section)-[:HAS_PARAGRAPH]->(Paragraph)
       |-[:HAS_FIGURE]-->(Figure)<-[:CAPTIONS|REFERS_TO]-(Paragraph)
       |-[:ABOUT]------>(Concept)
```

**Knotentypen:**
| Knoten | Schlüsseleigenschaften |
|--------|----------------------|
| `Paper` | `paper_id`, `title`, `doi`, `url`, `file_sha256`, `embedding` |
| `Section` | `section_id`, `title`, `level`, `page_start`, `page_end` |
| `Paragraph` | `paragraph_id`, `text`, `page`, `bbox`, `embedding` (3072-D) |
| `Figure` | `figure_id`, `caption`, `image_uri`, `figure_label`, `embedding` (3072-D) |
| `Concept` | `concept_id`, `name`, `alt_labels`, `description`, `embedding` (3072-D) |
| `Topic` | `name` |

**Kantentypen:**
| Kante | Von → Nach | Eigenschaften |
|-------|-----------|--------------|
| `HAS_SECTION` | Paper → Section | — |
| `HAS_PARAGRAPH` | Paper/Section → Paragraph | — |
| `HAS_FIGURE` | Paper/Section → Figure | — |
| `MENTIONS` | Paragraph → Concept | `confidence` |
| `HAS_CONCEPT` | Topic → Concept | — |
| `ABOUT` | Paper → Concept | `weight` |
| `SEMANTIC_RELATION` | Concept → Concept | `relation_type`, `confidence`, `context`, `source` |
| `CO_OCCURS_WITH` | Concept ↔ Concept | `count`, `strength` |
| `CAPTIONS` | Paragraph → Figure | — |
| `REFERS_TO` | Paragraph → Figure | — |
| `NEAR` | Paragraph → Figure | — |

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
tqdm>=4.66.4            # Fortschrittsanzeige
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
```

### 3.2 Externe Dienste

| Dienst | Zweck | Modell / Endpunkt |
|--------|-------|------------------|
| OpenAI Embeddings | Vektorisierung von Text und Bildern | `text-embedding-3-large` (3072-D) |
| OpenAI Chat | Konzeptextraktion, Antwortgenerierung | `gpt-4o-mini` |
| OpenAI Vision | Bildbeschreibung | `gpt-4o-mini` (multimodal) |
| OpenAI Web Search | Web-Fallback | `responses.create` mit `web_search`-Tool |
| Neo4j AuraDB | Persistenz, Vektorindizes | Neo4j ≥ 5.19 |
| Gamma API | Präsentationsgenerierung | REST-API |
| Synthesia API | KI-Videogenerierung | `https://api.synthesia.io/v1` |
| spaCy | Standardisiertes NER | `en_core_web_sm` |
| SciSpacy | Wissenschaftliches NER | `en_core_sci_sm` |

---

## 4. Datenbankschema (Neo4j)

### 4.1 Vollständiges Schema (graph_schema.cypher)

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
import os
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI       = os.getenv("NEO4J_URI")
NEO4J_USERNAME  = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD  = os.getenv("NEO4J_PASSWORD")

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

# Gamma API (Präsentationsgenerierung)
GAMMA_API_KEY = os.getenv("GAMMA_API_KEY")
GAMMA_API_URL = os.getenv("GAMMA_API_URL")

# Synthesia (KI-Video-Provider)
SYNTHESIA_API_KEY  = os.getenv("SYNTHESIA_API_KEY")
SYNTHESIA_API_BASE = os.getenv("SYNTHESIA_API_BASE", "https://api.synthesia.io/v1")

DEFAULT_CHUNK_SIZE    = int(os.getenv("DEFAULT_CHUNK_SIZE",    "1200"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("DEFAULT_CHUNK_OVERLAP", "150"))

DATA_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
IMAGES_DIR = os.path.join(DATA_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)
```

**Designentscheidungen:**
- Alle sensiblen Zugangsdaten ausschließlich über Umgebungsvariablen (12-Factor-App-Prinzip)
- Chunk-Parameter konfigurierbar ohne Codeänderung
- Standard-Chunk-Größe 1200 Zeichen mit 150 Zeichen Überlappung (empirisch ermittelt)

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
def sha256(text: str) -> str:
    """SHA-256-Hash für Textinhalte (Duplikaterkennung auf Absatzebene)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def file_sha256(path: str) -> str:
    """SHA-256-Hash der Binärdatei (Duplikaterkennung auf Dokumentenebene)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()
```

```python
def extract_doi_and_url(doc) -> tuple[str|None, str|None]:
    """
    Zweistufige DOI/URL-Extraktion:
    1. PDF-Metadaten (doc.metadata)
    2. Heuristik via Regex auf den ersten 2 Seiten
    Regex-Muster: r'\b10\.\d{4,9}/\S+\b' für DOIs
    """
```

```python
def extract_sections(doc) -> list[dict]:
    """
    Extrahiert Dokumentstruktur aus PDF-TOC (Table of Contents).
    Berechnet Seitenbereiche (page_start, page_end) aus TOC-Einträgen.
    Jede Section erhält eine UUID als section_id.
    """
```

```python
def extract_title_from_first_page(doc) -> str | None:
    """
    Titelextraktion durch Analyse der Textformatierung:
    - Analysiert Schriftgröße und Fettschrift (Flag-Bit 4)
    - Score = Schriftgröße × 1.5 (wenn Fettschrift) sonst × 1.0
    - Mindestpunktzahl: 12 (entspricht ~12pt Schriftgröße)
    Anmerkung: Im aktuellen Code wird als Titel der Dateiname (ohne Extension) verwendet.
    """
```

```python
def extract_paragraph_blocks(page) -> list[dict]:
    """
    Robuste Textblock-Extraktion via page.get_text('rawdict').
    
    Besonderheiten:
    - Unterstützt Spans ohne 'text'-Feld (Fallback auf 'chars'-Liste)
    - Ignoriert fehlerhafte/leere Lines
    - Liefert: text, bbox, order_in_page, page_width/-height, char_start/-end
    
    Fallback: Bei leeren Blöcken → plain-text-Extraktion + Chunking
    (chunk_size=1200, overlap=150)
    """
```

```python
def find_caption_for_image(image_bbox, text_blocks,
                           y_tolerance=50, x_tolerance=20) -> tuple[str, Optional[str]]:
    """
    Intelligente Caption-Suche für Bilder:
    1. Kandidaten: Textblöcke unterhalb des Bildes (y_tolerance=50pt)
    2. Horizontale Ausrichtung: Kandidat muss unter Bild zentriert sein
    3. Mindestlänge: 10 Zeichen
    4. Ranking: Sortiert nach vertikalem Abstand (näher = besser)
    5. Figure-Label-Extraktion: Regex für "Figure 1:", "Fig. 2.3:", "Abb. 5"
    
    Returns: (caption_text, figure_label)
    """
```

```python
def match_bbox_to_xref(image_bboxes, xref_list, page) -> dict:
    """
    Spatial Matching: Ordnet xref (Bild-Index im PDF) der räumlich passenden BBox zu.
    Verwendet Zentrum-Distanz-Minimierung (Threshold: < 50 Pixel).
    Returns: {xref: bbox_index}
    """
```

### 6.3 Bildverarbeitungs-Pipeline

```
Seite laden
    │
    ├─ extract_images_with_bbox()    → BBox-Positionen der Bildblöcke
    ├─ page.get_images(full=True)    → xref-Liste
    ├─ match_bbox_to_xref()          → Spatial Mapping xref→BBox
    │
    └─ Für jedes Bild (xref):
        ├─ fitz.Pixmap()             → Pixelmap erzeugen
        ├─ Größenfilter (< 50×50 px ignorieren)
        ├─ Farbkonvertierung → RGB (für JPEG)
        ├─ Speichern (PNG wenn Alpha, sonst JPEG)
        ├─ imagehash.phash()         → Perceptual Hash
        ├─ Duplikatcheck (seen_hashes)
        └─ find_caption_for_image()  → Caption + Figure-Label
```

### 6.4 Embed-Funktionen

```python
def embed_paragraphs(paragraphs: list[dict]) -> list[dict]:
    """
    Batch-Embedding aller Paragraphen.
    Verwendet: text-embedding-3-large (3072-D)
    Hinzugefügte Eigenschaft: para['embedding'] = List[float]
    """

def analyze_and_embed_figures(figures_meta: list[dict]) -> list[dict]:
    """
    Für jede Figur:
    1. describe_image(path) via GPT-4o-mini Vision
       → Gibt: caption, figure_type, entities, ocr_hints
    2. Embedding des Descriptions-Textes
    Returns: figures mit caption, analysis_json, embedding, image_uri
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
            connection_acquisition_timeout=120, # 2 Minuten
            connection_timeout=30,
            keep_alive=True
        )
    
    def run(self, cypher: str, params=None) -> List[Dict[str, Any]]:
        """
        Führt Cypher-Query aus mit Retry-Logik (max. 2 Versuche).
        Transaction-Timeout: 300 Sekunden.
        Retry bei: 'defunct'- oder 'connection'-Fehlern.
        """
```

### 7.2 Batch-Upsert-Methoden

```python
def add_paragraphs(self, paper_id: str, paragraphs: list[dict]) -> None:
    """
    Batch-Größe: 25 Paragraphen (konservativ wegen 3072-D-Embedding-Overhead).
    Verwendet MERGE-Semantik → idempotent (re-ingest-sicher).
    Verknüpft automatisch mit passenden Sections über section_id_ref.
    """

def add_figures(self, paper_id: str, figures: list[dict]) -> None:
    """
    Batch-Größe: 10 Figuren.
    Speichert: figure_id, caption, page, image_uri, analysis_json,
               embedding, bbox, figure_label.
    """
```

### 7.3 Vektorsuche

```python
def vector_search_paragraphs(self, embedding: List[float], k: int = 12):
    """
    Neo4j Native Vector Index: 'paragraph_embedding_index'
    Cypher: CALL db.index.vector.queryNodes(...)
    Liefert: paragraph_id, text, page, paper_title, doi, url,
             section_id, section_title, score
    """

def vector_search_figures(self, embedding: List[float], k: int = 6):
    """
    Neo4j Native Vector Index: 'figure_embedding_index'
    Liefert: figure_id, caption, image_uri, figure_label, paper_title, score
    """

def vector_search_concepts(self, embedding: List[float], k: int = 10,
                            min_score: float = 0.0) -> list[dict]:
    """
    Neo4j Native Vector Index: 'concept_embedding_index'
    Filter: score >= min_score
    Liefert zusätzlich: mentions (Anzahl verknüpfter Paragraphen)
    """
```

### 7.4 Auto-Stitching

Das System führt nach dem Ingest automatisch ein "Stitching" durch, das implizite strukturelle Beziehungen im Graphen materialisiert:

```python
def stitch_document_hierarchy(self) -> dict:
    """
    1. Erstellt Dummy-Sections für Papers ohne TOC.
    2. Verbindet Paragraphen mit passenden Sections (page-range-basiert).
    3. Verbindet Figures mit passenden Sections (page-range-basiert).
    
    Returns: Stats-Dictionary mit:
      - paras_via_section, figs_via_section
      - paragraph_orphans, figure_orphans
    """

def stitch_figures_to_paragraphs(self, prefix_length=60, page_tolerance=1) -> dict:
    """
    Drei-Pass-Algorithmus zur Figur-Paragraph-Verknüpfung:
    
    PASS 1a: CAPTIONS (Caption-Prefix 60 Zeichen, ±1 Seite)
    PASS 1b: CAPTIONS (Caption-Prefix 25 Zeichen, nur gleiche Seite)  ← Fallback
    PASS 2a: REFERS_TO (figure_label direkt, z.B. "Figure 1")
    PASS 2b: REFERS_TO (Varianten: figure/fig/abb + Ziffern)
    PASS 3:  NEAR (Fallback: nächster Paragraph auf gleicher Seite)
    
    Alle Passes: MERGE-Semantik → idempotent
    """
```

### 7.5 Konzept-Management

```python
def add_concepts(self, topic_name: str, concepts: list[dict]) -> None:
    """
    MERGE-Semantik: Keine Duplikate, auch bei Mehrfachaufruf.
    Hängt Concepts an Topic via HAS_CONCEPT.
    """

def link_paragraphs_to_concepts(self, paper_id: str, links: list[dict]) -> None:
    """
    Erstellt:
    1. (Paragraph)-[:MENTIONS {confidence}]->(Concept)
    2. (Paper)-[:ABOUT {weight}]->(Concept)  ← gewichtete Aggregation
    """
```

---

## 8. Kernmodul: Konzeptextraktion (src/concept_extract.py)

### 8.1 Überblick

Das Modul implementiert LLM-basierte Konzeptextraktion mit:
- **Semantischer Deduplizierung** gegen bestehende Neo4j-Konzepte (Schwellenwert konfigurierbar, Standard: 0.92)
- **Seed-basierter Extraktion** (vordefinierte Konzepte als Ausgangspunkt)
- **Fehlertoleranter JSON-Verarbeitung** (3 Fallback-Strategien)
- **Konfidenz-Filter** (min_confidence für Link-Qualitätskontrolle)

### 8.2 Hilfsfunktionen

```python
def _slug(s: str) -> str:
    """
    Normalisiert Namen zu URL-sicheren Bezeichnern:
    Lowercase → Sonderzeichen zu '-' → Mehrfach-Bindestriche reduzieren → max. 80 Zeichen
    Fallback: UUID wenn Ergebnis leer
    """
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:80] or str(uuid.uuid4())

def _embed(texts: List[str]) -> List[List[float]]:
    """
    Batch-Embedding via OpenAI text-embedding-3-large.
    Input: Liste von Strings
    Output: Liste von 3072-dimensionalen Float-Vektoren
    """
```

### 8.3 Fehlertolerante JSON-Verarbeitung

```python
def _try_parse_llm_response(raw: str) -> dict:
    """
    Parst LLM-Ausgabe mit drei Fallback-Strategien:
    
    Strategie 1: Direktes json.loads()
    Strategie 2: Bereinigung (Kommentare entfernen, trailing commas, ...)
                 dann json.loads()
    Strategie 3: Regex-Extraktion des JSON-Objekts {.*} aus umgebendem Text
    
    Fallback: {"concepts": [], "links": []}
    
    Wichtig für Robustheit: LLMs geben häufig nicht-valides JSON aus
    (trailing commas, Code-Fence-Wrapper, eingebetteter Text etc.)
    """
```

### 8.4 LLM-Konzeptextraktion

```python
def _ask_llm_for_concepts(
    title: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str | None,
    max_concepts: int,
    seed_names: List[str] | None,
    allow_new: bool
) -> dict:
    """
    LLM-Prompt-Strategie:
    - System-Rolle: "Erfahrener Dozent, der Lernmaterial aus wissenschaftlichen Texten erstellt"
    - Input: Titel, Topic-Hint, Seed-Policy, Paragraphen (max. 80, je max. 500 Zeichen)
    - Output-Format: {"concepts":[{name, alt_labels, description}], "links":[{paragraph_id, concept_name, confidence}]}
    
    Seed-Modi:
    - allow_new=False: "AUSSCHLIESSLICH vorgegebene Konzepte nutzen"
    - allow_new=True:  "Seeds bevorzugen; Ergänzungen erlaubt"
    
    Modell: gpt-4o-mini (client.responses.create)
    """
```

### 8.5 Hauptfunktion: extract_and_embed_concepts

```python
def extract_and_embed_concepts(
    paper_title: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str = "Künstliche Intelligenz",
    max_concepts: int = 30,
    seed_names: List[str] | None = None,
    allow_new: bool = True,
    neo_client: Optional['Neo4jClient'] = None,
    dedupe_threshold: float = 0.92,
    min_confidence: float = 0.0,
    persist_to_topic: bool = False
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Vollständige Extraktions-Pipeline:
    
    1. Seeds vorbereiten (mit Embeddings via _embed)
    2. LLM-Vorschläge + Link-Informationen abrufen
    3. Konzepte kanonisieren (Duplikate gegen Seeds filtern)
    3b. Mentions-Zählung und Konfidenz-Aggregation pro Konzept
    4. Embeddings für neue Konzepte generieren
    4b. Deduplizierung gegen Neo4j (wenn neo_client vorhanden):
        a. vector_search_concepts(embedding, k=1)
        b. wenn top_score >= dedupe_threshold → mapped_existing[name] = existing_id
        c. ansonsten → created_new_concepts
    5. Gesamtkonzepte aggregieren: Seeds + Neu + Mapped
    6. Links Paragraph→Concept per Name matchen (inkl. alt_labels)
    7. Optional: persist_to_topic → neo_client.upsert_topic() + add_concepts()
    
    Returns:
        concepts: [{concept_id, name, alt_labels, description, embedding}]
        links:    [{paragraph_id, concept_id, confidence}]
    """
```

### 8.6 Hybride Extraktion (vereinfacht)

```python
def extract_and_embed_concepts_hybrid(
    paper_title: str,
    paper_text: str,
    paragraphs: List[Dict[str, Any]],
    topic_hint: str = "Künstliche Intelligenz",
    max_entities: int = 30,
    max_relations: int = 20,
    neo_client: Optional['Neo4jClient'] = None,
    persist_to_topic: bool = False
) -> Dict[str, Any]:
    """
    VEREINFACHTER Ansatz (aktuell produktiv):
    - Nur 1 LLM-Aufruf pro Paper (nicht pro Absatz!)
    - Extrahiert Konzepte aus Titel + ersten 2000 Zeichen
    - KEINE Paragraph-Verlinkung (passiert zur Retrieval-Zeit via Vektorähnlichkeit)
    - Schnell und timeout-sicher
    
    Modell: gpt-4o-mini (chat.completions.create, temperature=0.1)
    Output: {"concepts": concepts, "paragraph_links": []}
    """
```

---

## 9. Kernmodul: Entity & Relation Extraction (src/entity_relation_extract.py)

### 9.1 Hybride NER-Pipeline

```python
def extract_entities_ner(text: str, use_scispacy: bool = True) -> Dict[str, List[str]]:
    """
    Zweistufige NER:
    1. Primär: SciSpacy (en_core_sci_sm) → optimiert für wissenschaftliche Texte
    2. Fallback: spaCy (en_core_web_sm) → allgemeines NER
    
    Entity-Mapping:
    - PERSON     → PERSON
    - ORG        → ORG
    - GPE        → GPE (Geopolitische Entitäten)
    - DATE       → DATE
    - CHEMICAL/DRUG → CHEMICAL
    - DISEASE    → DISEASE
    - PRODUCT/WORK_OF_ART/LAW/LANGUAGE → SCIENTIFIC_TERM
    
    Beide Modelle werden lazy-geladen (erst bei erstem Aufruf initialisiert).
    Text-Truncation bei > 100.000 Zeichen.
    Deduplizierung der Ergebnisse.
    """
```

```python
def extract_concepts_llm(
    text: str,
    max_concepts: int = 15,
    existing_entities: Optional[Dict[str, List[str]]] = None
) -> List[Dict[str, Any]]:
    """
    LLM-Konzeptextraktion für abstrakte Konzepte, die NER nicht erfasst.
    
    Typen: methodology | theory | technique | domain_term | abstract_concept
    
    Prompt-Strategie:
    - Informiert LLM über bereits gefundene NER-Entitäten → vermeidet Duplikate
    - Fokus auf pädagogischen Wert ("für Studierende")
    - JSON-Mode: response_format={"type": "json_object"}
    - Text-Truncation: max. 4000 Zeichen
    
    Validierung: Mindestlänge 3 Zeichen, keine reinen Zahlen
    """
```

```python
def extract_entities_hybrid(
    text: str,
    max_llm_concepts: int = 15,
    use_scispacy: bool = True
) -> Dict[str, Any]:
    """
    Fusion beider Extraktionsmethoden:
    
    1. NER-Extraktion → ner_entities
    2. LLM-Extraktion (informiert über NER-Ergebnisse) → llm_concepts
    3. Merge: Nur relevante NER-Typen (SCIENTIFIC_TERM, CHEMICAL, DISEASE, ORG)
       - PERSON, DATE, GPE werden bewusst ausgeschlossen
    4. Validierung: _is_valid_entity() → min. 3 Zeichen, keine reinen Zahlen
    5. Deduplizierung (case-insensitive)
    
    Returns: {ner_entities, llm_concepts, all_entities}
    """
```

### 9.2 Semantische Relationsextraktion

```python
def extract_relations_llm(
    text: str,
    entities: List[Dict[str, Any]],
    max_relations: int = 20
) -> List[Dict[str, Any]]:
    """
    Triplet-Extraktion via LLM: (Subject, Predicate, Object)
    
    Standardisierte Prädikate:
    is_a, part_of, uses, requires, causes, leads_to,
    improves, evaluates, applies_to, based_on, extends
    
    Output je Relation: subject, predicate, object, confidence, context
    - context: Satz/Phrase (max. 200 Zeichen)
    - confidence: 0.0–1.0
    - predicate: lowercase, underscore-separated
    
    JSON-Mode, temperature=0.3
    """
```

```python
def extract_cooccurrence_relations(
    entities: List[Dict[str, Any]],
    paragraphs: List[Dict[str, Any]],
    window_size: int = 50
) -> List[Dict[str, Any]]:
    """
    Statistisches Ko-Okkurrenz-Tracking:
    - Fenster: 50 Zeichen
    - Minimum: 2 gemeinsame Vorkommen
    - Konfidenz: min(0.9, 0.5 + count × 0.1)
    - Prädikatstyp: "co_occurs_with"
    - Kanonische Paare: alphabetisch sortiert (verhindert Doppelzählung)
    """
```

### 9.3 Integrierte Pipeline

```python
def extract_entities_and_relations(
    text: str,
    paragraphs: Optional[List[Dict[str, Any]]] = None,
    max_entities: int = 30,
    max_relations: int = 20,
    use_scispacy: bool = True,
    extract_cooccurrence: bool = True,
    embed_entities: bool = True,
) -> Dict[str, Any]:
    """
    Vollständige Pipeline:
    1. extract_entities_hybrid()    → Entitäten (NER + LLM)
    2. extract_relations_llm()      → Semantische Triplets
    3. extract_cooccurrence_relations() → Statistische Relationen
    4. _embed() auf Entity-Namen    → 3072-D Embeddings
    
    Returns: {entities, relations, stats}
    """
```

---

## 10. Kernmodul: Retrieval (src/retriever.py)

### 10.1 Embed-Hilfsfunktion

```python
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")  # 3072-D

def _embed_query(text: str) -> List[float]:
    """
    Delegiert an openai_client.embed_text() für konsistente Fehlerbehandlung.
    Kein OpenAI-Client wird beim Import instanziiert.
    """
```

### 10.2 Vektorsuchfunktionen

```python
def _vsearch_paragraphs(neo, embedding, k=24) -> List[Dict[str, Any]]:
    """
    Suche auf 'paragraph_embedding_index'.
    JOIN: Paper → (optional) Section → Paragraph
    Liefert: paragraph_id, text, page, paper_title, doi, url, authors, year,
             section_id, section_title, score
    """

def _vsearch_figures(neo, embedding, k=8) -> List[Dict[str, Any]]:
    """
    Suche auf 'figure_embedding_index'.
    Liefert: figure_id, caption, figure_label, page, image_uri,
             analysis_json, paper_title, score
    """

def _vsearch_concepts(neo, embedding, k=10, min_score=0.6) -> List[Dict[str, Any]]:
    """
    Suche auf 'concept_embedding_index'.
    Filter: WHERE score >= $min_score
    Liefert: concept_id, concept_name, description, score
    Standard min_score: 0.6 (niedrig, da Konzeptnamen kurz sind und
    daher geringere Ähnlichkeitswerte als vollständige Paragraphen erzielen)
    """
```

### 10.3 Konzept-zu-Paragraph Überbrückung

```python
def _paragraphs_via_concepts(neo, concept_ids, limit=20) -> List[Dict[str, Any]]:
    """
    ARCHITEKTURENTSCHEIDUNG (2024): Vektorbasiert statt MENTIONS-basiert.
    
    Ansatz:
    1. Concept-Embeddings aus Neo4j abrufen
    2. Durchschnitt über alle Concept-Embeddings berechnen (numpy.mean)
    3. Vektorsuche auf paragraph_embedding_index mit dem Durchschnittsvektor
    
    Begründung:
    - Schneller (keine LLM-Aufrufe beim Ingest)
    - Semantisch besser (findet ähnliche Inhalte ohne exakte Name-Matches)
    - Zuverlässiger (kein fragiles String-Matching)
    """
```

### 10.4 Semantische Relation-Expansion

```python
def _expand_via_semantic_relations(neo, concept_ids, k=5) -> List[str]:
    """
    Erweitert Konzeptliste über SEMANTIC_RELATION-Kanten.
    Erlaubte Relationstypen: IS_A, PART_OF, RELATED_TO
    
    Beispiel:
    Query: "Neural Networks"
    Gefunden: "Neural Networks" (direkt)
    Expandiert: "Deep Learning" (IS_A), "Backpropagation" (PART_OF)
    """
```

### 10.5 Figure-Kontext-Anreicherung

```python
def _expand_figure_context_with_paragraphs(neo, supports, limit=200) -> None:
    """
    Hängt zu bereits gefundenen Figures passende Paragraphen an.
    Traversierung: (Figure)<-[:REFERS_TO|:CAPTIONS]-(Paragraph)
    Deduplizierung gegen bestehende Paragraphen (in-place Modifikation).
    Score: 0.99 (hohe Priorität als direkte Kontext-Quelle)
    """
```

### 10.6 Öffentliche Retrieval-API

```python
def hybrid_retrieve(
    neo: Neo4jClient,
    query: str,
    *,
    k_paragraphs: int = 18,
    k_figures: int = 6,
    add_figure_context: bool = True,
    use_concept_based: bool = False,
) -> Dict[str, Any]:
    """
    Einfaches Hybrid-Retrieval (Fallback-Strategie):
    1. Query embedden
    2. Paragraphen-Vektorsuche (k_paragraphs)
    3. Figure-Vektorsuche (k_figures)
    4. Optional: Figure-Kontext ergänzen
    Returns: {"supports": [...]}
    """

def concept_based_retrieve(
    neo: Neo4jClient,
    query: str,
    *,
    k_concepts: int = 10,
    k_paragraphs_direct: int = 15,
    k_paragraphs_via_concepts: int = 20,
    k_figures: int = 6,
    expand_semantic: bool = True,
    add_figure_context: bool = True,
    min_concept_score: float = 0.6,
) -> Dict[str, Any]:
    """
    Intelligentes Concept-basiertes Retrieval (Standard-Strategie):
    
    1. Concept-Vektorsuche (k_concepts, min_score=0.6)
    2. Concept-Expansion via Semantic Relations (IS_A, PART_OF, RELATED_TO)
    3. Paragraphen via Concept-Embedding-Durchschnitt
    4. Direkte Paragraphen-Vektorsuche (Fallback/Ergänzung)
    5. Figure-Vektorsuche
    6. Deduplizierung: Concept-basiert hat höhere Priorität
    7. Figure-Kontext ergänzen
    
    Returns: {
        "supports": [...],
        "matched_concepts": [...],
        "debug": {matched_concepts_count, expanded_via_relations, 
                  paragraphs_via_concepts, paragraphs_direct, figures, total_supports}
    }
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

### 11.2 Web-Suche via OpenAI

```python
def answer_via_openai_web(query: str, *, lang: str = "de",
                          force_tool: bool = False) -> Dict[str, Any]:
    """
    OpenAI Responses API mit web_search-Tool.
    
    System-Prompt: Schulungsmaterialien-Erstellung, praktische Anwendbarkeit
    Tool-Choice: "force" | "auto" (je nach Verfügbarkeit von Belegen)
    
    Fallback-Kaskade:
    1. OPENAI_WEB_MODEL (konfigurierbar)
    2. gpt-4o-mini (fallback bei Tool-Zugangsproblemen)
    
    JSON-Extraktion: Quellen-Block ```json {"sources":[{url, title}]}```
    wird aus dem Antworttext extrahiert und als supports zurückgegeben.
    """
```

### 11.3 Orchestrierung (answer_query)

```python
def answer_query(
    query: str,
    neo: Neo4jClient,
    min_supports: int = 3,
    min_supports_score: float = 0.0,
    web_mode: str | None = None,   # "auto" | "force" | "off"
    k_paragraphs: int = 24,
    k_figures: int = 8,
    use_concept_retrieval: bool = True,
) -> Dict[str, Any]:
    """
    Drei-Wege-Entscheidungslogik:
    
    FORCE → sofort Websuche (Graph übersprungen)
    
    OFF   → nur Graph
           ↓ concept_based_retrieve() oder hybrid_retrieve()
           ↓ max 120 Paragraphen + alle Figures
           ↓ grounded_answer()
           → mode: "graph" | "graph_low_coverage"
    
    AUTO (Standard):
           ↓ Graph-Retrieval
           ↓ _effective_supports() zählt Paragraphen/Figures mit score ≥ min_score
           ├─ eff >= min_supports → Antwort aus Graph
           │                      → mode: "graph"
           └─ eff < min_supports  → Web-Fallback
                                  → force_tool wenn eff == 0
                                  → mode: "web"
    
    Returns: {mode, answer, supports, debug}
    """
```

---

## 12. Kernmodul: OpenAI-Client (src/openai_client.py)

### 12.1 Initialisierung (Lazy)

```python
_client: OpenAI | None = None

def client() -> OpenAI:
    """
    Lazy-Initialisierung: Client wird erst bei erstem Aufruf erstellt.
    Verhindert Import-Zeit-Fehlschläge bei fehlendem API-Key.
    """
```

### 12.2 Embedding-Funktion

```python
def embed_text(text: str, model: str = "text-embedding-3-large") -> List[float]:
    """
    Einzeltext-Embedding.
    Modell: text-embedding-3-large → 3072-dimensionaler Vektor
    """
```

### 12.3 Vision-API

```python
def describe_image(path: str) -> Dict[str, Any]:
    """
    Bildbeschreibung via GPT-4o-mini (multimodal).
    
    Input: Lokaler Bildpfad → Base64-Kodierung → data-URL
    System-Prompt: Wissenschaftlicher Bild-Analyst, KEINE Emojis
    Output-Schema: {caption, figure_type, entities, ocr_hints}
    figure_type: chart | diagram | photo | table-scan | other
    
    Post-Processing: Unicode-Emoji-Filterung via Regex
    (verhindert Emojis in wissenschaftlichen Beschreibungen)
    """
```

### 12.4 Didaktische Antwortgenerierung

```python
def grounded_answer(query: str, supports: List[Dict[str, Any]]) -> str:
    """
    Generiert didaktisch aufbereitete Antwort mit Quellenbelegen.
    
    System-Rolle: "Erfahrener Dozent und Lehrer"
    Struktur: Definition → Erklärung → Beispiele → Zusammenhänge
    
    Kontext-Aufbereitung:
    - Paragraphen: [P<id>] <Titel> • S.<Seite> • <Section> :: <Text[:800]>
    - Figures:     [F<id>] <Titel> • Abb. • S.<Seite> :: <Caption[:300]>
    - Bibliographie: JSON-Dictionary {ref_id: {paper, doi, url, page, section}}
    
    Zitationsformat: [Pxxx] oder [Fxxx] direkt im Fließtext
    KEINE Seitenzahlen in Klammern (z.B. NICHT [P12345 S.5])
    
    Post-Processing: Zitationen die direkt nach Listenmarkierungen
    stehen (z.B. "1[1].") werden ans Zeilenende verschoben.
    
    Modell: gpt-4o-mini, temperature=0.2
    Max. Kontext: 20 Belege
    """
```

---

## 13. Kernmodul: Zitationsvalidierung (src/citation_validator.py)

### 13.1 Zitationsextraktion

```python
def _extract_citations_from_text(text: str) -> List[Tuple[str, str]]:
    """
    Unterstützt zwei Formate:
    1. Numerisch: [1], [2], [1; 2; 3] (APA-ähnlich)
    2. ID-basiert: [Pxxx], [Fxxx] (systeminternes Format)
    
    Gibt zurück: [(citation_id, surrounding_context)]
    context_window: 150 Zeichen vor + 100 Zeichen nach der Zitation
    """
```

### 13.2 Semantische Ähnlichkeitsvalidierung

```python
def calculate_semantic_similarity(text1: str, text2: str) -> float:
    """
    Kosinus-Ähnlichkeit via OpenAI-Embeddings.
    Text-Truncation: max. 300 Zeichen (Performance).
    Normalisierung: (cosine + 1) / 2 → 0.0–1.0
    Fallback bei Fehler: 0.5 (neutral)
    """
```

### 13.3 Vollständige Validierung

```python
def validate_citations(
    generated_text: str,
    supports: List[Dict[str, Any]],
    similarity_threshold: float = 0.65
) -> Dict[str, Any]:
    """
    Getrennte Validierung für numerische und ID-basierte Zitationen:
    
    Numerisch [1] [2] ...:
    - Prüft nur ob Supports überhaupt vorhanden sind
    - Kein semantischer Check (Nummer→Support nicht direkt zuordenbar)
    
    ID-basiert [Pxxx] [Fxxx]:
    - Sucht passendes Support-Dokument
    - Berechnet semantische Ähnlichkeit: Zitationskontext vs. Quelltext
    - status: "valid" | "warning" | "invalid"
    - Schwellenwert: similarity_threshold (Standard: 0.65)
    
    Returns: {total_citations, valid_count, invalid_count, warning_count,
              warnings, details}
    """
```

---

## 14. Kernmodul: Erweiterter Ingest (src/ingest_enhanced.py)

```python
def calculate_dynamic_parameters(num_paragraphs: int) -> Dict[str, int]:
    """
    Passt Extraktionsparameter dynamisch an Dokumentgröße an:
    max_entities  = min(50, max(20, n_paras // 10))
    max_relations = min(30, max(10, n_paras // 15))
    k_paragraphs  = min(40, max(15, n_paras // 5))
    k_figures     = min(12, max(5,  n_paras // 20))
    """

def infer_topic_from_title(title: str) -> str:
    """
    Keyword-basierte Topic-Inferenz.
    Kategorien: Künstliche Intelligenz, Medizin, Physik, Chemie,
                Biologie, Informatik, Mathematik, Ingenieurwesen
    Fallback: "Künstliche Intelligenz"
    """

def check_for_duplicates(neo: Neo4jClient, paper_meta: dict) -> Optional[Dict[str, Any]]:
    """
    Drei-Stufen-Duplikaterkennung:
    1. file_sha256-Hash (exakte Übereinstimmung)
    2. DOI-Übereinstimmung
    3. Titel-Übereinstimmung (toLower)
    
    Returns: {type, paper_id, title, message} | None
    """

def filter_low_quality_concepts(concepts, min_confidence=0.6,
                                  links=None) -> Tuple[List, List]:
    """
    Filtert Konzepte aus, deren durchschnittliche Link-Konfidenz
    unter min_confidence (Standard: 0.6) liegt.
    Aktualisiert verbleibende Links entsprechend.
    """

def extract_enhanced_metadata(pdf_path: Path, paper_meta: dict) -> dict:
    """
    Erweitert Standard-Metadaten um:
    - file_size (Bytes)
    - ingested_at (ISO-Timestamp)
    - pdf_author, pdf_subject, pdf_keywords, pdf_creator, pdf_producer
    - pdf_creation_date, pdf_mod_date
    - page_count
    """
```

---

## 15. Skripte

### 15.1 scripts/ingest.py

CLI-Einstiegspunkt für PDF-Ingest. Verarbeitet eine oder mehrere PDF-Dateien sequenziell.

**Ablauf je PDF:**
1. `read_pdf_text_and_images()` → paper_meta, sections, paragraphs, figures
2. `neo.upsert_paper()` → Paper-Knoten mit DOI/URL/Hash
3. `neo.add_sections()` → Sections (falls TOC vorhanden)
4. `embed_paragraphs()` → Embeddings + `neo.add_paragraphs()`
5. `analyze_and_embed_figures()` + `neo.add_figures()`
6. `extract_and_embed_concepts_hybrid()` oder Legacy-Extraktion
7. `neo.stitch_document_hierarchy()` + `neo.stitch_figures_to_paragraphs()`

**Zwei Extraktionsmodi:**
```python
# Modus 1: Legacy (LLM-only)
# concepts, links = extract_and_embed_concepts(...)

# Modus 2: Hybrid (aktuell aktiv)
result = extract_and_embed_concepts_hybrid(
    paper_title=paper_meta.get("title") or "",
    paper_text=full_text,
    paragraphs=paragraphs,
    max_entities=30,
    max_relations=20,
    neo_client=neo,
    persist_to_topic=True
)
```

### 15.2 scripts/ask.py

```python
"""CLI-Frageschnittstelle.
Aufruf: python scripts/ask.py "Erkläre den Unterschied zwischen supervised und unsupervised learning."
Gibt: Antwort + Belegquellen auf der Konsole aus.
"""
```

### 15.3 scripts/gui_app.py

Streamlit-basierte Web-Oberfläche mit mehrstufiger Tab-Architektur:

| Tab | Funktion |
|-----|---------|
| Ingest | PDF-Upload, Konzeptextraktion, Duplikaterkennung |
| Kurs | Interaktive Kursgenerierung mit Kapiteln/Lernzielen |
| Export | PDF-Export, Präsentationsgenerierung (Gamma), Videogenerierung (Synthesia) |
| Graph | Graphvisualisierung mit Cypher-Queries |
| Diagnose | Datenbankstatistiken, Debug-Informationen |

---

## 16. Teststrategie & Testcode

### 16.1 Testphilosophie

Die Testsuite (tests/test_concept_extract.py) folgt diesen Prinzipien:
- **Vollständige API-Isolation**: Kein OpenAI-Aufruf, kein Neo4j-Aufruf
- **AAA-Muster** (Arrange – Act – Assert) mit Docstrings
- **Deterministische Stubs** mit vorhersehbarem Verhalten
- **Grenzwerttests** via `@pytest.mark.parametrize`

### 16.2 Vollständiger Testcode (tests/test_concept_extract.py)

```python
"""Unit-Tests für src/concept_extract.py

Alle Tests sind vollständig isoliert von externen APIs (OpenAI, Neo4j).
Der Aufbau folgt dem Arrange–Act–Assert-Muster (AAA).
"""
import pytest
from src import concept_extract as ce


# ===========================================================================
# Stubs & Hilfsfunktionen
# ===========================================================================

class FakeNeo:
    """Minimalstub für Neo4jClient mit deterministischem Verhalten.

    Gibt für das Embedding [1.0] einen Treffer mit score=0.95 auf
    'existing-1' zurück; für alle anderen Embeddings keine Treffer.
    """

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
    """Erzeugt eine LLM-Stub-Funktion mit fest vorgegebener Rückgabe."""
    def fake_llm(title, paragraphs, topic_hint, max_concepts, seed_names, allow_new):
        return {"concepts": concepts, "links": links}
    return fake_llm


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def fake_neo():
    return FakeNeo()

@pytest.fixture
def sample_paragraphs():
    return [
        {"paragraph_id": "p1", "text": "Text über Existing Concept."},
        {"paragraph_id": "p2", "text": "Text über New Interesting."},
        {"paragraph_id": "p3", "text": "Weiterer Text zu New Interesting."},
    ]

@pytest.fixture(autouse=True)
def mock_embed(monkeypatch):
    """Ersetzt _embed durch eine deterministische Stub-Funktion.

    Regel:
    - Text enthält 'existing' (case-insensitive) → Embedding [1.0]
    - Andernfalls → Embedding [0.0]

    Kein OpenAI-API-Aufruf findet statt; Tests sind vollständig isoliert.
    """
    def fake_embed(texts):
        return [[1.0] if "existing" in t.lower() else [0.0] for t in texts]
    monkeypatch.setattr(ce, '_embed', fake_embed)


# ===========================================================================
# Tests: Deduplizierung
# ===========================================================================

def test_dedupe_maps_to_existing(monkeypatch, fake_neo, sample_paragraphs):
    """Arrange: LLM schlägt 'Existing Concept' vor; FakeNeo liefert score 0.95 >= Schwelle 0.9.
    Act:     extract_and_embed_concepts mit aktivierter Deduplizierung.
    Assert:  Kein Duplikat erzeugt; Link p1 zeigt auf concept_id 'existing-1'.
    """
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[
            {"name": "Existing Concept", "alt_labels": [], "description": "desc"},
            {"name": "New Interesting",  "alt_labels": [], "description": "desc2"},
        ],
        links=[
            {"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9},
            {"paragraph_id": "p2", "concept_name": "New Interesting",  "confidence": 0.8},
            {"paragraph_id": "p3", "concept_name": "New Interesting",  "confidence": 0.7},
        ],
    ))
    concepts, links = ce.extract_and_embed_concepts(
        paper_title="T", paragraphs=sample_paragraphs,
        topic_hint="T", max_concepts=10, seed_names=[],
        allow_new=True, neo_client=fake_neo,
        dedupe_threshold=0.9, min_confidence=0.0,
    )
    p1_links = [l for l in links if l["paragraph_id"] == "p1"]
    assert p1_links
    assert all(l["concept_id"] == "existing-1" for l in p1_links)
    assert any(c["name"].lower() == "new interesting" for c in concepts)


def test_dedupe_no_duplicate_created(monkeypatch, fake_neo, sample_paragraphs):
    """Wenn ein Konzept auf ein bestehendes gemappt wird, darf es nicht
    zusätzlich als neues Konzept erscheinen (kein Duplikat).
    """
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[{"name": "Existing Concept", "alt_labels": [], "description": "desc"}],
        links=[{"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9}],
    ))
    concepts, _ = ce.extract_and_embed_concepts(
        paper_title="T", paragraphs=sample_paragraphs,
        neo_client=fake_neo, dedupe_threshold=0.9, min_confidence=0.0,
    )
    names_lower = [c["name"].lower() for c in concepts]
    assert names_lower.count("existing concept") <= 1


@pytest.mark.parametrize("threshold,expect_mapped", [
    (0.90, True),   # score 0.95 >= 0.90 → bestehendes Konzept nutzen
    (0.99, False),  # score 0.95 < 0.99  → neues Konzept anlegen
])
def test_dedupe_threshold_boundary(monkeypatch, fake_neo, sample_paragraphs,
                                   threshold, expect_mapped):
    """Parametrisierter Grenzwerttest: dedupe_threshold steuert Mapping vs. Neuanlage."""
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[{"name": "Existing Concept", "alt_labels": [], "description": "d"}],
        links=[{"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9}],
    ))
    concepts, links = ce.extract_and_embed_concepts(
        paper_title="T", paragraphs=sample_paragraphs,
        neo_client=fake_neo, dedupe_threshold=threshold,
        min_confidence=0.0, allow_new=True,
    )
    mapped = any(l.get("concept_id") == "existing-1" for l in links)
    assert mapped == expect_mapped


# ===========================================================================
# Tests: allow_new=False
# ===========================================================================

def test_allow_new_false_no_extra_concepts(monkeypatch, sample_paragraphs):
    """allow_new=False: Nur Seeds im Ergebnis, LLM-Extras werden verworfen."""
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[
            {"name": "AI Basics",      "alt_labels": [], "description": "seed"},
            {"name": "Neural Network", "alt_labels": [], "description": "extra"},
        ],
        links=[
            {"paragraph_id": "p1", "concept_name": "AI Basics",      "confidence": 0.9},
            {"paragraph_id": "p2", "concept_name": "Neural Network", "confidence": 0.8},
        ],
    ))
    concepts, _ = ce.extract_and_embed_concepts(
        paper_title="T", paragraphs=sample_paragraphs,
        seed_names=["AI Basics"], allow_new=False, min_confidence=0.0,
    )
    names = [c["name"].lower() for c in concepts]
    assert "ai basics" in names
    assert "neural network" not in names


# ===========================================================================
# Tests: min_confidence-Filter
# ===========================================================================

def test_min_confidence_filters_concepts(monkeypatch, sample_paragraphs):
    """Konzepte unter min_confidence werden nicht in die Ergebnisliste aufgenommen."""
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[
            {"name": "High Conf", "alt_labels": [], "description": "d"},
            {"name": "Low Conf",  "alt_labels": [], "description": "d"},
        ],
        links=[
            {"paragraph_id": "p1", "concept_name": "High Conf", "confidence": 0.9},
            {"paragraph_id": "p2", "concept_name": "Low Conf",  "confidence": 0.2},
        ],
    ))
    concepts, _ = ce.extract_and_embed_concepts(
        paper_title="T", paragraphs=sample_paragraphs,
        allow_new=True, min_confidence=0.5,
    )
    names = [c["name"].lower() for c in concepts]
    assert "high conf" in names
    assert "low conf" not in names


# ===========================================================================
# Tests: _try_parse_llm_response
# ===========================================================================

def test_parse_valid_json():
    raw = '{"concepts": [{"name": "KI"}], "links": []}'
    result = ce._try_parse_llm_response(raw)
    assert result["concepts"] == [{"name": "KI"}]
    assert result["links"] == []

def test_parse_json_with_trailing_comma():
    raw = '{"concepts": [{"name": "KI",}], "links": [],}'
    result = ce._try_parse_llm_response(raw)
    assert isinstance(result, dict)
    assert "concepts" in result

def test_parse_empty_string():
    result = ce._try_parse_llm_response("")
    assert result == {"concepts": [], "links": []}

def test_parse_json_in_code_fence():
    raw = '```json\n{"concepts": [], "links": [{"paragraph_id": "p1"}]}\n```'
    result = ce._try_parse_llm_response(raw)
    assert result["links"] == [{"paragraph_id": "p1"}]

def test_parse_missing_keys_filled_with_defaults():
    raw = '{"concepts": [{"name": "Test"}]}'
    result = ce._try_parse_llm_response(raw)
    assert "links" in result
    assert result["links"] == []
```

---

## 17. Retrieval-Architektur: Detailbeschreibung

### 17.1 Architekturevolution: MENTIONS-basiert → Vektorbasiert

#### Ausgangsproblem (MENTIONS-basiert)

Im ursprünglichen Ansatz wurden Konzepte während des Ingest-Prozesses mit Paragraphen verknüpft:
- **Problem 1**: LLM-Timeouts bei der Verknüpfung (60s pro Batch von 3–5 Paragraphen)
- **Problem 2**: 82% der Paragraphen ohne Konzeptverknüpfungen
- **Problem 3**: 15–20% Konzeptabdeckung nach mehrfachen Reingest-Versuchen
- **Problem 4**: Fragile String-Matching-Logik

```
ALTES System (MENTIONS-basiert):
Ingest: Paper → [LLM für jeden Batch von Paragraphen] → MENTIONS-Kanten
Retrieval: Concept → [:MENTIONS] ← Paragraph

Problem: O(n_paragraphen × LLM_calls) → timeout-anfällig
```

#### Neue Architektur (Vektorbasiert)

```
NEUES System (Vector-basiert):
Ingest: Paper → [1 LLM-Aufruf] → Concept-Knoten mit Embeddings
        KEINE MENTIONS-Kanten
        
Retrieval: Query → embed() → concept_embedding_index (CALL db.index.vector...)
                           → avg(concept_embeddings)
                           → paragraph_embedding_index
```

**Ergebnisse nach Umstellung:**
| Metrik | MENTIONS-basiert | Vektorbasiert |
|--------|-----------------|--------------|
| Ingest-Geschwindigkeit | Timeouts häufig | 1 LLM-Aufruf/Paper |
| Konzeptabdeckung | 15–20% | N/A (kein Linking) |
| Retrieval-Ergebnisse | 0 Treffer | 10 Paragraphen via Konzepte |
| Semantisches Matching | Exakte Strings | Ähnlichkeitsbasiert |
| Skalierbarkeit | O(n×LLM) | O(1×LLM + Vektorsuche) |

### 17.2 Vollständiger Retrieval-Pfad

```
Query: "Was ist Deep Learning?"
    │
    ├─ 1. _embed_query(query) → 3072-D Vektor
    │
    ├─ 2. concept_embedding_index CALL vector.queryNodes(k=10, min_score=0.6)
    │      → ["Deep Learning" (0.91), "Neural Networks" (0.87), "Backpropagation" (0.81), ...]
    │
    ├─ 3. _expand_via_semantic_relations(concept_ids, k=5)
    │      → "Machine Learning" (IS_A), "CNN" (PART_OF)
    │
    ├─ 4. neo.run("MATCH (c:Concept {concept_id: cid}) RETURN c.embedding")
    │      → Embeddings für alle gefundenen Konzepte
    │      numpy.mean([emb1, emb2, emb3, ...], axis=0) → avg_embedding
    │
    ├─ 5. paragraph_embedding_index CALL vector.queryNodes($avg_embedding, limit=20)
    │      → 20 semantisch ähnliche Paragraphen (via gemittelter Konzept-Embedding)
    │
    ├─ 6. paragraph_embedding_index CALL vector.queryNodes($query_embedding, k=15)
    │      → 15 direkte Treffer via Query-Embedding
    │
    ├─ 7. figure_embedding_index CALL vector.queryNodes($query_embedding, k=6)
    │      → 6 relevante Abbildungen
    │
    ├─ 8. Deduplizierung (concept-basierte Ergebnisse haben höhere Priorität)
    │
    ├─ 9. _expand_figure_context_with_paragraphs()
    │      → (Figure)<-[:REFERS_TO|:CAPTIONS]-(Paragraph) traversieren
    │
    └─ 10. grounded_answer(query, supports)
           → GPT-4o-mini mit Dozenten-Systemprompt
           → Didaktische Antwort mit [Pxxx]/[Fxxx]-Zitationen
```

---

## 18. Pipeline-Abläufe (Sequenzdiagramme)

### 18.1 PDF-Ingest-Pipeline

```
User → GUI/CLI → ingest_one_pdf(path)
                      │
          ┌───────────▼────────────┐
          │  Phase 1: Extraktion   │
          │  read_pdf_text_and_    │
          │  images() via PyMuPDF  │
          │  ├─ Text (rawdict)     │
          │  ├─ Images (xref)      │
          │  ├─ TOC (Sections)     │
          │  └─ Metadaten (DOI)    │
          └───────────┬────────────┘
                      │
          ┌───────────▼────────────┐
          │  Phase 2: Embeddings   │
          │  embed_paragraphs()    │
          │  + analyze_and_embed_  │
          │    figures() via       │
          │    GPT-4o-mini Vision  │
          └───────────┬────────────┘
                      │
          ┌───────────▼────────────┐
          │  Phase 3: Persistenz   │
          │  neo.upsert_paper()    │
          │  neo.add_sections()    │
          │  neo.add_paragraphs()  │ ← Batch-Größe: 25
          │  neo.add_figures()     │ ← Batch-Größe: 10
          └───────────┬────────────┘
                      │
          ┌───────────▼────────────┐
          │  Phase 4: Konzepte     │
          │  extract_and_embed_    │
          │  concepts_hybrid()     │
          │  ├─ 1 LLM-Aufruf      │
          │  ├─ Embeddings         │
          │  └─ neo.add_concepts() │
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

### 18.2 Kurs-Generierungs-Pipeline

```
User wählt Kapitel/Abschnitt
    │
    ├─ answer_query(web_mode="off", k_paragraphs=48, k_figures=8)
    │       │
    │   concept_based_retrieve()
    │       ├─ concepts gefunden? → Concept-Pfad
    │       └─ keine concepts   → hybrid_retrieve() Fallback
    │       │
    │   grounded_answer(query, supports)
    │       └─ GPT-4o-mini → Antworttext mit [Pxxx]/[Fxxx]
    │
    ├─ citation_validator.validate_citations()
    │       └─ Semantischer Check für [Pxxx]/[Fxxx]
    │
    └─ Nächster Abschnitt (Schleife)
           │
           └─ Optional: PDF-Export / Präsentation / Video
```

---

## 19. Datenbankabfragen (Cypher)

### 19.1 Diagnostik

```cypher
-- Alle Knotentypen zählen
MATCH (n)
RETURN labels(n) AS NodeType, count(n) AS Count
ORDER BY Count DESC;

-- Papers und ihre Komponenten
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:HAS_PARAGRAPH]->(para:Paragraph)
OPTIONAL MATCH (p)-[:HAS_FIGURE]->(fig:Figure)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)
RETURN p.title AS Paper,
       count(DISTINCT para) AS Paragraphs,
       count(DISTINCT fig) AS Figures,
       count(DISTINCT sec) AS Sections
LIMIT 10;

-- Konzept-Paragraph-Verknüpfungen prüfen
MATCH (para:Paragraph)-[:MENTIONS]->(c:Concept)
RETURN count(*) AS MentionsCount;
```

### 19.2 Vektorsuche (Programmatisch via Neo4j Cypher)

```cypher
-- Paragraph-Vektorsuche
CALL db.index.vector.queryNodes('paragraph_embedding_index', $k, $embedding)
YIELD node, score
WITH node, score
MATCH (p:Paper)-[:HAS_PARAGRAPH]->(node)
OPTIONAL MATCH (p)-[:HAS_SECTION]->(sec:Section)-[:HAS_PARAGRAPH]->(node)
RETURN node.text AS text, p.title AS paper, toFloat(score) AS score
ORDER BY score DESC LIMIT 10;

-- Konzept-Vektorsuche mit Score-Filter
CALL db.index.vector.queryNodes('concept_embedding_index', 10, $embedding)
YIELD node, score
WHERE score >= 0.6
RETURN node.name AS concept, toFloat(score) AS score
ORDER BY score DESC;
```

### 19.3 Semantische Relationen

```cypher
-- Alle IS_A-Relationen (Taxonomie)
MATCH (sub:Concept)-[r:SEMANTIC_RELATION {relation_type: 'IS_A'}]->(obj:Concept)
RETURN sub.name AS Subkonzept, obj.name AS Überkonzept, r.confidence
ORDER BY r.confidence DESC;

-- Transitive Konzepthierarchie
MATCH path = (c:Concept {name: 'Convolutional Neural Network'})
               -[:SEMANTIC_RELATION*1..3 {relation_type: 'IS_A'}]->
               (general:Concept)
RETURN [node IN nodes(path) | node.name] AS Hierarchie;

-- Ko-Okkurrenz-Netzwerk (stark korreliert)
MATCH (c1:Concept)-[r:CO_OCCURS_WITH]-(c2:Concept)
WHERE r.strength > 0.7
RETURN c1.name, c2.name, r.strength, r.count
ORDER BY r.strength DESC LIMIT 20;
```

### 19.4 Graph-Visualisierung

```cypher
-- Vollständiger Subgraph für ein Topic
MATCH (t:Topic {name: $topic})
OPTIONAL MATCH (t)-[:HAS_CONCEPT]->(c:Concept)
WITH c WHERE c IS NOT NULL
OPTIONAL MATCH p1 = (c)<-[:MENTIONS]-(para:Paragraph)<-[:HAS_PARAGRAPH]-(paper:Paper)
OPTIONAL MATCH p2 = (paper)-[:HAS_FIGURE]->(fig:Figure)
OPTIONAL MATCH p3 = (c)-[:SEMANTIC_RELATION]->(c2:Concept)
RETURN p1, p2, p3 LIMIT 500;
```

---

## 20. Evolutionsgeschichte & Designentscheidungen

### 20.1 Chronologie der Architekturänderungen

| Phase | Beschreibung | Problem | Lösung |
|-------|-------------|---------|--------|
| v1 | Reines Vektor-RAG | Keine strukturelle Wissensrepräsentation | Graph-Integration |
| v2 | MENTIONS-basiertes GraphRAG | Ingest-Timeouts, 82% Paragraphen ohne Links | Vektorbasiertes Retrieval |
| v3 (aktuell) | Vektorbasiertes GraphRAG | — | Hybrides NER + LLM, Didaktische Antwortgenerierung |

### 20.2 Zentrale Designentscheidungen

**1. Trennung von Ingest und Linking**
- Konzeptextraktion beim Ingest: Nur Konzeptknoten erstellen + embedden
- Paragraph-Konzept-Zuordnung: Zur Retrieval-Zeit über Vektorähnlichkeit
- Vorteil: O(1) statt O(n) LLM-Aufrufe, keine Timeouts

**2. Drei Vektorindizes statt einem**
- Separate Indizes für Paragraphen, Figures und Konzepte
- Ermöglicht gezielte Suche je nach Anfragetyp
- Alle 3072-dimensional (text-embedding-3-large)

**3. Konzept-Embedding-Mittelung**
- Mehrere relevante Konzepte → Durchschnitt der Embeddings
- Fungiert als "semantisches Zentroid" der Anfrage
- Findet Paragraphen, die thematisch relevant sind, ohne exakte Konzeptnamen zu erwähnen

**4. Didaktischer Systemprompt**
- Rolle: "Erfahrener Dozent" statt generischem Assistenten
- Struktur: Definition → Erklärung → Beispiele → Zusammenfassung
- Direkte Abbildungsreferenzen im Fließtext ([Fxxx])

**5. Robuste JSON-Verarbeitung**
- 3 Parsing-Strategien für LLM-Ausgaben
- Verhindert System-Abstürze bei Halluzinationen oder Formatfehlern
- Graceful Fallback: Leeres Ergebnis statt Exception

**6. Deduplizierungsschwelle (0.92)**
- Konzepte mit ähnlichem Embedding werden konsolidiert
- Verhindert semantische Duplikate im Konzeptgraphen
- Konfigurierbar pro Ingest-Aufruf

**7. Web-Fallback-Kaskade**
- Erst Graph; wenn < min_supports (Standard: 3) valide Belege → Web
- force_tool wenn gar keine Belege gefunden
- Transparenz: mode-Feld zeigt welcher Pfad gewählt wurde

### 20.3 Bekannte Limitierungen & offene Punkte

1. **Chunk-Kohärenz**: Feste Chunk-Größe (1200 Zeichen) ignoriert semantische Grenzen
2. **Mehrsprachigkeit**: System-Prompts auf Deutsch, NER-Modelle auf Englisch
3. **Konzept-Qualität**: LLM-generierte Konzepte ohne manuelle Qualitätskontrolle
4. **MENTIONS-Vollständigkeit**: Vektorbasiertes Retrieval ist approximativ, keine Garantie auf Vollständigkeit
5. **Sitzungsgedächtnis**: Kein Konversationsgedächtnis im Chat-Interface
6. **Embedding-Konsistenz**: Modellwechsel würde alle bestehenden Embeddings invalidieren

---

*Ende der Codebase-Dokumentation*  
*Generiert für: Masterthesis-Ausarbeitung*  
*Umfang: ~16 Quelldateien, ~5.000+ Zeilen produktiver Code*
