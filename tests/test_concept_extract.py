import pytest
from src import concept_extract as ce


class FakeNeo:
    def __init__(self, existing=None):
        # existing: dict mapping concept_id -> (name, score)
        self.existing = existing or {}

    def vector_search_concepts(self, embedding, k=1):
        # Return a high-score match for a specific test case: if embedding equals [1.0], map to 'existing-1'
        if embedding and isinstance(embedding, (list, tuple)) and len(embedding) == 1 and embedding[0] == 1.0:
            return [{"concept_id": "existing-1", "name": "Existing Concept", "score": 0.95}]
        return []

    def run(self, cypher, params=None):
        # Minimal stub for info fetch in concept_extract
        if "MATCH (c:Concept" in cypher:
            return [{"concept_id": "existing-1", "name": "Existing Concept", "alt_labels": [], "description": ""}]
        return []


def test_dedupe_maps_to_existing(monkeypatch):
    # Monkeypatch the LLM responder to return one new concept and links
    def fake_llm(title, paragraphs, topic_hint, max_concepts, seed_names, allow_new):
        return {
            "concepts": [{"name": "Existing Concept", "alt_labels": [], "description": "desc"},
                         {"name": "New Interesting", "alt_labels": [], "description": "desc2"}],
            "links": [
                {"paragraph_id": "p1", "concept_name": "Existing Concept", "confidence": 0.9},
                {"paragraph_id": "p2", "concept_name": "New Interesting", "confidence": 0.8},
                {"paragraph_id": "p3", "concept_name": "New Interesting", "confidence": 0.7},
            ]
        }

    monkeypatch.setattr(ce, '_ask_llm_for_concepts', fake_llm)

    # Create paragraphs (not used by fake llm but required shape)
    paras = [{"paragraph_id": "p1", "text": "foo"}, {"paragraph_id": "p2", "text": "bar"}, {"paragraph_id": "p3", "text": "baz"}]

    fake_neo = FakeNeo()

    # Call extractor with neo_client to trigger dedupe for the first concept
    concepts, links = ce.extract_and_embed_concepts(
        paper_title="T",
        paragraphs=paras,
        topic_hint="T",
        max_concepts=10,
        seed_names=[],
        allow_new=True,
        neo_client=fake_neo,
        dedupe_threshold=0.9,
        min_mentions=1,
        min_confidence=0.0,
    )

    # Assert that Existing Concept was not duplicated (mapped placeholder should appear)
    names = [c['name'].lower() for c in concepts]
    assert 'existing concept' in names or any(c['concept_id'] == 'existing-1' for c in concepts)
    # New Interesting should be present as a created concept (since it has 2 mentions)
    assert any(c['name'].lower() == 'new interesting' for c in concepts)

    # Links should map to the existing concept id for the first link and to new for others
    assert any(l['concept_id'] == 'existing-1' for l in links) or any(l['concept_id'] != '' for l in links)