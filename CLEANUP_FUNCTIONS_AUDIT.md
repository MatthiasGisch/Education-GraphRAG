# GUI Funktionen Audit - Ingest & Concept Tab + Cypher Tab

## INGEST & KONZEPTE TAB

### AKTIV & SINNVOLL (behalten)

#### Konzept-Extraktion & Konfiguration
- ✅ **Topic-Auswahl** - Wird verwendet zum Zuordnen von Konzepten
  - Auto-Topic aus Titel ableiten
  - Manual Topic-Auswahl
  - Neues Topic erstellen
- ✅ **Konzept-Extraktions-Strategie** (Hybrid vs LLM)
  - Hybrid-Modus: NER + LLM + Semantische Relationen (EMPFOHLEN für wissenschaftliche Texte)
  - LLM-Modus: Nur LLM-basiert
  - Wird bei `ingest_one_pdf_enhanced()` verwendet
- ✅ **SciSpacy Toggle** - Optimiert für wissenschaftliche Texte, wird in Hybrid-Modus verwendet
- ✅ **Dynamische Parameter-Anpassung** - Passt max_entities/max_relations an Dokumentgröße an
- ✅ **Duplikat-Erkennung** - Verhindert doppelte Ingests (SHA256, DOI, Titel)
- ✅ **Qualitätsfilter** - Entfernt Konzepte mit niedriger Confidence
  - Konfigurierbarer Min-Confidence Slider (0.0-1.0)

#### PDF Ingest
- ✅ **PDF Upload** - Hauptfunktion, aktuell in Verwendung
- ✅ **Multi-File Upload** - Mehrere PDFs gleichzeitig
- ✅ **Progress Tracking** 
  - Gesamt-Progress für alle Papers
  - Paper-spezifischer Progress mit Schrittanzeige
  - Validierung der Ingestions-Qualität

#### Metadaten-Kuration (Post-Ingest)
- ✅ **Metadaten-Form nach Ingest** - Ermöglicht Ergänzung fehlender Felder
  - author (komma-getrennt)
  - publication_year
  - doi
  - url
  - source
  - publisher
- ✅ **Validierungs-Zusammenfassung** - Zeigt Quality-Label und Score für jedes Paper

#### Dienstprogramme
- ✅ **Graph leeren** - Löscht alle Knoten mit Bestätigung (DELETE-String)
- ⚠️ **Auto-Stitching Hinweis** - Kommentar sagt es erfolgt automatisch nach Ingest (aber kein Button sichtbar)

---

### NICHT AKTIV oder FRAGWÜRDIG

#### Stitching
- ❓ **`stitch_graph()` Funktion** - NICHT SICHTBAR als Button
  - Definition existiert (Zeile 195): Ruft `neo.stitch_document_hierarchy()` auf
  - Wird NICHT aufgerufen in der GUI
  - Kommentar besagt: "Auto-Stitching erfolgt nun automatisch nach dem Ingest"
  - **Status:** Wahrscheinlich überflüssig, da nicht mehr verwendet

---

## CYPHER TAB

### AKTIV & SINNVOLL (behalten)

#### Query-Ausführung & Visualisierung
- ✅ **Cypher Query Input** - Textfeld für beliebige Abfragen
- ✅ **Parameter Input (JSON)** - Für dynamische Parameterübergabe
- ✅ **Preset-Queries** - 4 vordefinierte Abfragen:
  1. "Topic → Concepts → Paragraphs & Figures" - SINNVOLL
  2. "Alle Paper → Paragraphen" - SINNVOLL
  3. "Papers → ABOUT → Concepts" - FRAGWÜRDIG (ABOUT Relation? wird nicht überall konsistent verwendet)
  4. "Concepts mit SEMANTIC_RELATION" - SINNVOLL

#### Visualisierungs-Engines
- ✅ **Plotly Engine** (empfohlen) - Moderne, bessere Visualisierung
  - Layout-Algorithmen: spring, kamada_kawai, circular, hierarchical, shell
  - Edge-Labels Toggle
  - Färbung nach: type, degree, community
- ✅ **PyVis Engine** (klassisch) - Legacy, aber funktioniert noch
- ✅ **Rohdaten-Export** - Expandable JSON-Ausgabe

#### Diagnose-Tools
- ✅ **Topic/Umbrella/Concept Count Diagnose** - Zeigt:
  - Alle verfügbaren Topics
  - Count von Topic/Umbrella/Concepts
  - Direct Concepts
- ✅ **One-Click Topic→Concepts View** - Schnelle Übersicht

---

### PROBLEMATISCH oder UNGENUTZT

#### Relations-Presets
- ⚠️ **"Papers → ABOUT → Concepts" Preset**
  - Nutzt `ABOUT` Relation mit weight > 0.5
  - **Status:** Relation WIRD tatsächlich in `src/neo.py` erstellt (Zeile 292)
    - In `link_paragraphs_to_concepts()`: `MERGE (p)-[ab:ABOUT]->(c)`
  - Allerdings nur wenn `paper_id` gesetzt (wird nach Ingest gemacht)
  - **Problem:** Nicht alle Papers haben möglicherweise diese Relation erstellt
  - **Empfehlung:** Preset kann bleiben, sollte aber dokumentiert sein

#### Parameter Handling
- ✅ **`_extract_params_from_textarea()`** - Funktion EXISTIERT (Zeile 520)
  - Parsed JSON aus Parameter-Textbox
  - Wird korrekt aufgerufen für Cypher-Abfragen
  - **Status:** Funktioniert einwandfrei

---

## ZUSAMMENFASSUNG

### ZU BEHALTEN (aktiv & nützlich)
1. ✅ **Ingest-Funktion** mit allen Features (hybrid/LLM, duplikat-check, quality-filter)
2. ✅ **Konzept-Extraktion** mit dynamischen Parametern
3. ✅ **Metadaten-Kuration** (neue Funktion, sinnvoll)
4. ✅ **Cypher-Queries** mit Plotly-Visualisierung
5. ✅ **Topic/Umbrella/Concept Diagnostic**
6. ✅ **Graph leeren** mit Bestätigung
7. ✅ **Parameter JSON-Parser** - `_extract_params_from_textarea()`

### ZU ENTFERNEN (nicht verwendet)
1. ❌ **`stitch_graph()` Funktion** 
   - Wird nicht aufgerufen in der GUI
   - Kommentar sagt Auto-Stitching erfolgt automatisch nach Ingest
   - Kann aus GUI entfernt werden (bleibt aber in neo.py für mögliche Scripts)

### KANN BLEIBEN (bereits sinnvoll)
1. ✅ **"Papers → ABOUT → Concepts" Preset** 
   - Relation wird tatsächlich erstellt
   - Sinnvoll für spezifische Analysen
   - Keine Änderung nötig

### POTENZIELLE VEREINFACHUNGEN
1. **PyVis vs Plotly** - Momentan gibt es beide. Plotly ist modern & besser.
   - Option: PyVis-Engine entfernen, nur Plotly behalten
   - Reduces code complexity, nur noch ein Visualisierungs-Engine
   
2. **Umbrella-Struktur** - Verwendet in Cypher-Presets
   - "Topic → Concepts → Paragraphs" nutzt Umbrella-Struktur
   - Sollte dokumentiert werden, ob das noch nötig ist
   
3. **Hybrid vs LLM Strategie** - Beide funktional
   - Hybrid-Modus ist komplexer (NER + LLM + Relationen)
   - LLM-Mode ist einfacher
   - Könnte man auf einen reduzieren, momentan beide brauchbar

### NÄCHSTE SCHRITTE
1. [x] `stitch_graph()` aus GUI entfernt
2. [x] "Dienstprogramme"-Sektion im Tab aufgeräumt
3. [x] PyVis-Engine entfernt, nur Plotly behalten
4. [x] Umbrella-Struktur aus der GUI entfernt
5. [x] `cluster_concepts_into_umbrellas()` Funktion gelöscht
6. [x] Cypher-Presets vereinfacht (nur 3 statt 4)
7. [x] Diagnostic-Button vereinfacht (Topic/Concept statt Topic/Umbrella/Concept)

## DURCHGEFÜHRTE ÄNDERUNGEN

### gui_app.py
- ❌ Import von PyVis entfernt
- ❌ `cluster_concepts_into_umbrellas()` Funktion gelöscht
- ❌ PyVis Radio-Button entfernt
- ❌ Umbrella-ID aus ID_KEYS entfernt
- ❌ Umbrella-Label aus `_guess_label_from_props()` entfernt
- ✅ Cypher-Presets von 4 auf 3 reduziert:
  - "Topic → Concepts → Paragraphs & Figures" (behalten)
  - "Alle Paper → Paragraphen" (behalten)
  - "Concept-Netzwerk (SEMANTIC_RELATION)" (behalten, ABOUT-Relation entfernt)
- ✅ Visualisierungs-Engine-Selection entfernt (nur noch Plotly)
- ✅ Diagnostic-Button vereinfacht (Topic/Concept nur, nicht Umbrella)
- ✅ Umbrella-Attachment im Ingest-Tab deaktiviert

### Verfügbarkeit
- neo.py Module bleiben unverändert (können später auch entfernt werden)
- CLEANUP_FUNCTIONS_AUDIT.md dokumentiert alle Änderungen
