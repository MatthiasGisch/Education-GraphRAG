"""Unit-Tests für src/concept_extract.py: vollständig isoliert von externen APIs, AAA-Muster."""
import pytest
from src import concept_extract as ce


# ===========================================================================
# Stubs & Hilfsfunktionen
# ===========================================================================

class FakeNeo:
    """Minimalstub für Neo4jClient: gibt für Embedding [1.0] einen Treffer auf existing-1 zurück, sonst nichts."""

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
    """Ersetzt _embed durch einen deterministischen Stub: 'existing' → [1.0], sonst → [0.0], kein API-Call."""
    def fake_embed(texts):
        return [[1.0] if "existing" in t.lower() else [0.0] for t in texts]

    monkeypatch.setattr(ce, '_embed', fake_embed)


# ===========================================================================
# Tests: Deduplizierung
# ===========================================================================

def test_dedupe_maps_to_existing(monkeypatch, fake_neo, sample_paragraphs):
    """Prüft dass 'Existing Concept' auf existing-1 gemappt und 'New Interesting' als neues Konzept angelegt wird."""
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
        paper_title="T",
        paragraphs=sample_paragraphs,
        topic_hint="T",
        max_concepts=10,
        seed_names=[],
        allow_new=True,
        neo_client=fake_neo,
        dedupe_threshold=0.9,
        min_confidence=0.0,
    )

    # Link p1 muss explizit auf das bestehende Konzept zeigen
    p1_links = [l for l in links if l["paragraph_id"] == "p1"]
    assert p1_links, "Mindestens ein Link für Paragraph p1 erwartet"
    assert all(l["concept_id"] == "existing-1" for l in p1_links), (
        "p1 muss auf das per Deduplizierung gemappte Konzept 'existing-1' zeigen"
    )

    # 'New Interesting' muss als eigenständiges neues Konzept vorhanden sein
    assert any(c["name"].lower() == "new interesting" for c in concepts), (
        "'New Interesting' muss als neues Konzept im Ergebnis erscheinen"
    )


def test_dedupe_no_duplicate_created(monkeypatch, fake_neo, sample_paragraphs):
    """Prüft dass ein auf ein bestehendes Konzept gemapptes Konzept nicht zusätzlich als Duplikat erscheint."""
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[{"name": "Existing Concept", "alt_labels": [], "description": "desc"}],
        links=[{"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9}],
    ))

    concepts, _ = ce.extract_and_embed_concepts(
        paper_title="T",
        paragraphs=sample_paragraphs,
        neo_client=fake_neo,
        dedupe_threshold=0.9,
        min_confidence=0.0,
    )

    names_lower = [c["name"].lower() for c in concepts]
    assert names_lower.count("existing concept") <= 1, (
        "Nach Deduplizierung darf 'Existing Concept' nur einmal in der Ergebnisliste erscheinen"
    )


@pytest.mark.parametrize("threshold,expect_mapped", [
    (0.90, True),   # score 0.95 ≥ 0.90 → bestehendes Konzept nutzen
    (0.99, False),  # score 0.95 < 0.99 → neues Konzept anlegen
])
def test_dedupe_threshold_boundary(monkeypatch, fake_neo, sample_paragraphs,
                                   threshold, expect_mapped):
    """Prüft dass dedupe_threshold korrekt entscheidet, ob ein Konzept gemappt (≥ Schwelle) oder neu angelegt wird."""
    monkeypatch.setattr(ce, '_ask_llm_for_concepts', _llm_stub(
        concepts=[{"name": "Existing Concept", "alt_labels": [], "description": "d"}],
        links=[{"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9}],
    ))

    concepts, links = ce.extract_and_embed_concepts(
        paper_title="T",
        paragraphs=sample_paragraphs,
        neo_client=fake_neo,
        dedupe_threshold=threshold,
        min_confidence=0.0,
        allow_new=True,
    )

    mapped = any(l.get("concept_id") == "existing-1" for l in links)
    assert mapped == expect_mapped, (
        f"Schwelle {threshold}: Mapping auf 'existing-1' erwartet={expect_mapped}, "
        f"erhalten={mapped}"
    )


# ===========================================================================
# Tests: allow_new=False
# ===========================================================================

def test_allow_new_false_no_extra_concepts(monkeypatch, sample_paragraphs):
    """Prüft dass bei allow_new=False nur Seed-Konzepte im Ergebnis erscheinen und LLM-Extrakonzepte verworfen werden."""
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
        paper_title="T",
        paragraphs=sample_paragraphs,
        seed_names=["AI Basics"],
        allow_new=False,
        min_confidence=0.0,
    )

    names = [c["name"].lower() for c in concepts]
    assert "ai basics" in names, (
        "Das Seed-Konzept 'AI Basics' muss im Ergebnis vorhanden sein"
    )
    assert "neural network" not in names, (
        "allow_new=False: Extrakonzept 'Neural Network' darf nicht im Ergebnis erscheinen"
    )


# ===========================================================================
# Tests: min_confidence-Filter
# ===========================================================================

def test_min_confidence_filters_concepts(monkeypatch, sample_paragraphs):
    """Prüft dass Konzepte mit durchschnittlicher Konfidenz unter min_confidence aus dem Ergebnis gefiltert werden."""
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
        paper_title="T",
        paragraphs=sample_paragraphs,
        allow_new=True,
        min_confidence=0.5,
    )

    names = [c["name"].lower() for c in concepts]
    assert "high conf" in names, (
        "Konzept mit Konfidenz 0.9 ≥ Schwelle 0.5 muss im Ergebnis enthalten sein"
    )
    assert "low conf" not in names, (
        "Konzept mit Konfidenz 0.2 < Schwelle 0.5 muss herausgefiltert werden"
    )


# ===========================================================================
# Tests: _try_parse_llm_response
# ===========================================================================

def test_parse_valid_json():
    """Gültiges JSON wird korrekt deserialisiert."""
    raw = '{"concepts": [{"name": "KI"}], "links": []}'
    result = ce._try_parse_llm_response(raw)
    assert result["concepts"] == [{"name": "KI"}]
    assert result["links"] == []


def test_parse_json_with_trailing_comma():
    """Prüft dass JSON mit trailing commas (typischer LLM-Fehler) repariert und korrekt deserialisiert wird."""
    raw = '{"concepts": [{"name": "KI",}], "links": [],}'
    result = ce._try_parse_llm_response(raw)
    assert isinstance(result, dict)
    assert "concepts" in result


def test_parse_empty_string():
    """Leerer Input liefert ein leeres Ergebnis-Dict ohne Exception."""
    result = ce._try_parse_llm_response("")
    assert result == {"concepts": [], "links": []}


def test_parse_json_in_code_fence():
    """Prüft dass in Markdown-Codeblöcken eingebettetes JSON korrekt extrahiert wird."""
    raw = '```json\n{"concepts": [], "links": [{"paragraph_id": "p1"}]}\n```'
    result = ce._try_parse_llm_response(raw)
    assert result["links"] == [{"paragraph_id": "p1"}]


def test_parse_missing_keys_filled_with_defaults():
    """Prüft dass fehlende 'links'- oder 'concepts'-Schlüssel durch leere Default-Listen ersetzt werden."""
    raw = '{"concepts": [{"name": "Test"}]}'
    result = ce._try_parse_llm_response(raw)
    assert "links" in result
    assert result["links"] == []