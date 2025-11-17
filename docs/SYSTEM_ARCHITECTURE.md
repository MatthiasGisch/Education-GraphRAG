# System-Architektur: GraphRAG Agentensystem

## Übersicht

Dieses System implementiert ein **Multi-Agenten-System** für Retrieval-Augmented Generation (RAG) mit Knowledge Graph-Integration. Die Agenten arbeiten orchestriert zusammen, um wissenschaftliche Dokumente zu analysieren, Wissen zu extrahieren und Fragen didaktisch zu beantworten.

---

## Hauptkomponenten-Diagramm

```mermaid
graph TB
    subgraph "🎯 User Interface Layer"
        GUI[Streamlit GUI<br/>gui_app.py]
        CLI[CLI Scripts<br/>ask.py, ingest.py]
    end

    subgraph "🤖 Agent Orchestration Layer"
        AGENT[Agent Orchestrator<br/>agent.py<br/>• Query Routing<br/>• Tool Selection<br/>• Response Synthesis]
        
        subgraph "Spezialisierte Sub-Agenten"
            INGEST[📄 Ingest Agent<br/>pdf_ingest.py<br/>• PDF Parsing<br/>• Text Extraction<br/>• Image Analysis]
            
            CONCEPT[🧠 Concept Extraction Agent<br/>concept_extract.py<br/>• LLM-basiert<br/>• Hybrid (NER+LLM)<br/>• Semantic Relations]
            
            ENTITY[🔍 Entity & Relation Agent<br/>entity_relation_extract.py<br/>• Named Entity Recognition<br/>• Triplet Extraction<br/>• Co-occurrence Analysis]
            
            RETRIEVAL[🔎 Retrieval Agent<br/>retriever.py<br/>• Vector Search<br/>• Graph Traversal<br/>• Hybrid Retrieval]
            
            ANSWER[💬 Answer Agent<br/>openai_client.py<br/>• Grounded Answers<br/>• Didactic Explanations<br/>• Citation Generation]
        end
    end

    subgraph "🗄️ Knowledge Storage Layer"
        NEO[Neo4j Graph DB<br/>neo.py<br/>• Papers, Paragraphs<br/>• Concepts, Relations<br/>• Vector Indexes]
        
        VECTOR[Vector Embeddings<br/>OpenAI ada-002<br/>• Text Embeddings<br/>• Semantic Search]
    end

    subgraph "🔧 External Services"
        OPENAI[OpenAI API<br/>• GPT-4o<br/>• Embeddings<br/>• Vision]
        
        GAMMA[Gamma API<br/>gamma_client.py<br/>• Presentation Gen]
        
        SYNTH[Synthesia API<br/>synthesia_client.py<br/>• Video Gen]
    end

    %% Connections
    GUI --> AGENT
    CLI --> AGENT
    
    AGENT --> INGEST
    AGENT --> CONCEPT
    AGENT --> RETRIEVAL
    AGENT --> ANSWER
    
    INGEST --> NEO
    INGEST --> OPENAI
    
    CONCEPT --> ENTITY
    CONCEPT --> NEO
    CONCEPT --> OPENAI
    
    ENTITY --> OPENAI
    ENTITY --> NEO
    
    RETRIEVAL --> NEO
    RETRIEVAL --> VECTOR
    
    ANSWER --> OPENAI
    ANSWER --> RETRIEVAL
    
    NEO --> VECTOR
    
    AGENT --> GAMMA
    AGENT --> SYNTH

    style AGENT fill:#ff6b6b,stroke:#333,stroke-width:3px,color:#fff
    style INGEST fill:#4ecdc4,stroke:#333,stroke-width:2px
    style CONCEPT fill:#4ecdc4,stroke:#333,stroke-width:2px
    style ENTITY fill:#4ecdc4,stroke:#333,stroke-width:2px
    style RETRIEVAL fill:#4ecdc4,stroke:#333,stroke-width:2px
    style ANSWER fill:#4ecdc4,stroke:#333,stroke-width:2px
```

---

## Detaillierter Ablauf: PDF Ingest

```mermaid
sequenceDiagram
    actor User
    participant GUI as Streamlit GUI
    participant Ingest as Ingest Agent
    participant Vision as OpenAI Vision
    participant Entity as Entity Agent
    participant Concept as Concept Agent
    participant Neo4j as Knowledge Graph
    
    User->>GUI: Upload PDF
    GUI->>Ingest: ingest_one_pdf(path)
    
    Note over Ingest: Phase 1: Extraktion
    Ingest->>Ingest: PDF Text extrahieren
    Ingest->>Ingest: Bilder extrahieren
    Ingest->>Vision: Bilder analysieren (GPT-4o)
    Vision-->>Ingest: Bildbeschreibungen
    
    Note over Ingest: Phase 2: Embeddings
    Ingest->>Ingest: Paragraphen embedden
    Ingest->>Ingest: Bilder embedden
    
    Note over Ingest: Phase 3: Persistierung
    Ingest->>Neo4j: Paper, Sections speichern
    Ingest->>Neo4j: Paragraphs mit paper_id speichern
    Ingest->>Neo4j: Figures speichern
    
    Note over Ingest: Phase 4: Konzept-Extraktion
    alt Hybrid-Modus
        Ingest->>Entity: extract_entities_and_relations()
        Entity->>Entity: NER (spaCy + SciSpacy)
        Entity->>Entity: LLM Concept Extraction
        Entity->>Entity: Relation Extraction
        Entity-->>Concept: Entities + Relations
        
        Concept->>Concept: Format zu Concepts
        Concept->>Concept: Paragraph-Linking
        Concept->>Neo4j: persist_to_topic=True
        Neo4j->>Neo4j: CREATE Concepts
        Neo4j->>Neo4j: CREATE MENTIONS Relations
        Neo4j->>Neo4j: CREATE SEMANTIC_RELATION
    else LLM-Modus
        Ingest->>Concept: extract_and_embed_concepts()
        Concept->>Concept: LLM Extraction
        Concept->>Neo4j: Concepts + Links speichern
    end
    
    Ingest-->>GUI: Status: Erfolgreich
    GUI-->>User: Ingest abgeschlossen
```

---

## Detaillierter Ablauf: Query Processing

```mermaid
sequenceDiagram
    actor User
    participant GUI as Streamlit GUI
    participant Agent as Agent Orchestrator
    participant Retriever as Retrieval Agent
    participant Neo4j as Knowledge Graph
    participant Answer as Answer Agent
    participant LLM as OpenAI GPT-4o
    
    User->>GUI: Stelle Frage
    GUI->>Agent: answer_question(query)
    
    Note over Agent: Phase 1: Query Analysis
    Agent->>Agent: Analysiere Query-Intent
    Agent->>Agent: Bestimme Retrieval-Strategie
    
    Note over Agent: Phase 2: Retrieval
    Agent->>Retriever: retrieve_context(query, topic)
    
    par Hybrid Retrieval
        Retriever->>Neo4j: Vector Search (Paragraphs)
        Neo4j-->>Retriever: Top-K Paragraphs
    and
        Retriever->>Neo4j: Graph Traversal (Concepts)
        Neo4j-->>Retriever: Related Concepts + Relations
    and
        Retriever->>Neo4j: Figure Retrieval
        Neo4j-->>Retriever: Relevant Figures
    end
    
    Retriever->>Retriever: Deduplizierung & Ranking
    Retriever-->>Agent: Unified Context
    
    Note over Agent: Phase 3: Answer Generation
    Agent->>Answer: grounded_answer(query, context)
    Answer->>Answer: Format Context (Teacher Perspective)
    Answer->>LLM: Generate Answer mit Citations
    LLM-->>Answer: Didactic Answer + Sources
    
    Answer->>Answer: Post-processing
    Answer->>Answer: Belege hinzufügen
    Answer-->>Agent: Final Answer
    
    Note over Agent: Phase 4: Optional Extensions
    opt Presentation erstellen
        Agent->>Agent: to_gamma_input_text()
        Agent->>Agent: generate_presentation()
    end
    
    opt Video erstellen
        Agent->>Agent: generate_video_from_pptx()
    end
    
    Agent-->>GUI: Complete Response
    GUI-->>User: Antwort mit Belegen
```

---

## Konzept-Extraktion: Hybrid-Agent Architektur

```mermaid
flowchart TB
    START([PDF Text]) --> SPLIT{Strategie?}
    
    SPLIT -->|LLM-Modus| LLM_AGENT[🤖 LLM Agent<br/>extract_and_embed_concepts]
    SPLIT -->|Hybrid-Modus| HYBRID_AGENT[🤖 Hybrid Agent<br/>extract_and_embed_concepts_hybrid]
    
    subgraph "LLM Agent Pipeline"
        LLM_AGENT --> LLM_EXTRACT[LLM Concept Extraction]
        LLM_EXTRACT --> LLM_EMBED[Embedding Generation]
        LLM_EMBED --> LLM_DEDUP[Deduplication<br/>Similarity > 0.92]
        LLM_DEDUP --> LLM_PERSIST[Persist to Neo4j]
    end
    
    subgraph "Hybrid Agent Pipeline"
        HYBRID_AGENT --> NER_AGENT[🔍 NER Sub-Agent<br/>extract_entities_ner]
        HYBRID_AGENT --> LLM_SUB[🧠 LLM Sub-Agent<br/>extract_concepts_llm]
        
        NER_AGENT --> NER_SPACY[spaCy NER]
        NER_AGENT --> NER_SCI[SciSpacy NER]
        
        NER_SPACY --> FILTER[🔧 Filter Agent<br/>• Min 3 chars<br/>• No numbers<br/>• Relevant types only]
        NER_SCI --> FILTER
        
        LLM_SUB --> LLM_CONCEPTS[Abstract Concepts<br/>Methodologies<br/>Theories]
        
        FILTER --> MERGE[Merge & Deduplicate]
        LLM_CONCEPTS --> MERGE
        
        MERGE --> REL_AGENT[🔗 Relation Agent<br/>extract_relations_llm]
        REL_AGENT --> REL_TRIPLETS[Semantic Triplets<br/>Subject-Predicate-Object]
        
        REL_TRIPLETS --> COOC_AGENT[📊 Co-occurrence Agent<br/>analyze_cooccurrence]
        COOC_AGENT --> COOC_STATS[Statistical Relations<br/>Count & Strength]
        
        COOC_STATS --> HYBRID_EMBED[Embedding Generation]
        HYBRID_EMBED --> HYBRID_LINK[Paragraph Linking]
        HYBRID_LINK --> HYBRID_PERSIST[Persist Everything<br/>• Concepts<br/>• MENTIONS<br/>• SEMANTIC_RELATION<br/>• CO_OCCURS_WITH]
    end
    
    LLM_PERSIST --> RESULT([Knowledge Graph])
    HYBRID_PERSIST --> RESULT
    
    style HYBRID_AGENT fill:#ff6b6b,stroke:#333,stroke-width:3px
    style NER_AGENT fill:#4ecdc4,stroke:#333,stroke-width:2px
    style LLM_SUB fill:#4ecdc4,stroke:#333,stroke-width:2px
    style REL_AGENT fill:#4ecdc4,stroke:#333,stroke-width:2px
    style COOC_AGENT fill:#4ecdc4,stroke:#333,stroke-width:2px
    style FILTER fill:#95e1d3,stroke:#333,stroke-width:1px
```

---

## Datenfluss im Knowledge Graph

```mermaid
graph LR
    subgraph "📚 Document Level"
        PAPER[Paper]
        SEC[Section]
        FIG[Figure]
    end
    
    subgraph "📝 Content Level"
        PARA[Paragraph<br/>+ embedding<br/>+ paper_id]
    end
    
    subgraph "💡 Knowledge Level"
        TOPIC[Topic]
        CONCEPT[Concept<br/>+ embedding<br/>+ type<br/>+ source]
    end
    
    subgraph "🔗 Relation Level"
        SEM_REL[SEMANTIC_RELATION<br/>+ relation_type<br/>+ confidence<br/>+ context]
        COOC_REL[CO_OCCURS_WITH<br/>+ count<br/>+ strength]
    end
    
    %% Document Structure
    PAPER -->|HAS_SECTION| SEC
    PAPER -->|HAS_PARAGRAPH| PARA
    PAPER -->|HAS_FIGURE| FIG
    SEC -->|HAS_PARAGRAPH| PARA
    SEC -->|HAS_FIGURE| FIG
    
    %% Content to Knowledge
    PARA -->|MENTIONS| CONCEPT
    FIG -->|DEPICTS| CONCEPT
    
    %% Knowledge Organization
    TOPIC -->|HAS_CONCEPT| CONCEPT
    
    %% Knowledge Relations
    CONCEPT -->|SEMANTIC_RELATION| CONCEPT
    CONCEPT -->|CO_OCCURS_WITH| CONCEPT
    
    style PAPER fill:#e8f4f8,stroke:#333,stroke-width:2px
    style CONCEPT fill:#ffe66d,stroke:#333,stroke-width:2px
    style SEM_REL fill:#ff6b6b,stroke:#333,stroke-width:2px
    style COOC_REL fill:#4ecdc4,stroke:#333,stroke-width:2px
```

---

## Agent-Entscheidungsbaum

```mermaid
flowchart TD
    START([User Request]) --> INTENT{Intent<br/>Detection}
    
    INTENT -->|Frage| QUERY_AGENT[🤖 Query Agent]
    INTENT -->|Ingest| INGEST_AGENT[🤖 Ingest Agent]
    INTENT -->|Presentation| PRES_AGENT[🤖 Presentation Agent]
    INTENT -->|Video| VIDEO_AGENT[🤖 Video Agent]
    
    QUERY_AGENT --> TOPIC_CHECK{Topic<br/>vorhanden?}
    TOPIC_CHECK -->|Ja| RETRIEVE[Retrieve from Graph]
    TOPIC_CHECK -->|Nein| FALLBACK[Web Search Fallback]
    
    RETRIEVE --> CONTEXT_BUILD[Context Building]
    FALLBACK --> CONTEXT_BUILD
    
    CONTEXT_BUILD --> EVIDENCE_CHECK{Belege<br/>gefunden?}
    EVIDENCE_CHECK -->|Ja| ANSWER_GEN[Answer Generation<br/>mit Citations]
    EVIDENCE_CHECK -->|Nein| ANSWER_FALLBACK[General Answer<br/>ohne Citations]
    
    INGEST_AGENT --> FORMAT_CHECK{Format<br/>Check}
    FORMAT_CHECK -->|PDF| PDF_PROCESS[PDF Processing]
    FORMAT_CHECK -->|Other| ERROR[Error: Unsupported]
    
    PDF_PROCESS --> EXTRACT_DECISION{Extraction<br/>Strategy?}
    EXTRACT_DECISION -->|Hybrid| HYBRID_EXTRACT[NER + LLM + Relations]
    EXTRACT_DECISION -->|LLM| LLM_EXTRACT[LLM Only]
    
    HYBRID_EXTRACT --> PERSIST[Persist to Neo4j]
    LLM_EXTRACT --> PERSIST
    
    PRES_AGENT --> GAMMA_CHECK{Gamma<br/>verfügbar?}
    GAMMA_CHECK -->|Ja| GAMMA_GEN[Gamma Generation]
    GAMMA_CHECK -->|Nein| PPTX_GEN[Local PPTX<br/>mit Font Controls]
    
    VIDEO_AGENT --> SYNTH_CHECK{Synthesia<br/>verfügbar?}
    SYNTH_CHECK -->|Ja| VIDEO_GEN[Video Generation]
    SYNTH_CHECK -->|Nein| ERROR2[Error: No Service]
    
    ANSWER_GEN --> OUTPUT([Response])
    ANSWER_FALLBACK --> OUTPUT
    PERSIST --> OUTPUT
    GAMMA_GEN --> OUTPUT
    PPTX_GEN --> OUTPUT
    VIDEO_GEN --> OUTPUT
    ERROR --> OUTPUT
    ERROR2 --> OUTPUT
    
    style QUERY_AGENT fill:#ff6b6b,stroke:#333,stroke-width:3px
    style INGEST_AGENT fill:#ff6b6b,stroke:#333,stroke-width:3px
    style PRES_AGENT fill:#ff6b6b,stroke:#333,stroke-width:3px
    style VIDEO_AGENT fill:#ff6b6b,stroke:#333,stroke-width:3px
```

---

## Key Features des Agentensystems

### 🎯 Orchestrierung (agent.py)
- **Zentrale Koordination**: Verwaltet alle Sub-Agenten
- **Context-Aware Routing**: Leitet Anfragen basierend auf Intent
- **Tool Selection**: Wählt die richtigen Tools/Agenten für die Aufgabe

### 🤖 Spezialisierte Agenten

1. **Ingest Agent** (pdf_ingest.py)
   - Verantwortlich für: PDF-Parsing, Text-/Bildextraktion
   - Nutzt: PyMuPDF, OpenAI Vision

2. **Concept Extraction Agent** (concept_extract.py)
   - Verantwortlich für: Konzept-Identifikation, Embedding
   - Modi: LLM-only oder Hybrid (delegiert an Entity Agent)

3. **Entity & Relation Agent** (entity_relation_extract.py)
   - Verantwortlich für: NER, Triplet-Extraktion, Co-occurrence
   - Nutzt: spaCy, SciSpacy, OpenAI GPT-4o
   - Filter: Qualitätskontrolle für wissenschaftliche Konzepte

4. **Retrieval Agent** (retriever.py)
   - Verantwortlich für: Hybrid Retrieval (Vector + Graph)
   - Strategien: Similarity Search, Graph Traversal, Figure Retrieval

5. **Answer Agent** (openai_client.py)
   - Verantwortlich für: Didaktische Antwortgenerierung
   - Perspektive: "Erfahrener Dozent und Lehrer"
   - Output: Strukturierte Erklärungen mit Belegen

### 🧠 Intelligente Features

- **Adaptive Retrieval**: Wählt beste Strategie basierend auf Query
- **Multi-Modal**: Text + Bilder + Relationen
- **Quality Control**: Filter für wissenschaftliche Relevanz
- **Didactic Synthesis**: Lern-orientierte Antworten
- **Citation Generation**: Automatische Quellenangaben

---

## Technologie-Stack

```mermaid
mindmap
  root((GraphRAG<br/>Agent System))
    Frontend
      Streamlit GUI
      CLI Scripts
    Agents
      Orchestrator (agent.py)
      Specialized Sub-Agents
    NLP/ML
      OpenAI GPT-4o
      OpenAI Embeddings
      spaCy NER
      SciSpacy
    Storage
      Neo4j AuraDB
      Vector Indexes
      Graph Relations
    Integration
      Gamma API
      Synthesia API
      Web Search (optional)
```

---

## Deployment & Ausführung

### Lokale Entwicklung
```bash
# Starte GUI (alle Agenten verfügbar)
streamlit run scripts/gui_app.py

# Direkter Agent-Aufruf (CLI)
python scripts/ask.py "Deine Frage"
python scripts/ingest.py pfad/zur/datei.pdf
```

### Agent-Konfiguration
- **Topic**: Bestimmt Knowledge Domain
- **Strategy**: LLM vs. Hybrid Extraction
- **Retrieval**: Vector + Graph Hybrid
- **Teacher Mode**: Didaktische Perspektive

---

## Zusammenfassung

Dieses System implementiert ein **hierarchisches Multi-Agenten-System**:

1. **Orchestrator-Agent** koordiniert alle Operationen
2. **Spezialisierte Sub-Agenten** führen spezifische Aufgaben aus
3. **Knowledge Graph** als zentraler Wissensspeicher
4. **Hybrid Retrieval** kombiniert Vector + Graph Search
5. **Didactic Answer Generation** mit Teacher Perspective

Die Agenten arbeiten **autonom** in ihren Domänen, werden aber **orchestriert** vom Hauptagenten für komplexe Workflows.
