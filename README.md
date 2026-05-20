# GraphRAG-System zur automatisierten Kursgenerierung

Ein hybrides GraphRAG-System, das wissenschaftliche PDFs in einen Neo4j-Wissensgraphen aufnimmt und daraus strukturierte Lernkurse generiert.

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#1-voraussetzungen)
2. [Installation](#2-installation)
3. [Konfiguration (.env)](#3-konfiguration-env)
4. [Datenbank vorbereiten](#4-datenbank-vorbereiten)
5. [PDFs ingestieren](#5-pdfs-ingestieren)
6. [GUI verwenden](#6-gui-verwenden)
7. [CLI-Befehle](#7-cli-befehle)
8. [Kursgenerierung](#8-kursgenerierung)
9. [Lokaler Betrieb mit LM Studio](#9-lokaler-betrieb-mit-lm-studio)
10. [Projektstruktur](#12-projektstruktur)

---

## 1. Voraussetzungen

- Python 3.11 oder 3.12
- Neo4j AuraDB-Instanz (kostenloser Tier reicht für Tests)
- OpenAI API-Key
- Optional: LM Studio (für lokalen Betrieb ohne OpenAI)
- Optional: Gamma API Zugang
- Optional: Synthesia API Zugang

---

## 2. Installation

```bash
# 1. Repository klonen
git clone <repo-url>
cd <zum Speicherort>

# 2. Virtuelle Umgebung erstellen und aktivieren
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. spaCy-Modelle installieren
python scripts/install_spacy_models.py
```

> **Hinweis:** `graphrag`, `lightrag-hku` und `pdfplumber` werden nur für den Framework-Vergleich benötigt und sind optional.

---

## 3. Konfiguration (.env)

Die Datei `.env` im Projektroot enthält alle Credentials. Pflichtfelder:

```env
# Neo4j AuraDB
NEO4J_URI=neo4j+s://<deine-instanz>.databases.neo4j.io
NEO4J_USERNAME=<benutzername>
NEO4J_PASSWORD=<passwort>

# OpenAI
OPENAI_API_KEY=sk-proj-...

# LLM-Modus: "cloud" (OpenAI) oder "local" (LM Studio)
LLM_MODE=cloud
```

Alle weiteren Variablen (Gamma, Synthesia, LM Studio, Websuche) sind optional und haben sinnvolle Defaults. Siehe bestehende `.env` für die vollständige Liste.

---

## 4. Datenbank vorbereiten

Einmalig das Neo4j-Schema, entweder in der GUI oder durch folgenden Befehl, anlegen (Constraints und Vektorindizes):

```bash
python scripts/create_schema.py
```

Danach kann der Graph mit PDFs befüllt werden.

---

## 5. PDFs ingestieren

### Via GUI (empfohlen)

Die GUI bietet den komfortabelsten Ingest-Workflow — siehe [Abschnitt 6](#6-gui-verwenden).

### Via CLI

```bash
python scripts/ingest.py pfad/zu/paper1.pdf pfad/zu/paper2.pdf
```

Was dabei passiert:
1. PDF wird geparst (Text, Bilder, Struktur)
2. Embeddings werden berechnet (`text-embedding-3-large`, 3072-D)
3. Paper, Sections, Paragraphs und Figures werden in Neo4j gespeichert
4. Konzepte und semantische Relationen werden extrahiert (NER + LLM)
5. Paragraph→Konzept-Verknüpfungen werden angelegt

---

## 6. GUI verwenden

```bash
streamlit run scripts/gui_app.py
```

Der Browser öffnet sich automatisch unter `http://localhost:8501`.

### Tab-Übersicht

| Tab | Funktion |
|-----|---------|
| **Ingest** | PDFs hochladen und verarbeiten, Fortschrittsanzeige in Echtzeit |
| **Q&A** | Fragen an den Graphen stellen, Antworten mit Quellenbelegen |
| **Kursgenerierung** | Automatisierte Kurse aus dem Graphen erstellen |
| **Paper-Verwaltung** | Ingestionierte Paper anzeigen, Metadaten bearbeiten, Paper löschen |
| **Graph** | Wissensgraph interaktiv visualisieren (Plotly / agraph) |
| **Cypher** | Direkte Cypher-Abfragen an Neo4j |
| **Evaluation** | RAGAS-Evaluation starten und Ergebnisse anzeigen |

### Ingest-Workflow (Schritt für Schritt)

1. Tab **Ingest** öffnen
2. PDFs via Datei-Upload hochladen (mehrere gleichzeitig möglich)
3. Topic eingeben (z.B. `KI-Literacy`)
4. **Ingestieren** klicken — der Fortschrittsbalken zeigt jeden Schritt
5. Nach Abschluss erscheinen die Paper im Tab **Paper-Verwaltung**

### Q&A-Workflow

1. Tab **Q&A** öffnen
2. Frage in das Textfeld eingeben
3. Optional: Websuche aktivieren (Fallback wenn Graph keine Belege findet)
4. **Fragen** klicken
5. Die Antwort erscheint mit Quellenbelegen (`[P123]` = Paragraph, `[F45]` = Figure)

---

## 7. CLI-Befehle

```bash
# Frage stellen und Antwort auf der Konsole ausgeben
python scripts/ask.py "Was ist Transfer Learning?"

# Frage stellen und Antwort als PDF speichern
python scripts/ask_to_pdf.py "Erkläre Transformer-Architekturen."

# Alle Paper neu ingestieren (z.B. nach Schema-Änderung)
python scripts/reingest_all.py

# Datenbankstatus prüfen (Knotenanzahlen, Indizes)
python scripts/diagnose_db.py

# Gesamten Graphen leeren (VORSICHT: unumkehrbar)
python scripts/clear_all.py
```

---

## 8. Kursgenerierung

Die Kursgenerierung ist in den GUI-Tab **Kursgenerierung** integriert.

### Ablauf

1. Tab **Kursgenerierung** öffnen
2. Kursname, Zielgruppe und gewünschte Kapitelanzahl eingeben
3. Optional: Themenfilter setzen (nur bestimmte Paper verwenden)
4. **Kurs generieren** klicken
5. Der Kurs wird mit Kapiteln, Erklärungen und Quellenbelegen generiert
6. Export als **PDF** oder **PowerPoint** möglich

### Präsentationsgenerierung

Im selben Tab können Präsentationen erzeugt werden:
- **Gamma API**: Hochwertige Web-Präsentation (erfordert `GAMMA_API_KEY` + `GAMMA_API_URL` in `.env`)
- **PowerPoint (lokal)**: Kein API-Key nötig, via `python-pptx`

### Videogenerierung

- **Synthesia**: KI-Avatar liest den Kurs vor (erfordert `SYNTHESIA_API_KEY`)
- **Lokal**: Text-to-Speech via `edge-tts` + `moviepy`, kein API-Key nötig

---

## 9. Lokaler Betrieb mit LM Studio

Das System läuft vollständig ohne Cloud-APIs, wenn LM Studio installiert und konfiguriert ist.

### Setup

1. [LM Studio](https://lmstudio.ai) installieren
2. Ein Chat-Modell laden (z.B. `mistral-7b-instruct`)
3. Ein Vision-Modell laden (z.B. `llava-v1.5-7b`)
4. Ein Embedding-Modell laden (z.B. `nomic-embed-text`)
5. Den lokalen Server in LM Studio starten (Standard-Port: 1234)

### .env anpassen

```env
LLM_MODE=local

LMSTUDIO_BASE_URL=http://localhost:1234/v1
LMSTUDIO_CHAT_MODEL=mistral-7b-instruct
LMSTUDIO_VISION_MODEL=llava-v1.5-7b
LMSTUDIO_EMBED_MODEL=nomic-embed-text
LMSTUDIO_EMBED_DIM=768
```

> **Wichtig:** Die Neo4j-Vektorindizes sind auf **3072 Dimensionen** angelegt (für `text-embedding-3-large`). Lokale Embedding-Modelle haben meist 768 Dimensionen. Wenn lokal und cloud gemischt werden, entstehen inkompatible Embeddings. Entweder konsequent einen Modus verwenden oder das Schema mit `python scripts/create_schema.py` neu anlegen.

---

## 10. Projektstruktur

```
masterthesis_neu/
├── src/                          # Kernmodule
│   ├── config.py                 # Zentrale Konfiguration (.env)
│   ├── neo.py                    # Neo4j-Client
│   ├── openai_client.py          # LLM + Embedding + Vision (cloud & lokal)
│   ├── retriever.py              # Hybrid-Retrieval (Graph + Vektor)
│   ├── agent.py                  # Query-Orchestrierung + Websuche
│   ├── pdf_ingest.py             # PDF-Parsing, Chunking, Embedding
│   ├── concept_extract.py        # Konzextextraktion (NER + LLM)
│   ├── entity_relation_extract.py# Entitäten- und Relationsextraktion
│   ├── citation_validator.py     # Zitationsvalidierung
│   ├── ingest_enhanced.py        # Erweiterter Ingest mit Qualitätsprüfung
│   ├── pdf_export.py             # PDF-Export (ReportLab)
│   ├── gamma_client.py           # Gamma-Präsentationsgenerierung
│   ├── synthesia_client.py       # Synthesia-Videogenerierung
│   ├── local_tts_video.py        # Lokale Videogenerierung (edge-tts + moviepy)
│   └── presentation_api.py       # FastAPI REST-Endpunkte (optional)
├── scripts/                      # Einstiegspunkte & Utilities
│   ├── gui_app.py                # Streamlit-GUI (Haupteinstieg)
│   ├── ingest.py                 # CLI-Batch-Ingest
│   ├── ask.py                    # CLI Q&A
│   ├── course_generator.py       # Kursgenerierung (GUI-Plugin)
│   ├── ragas_eval.py             # RAGAS-Evaluation
│   ├── system_comparison.py      # Framework-Vergleich (Adapter-basiert)
│   ├── generate_course_questions.py  # Testfragen generieren
│   ├── create_schema.py          # Datenbankschema anlegen
│   ├── diagnose_db.py            # Datenbankdiagnose
│   └── reingest_all.py           # Alle Paper neu ingestieren
├── data/                         # Laufzeitdaten (in .gitignore)
│   ├── images/                   # Extrahierte Abbildungen
│   ├── eval/                     # RAGAS-Ergebnisse (JSON)
│   ├── uploads/                  # PDFs für Framework-Vergleich
│   ├── graphrag_index/           # MS GraphRAG Index
│   └── lightrag_index/           # LightRAG Index
├── exports/                      # Exportierte Kurse (PDF, PPTX, MP4)
├── .env                          # Credentials (nicht in Git!)
├── requirements.txt              # Python-Abhängigkeiten
└── README.md                     # Diese Datei
```
