"""RAGAS-Evaluation der GraphRAG-Pipeline: Faithfulness, Answer Relevancy, Context Precision und Context Recall."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openai import OpenAI as _OpenAI
from datasets import Dataset
from langchain_openai import OpenAIEmbeddings as _LCOpenAIEmbeddings
from ragas import evaluate
from ragas.llms import llm_factory
from ragas.metrics._faithfulness import Faithfulness
from ragas.metrics._answer_relevance import AnswerRelevancy
from ragas.metrics._context_precision import ContextPrecision
from ragas.metrics._context_recall import ContextRecall

from src import config as cfg
from src.neo import Neo4jClient
from src.openai_client import grounded_answer
from src.retriever import concept_based_retrieve, hybrid_retrieve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Testdatensatz-Struktur
# ---------------------------------------------------------------------------

@dataclass
class TestQuestion:
    question: str
    ground_truth: str
    question_type: str  # "factual" | "cross_topic" | "visual" | "false_context"
    kurs_id: str | None = None  # gesetzt bei Kursfragen, None bei DEFAULT_TEST_QUESTIONS


DEFAULT_TEST_QUESTIONS: list[TestQuestion] = [
    # Faktische Einzelfragen (5x)
    TestQuestion(
        "Was ist ein Transformer in der NLP?",
        "Ein Transformer ist eine neuronale Netzarchitektur basierend auf dem Self-Attention-Mechanismus, vorgestellt von Vaswani et al. (2017) in 'Attention is All You Need'.",
        "factual",
    ),
    TestQuestion(
        "Wie funktioniert der Self-Attention-Mechanismus?",
        "Self-Attention berechnet für jedes Token eine gewichtete Summe aller anderen Token, wobei die Gewichte durch skalierte Query-Key-Skalarprodukte bestimmt werden.",
        "factual",
    ),
    TestQuestion(
        "Was versteht man unter Retrieval-Augmented Generation (RAG)?",
        "RAG kombiniert einen Retrieval-Schritt (Dokumentsuche) mit einem generativen LLM, das die abgerufenen Dokumente als Kontext für die Antwortgenerierung nutzt.",
        "factual",
    ),
    TestQuestion(
        "Was ist ein Knowledge Graph?",
        "Ein Knowledge Graph repräsentiert Wissen als Knoten (Entitäten) und Kanten (Relationen) in einem Graphen und ermöglicht strukturierte Wissensabfragen.",
        "factual",
    ),
    TestQuestion(
        "Was bedeutet Named Entity Recognition (NER)?",
        "NER ist eine Aufgabe der Informationsextraktion, bei der benannte Entitäten wie Personen, Orte und Konzepte im Text identifiziert und klassifiziert werden.",
        "factual",
    ),
    # Themenübergreifende Fragen (5x)
    TestQuestion(
        "Wie hängen Attention-Mechanismus und GraphRAG zusammen?",
        "GraphRAG nutzt Graphstrukturen für das Retrieval, während der Attention-Mechanismus im LLM die Relevanz der abgerufenen Kontexte bei der Generierung gewichtet.",
        "cross_topic",
    ),
    TestQuestion(
        "Welche Vorteile hat GraphRAG gegenüber klassischem RAG?",
        "GraphRAG ermöglicht strukturiertes Retrieval über Relationen zwischen Entitäten, was bei vernetzten Wissensdomänen präzisere Ergebnisse liefert als rein vektorbasiertes RAG.",
        "cross_topic",
    ),
    TestQuestion(
        "Wie verbessern Embeddings die Qualität von Wissensrepräsentationen in Graphen?",
        "Embeddings kodieren semantische Ähnlichkeit in Vektoren; in Graphen erlauben sie vektorbasierte Suche über Knoten hinaus, die über strukturelle Kanten semantisch verwandt sind.",
        "cross_topic",
    ),
    TestQuestion(
        "Wie unterscheiden sich parametrisches und nicht-parametrisches Wissen in LLMs?",
        "Parametrisches Wissen ist in den Modellgewichten gespeichert; nicht-parametrisches Wissen wird zur Inferenzzeit durch Retrieval bereitgestellt, wie bei RAG-Systemen.",
        "cross_topic",
    ),
    TestQuestion(
        "Welche Rolle spielt Neo4j in einer GraphRAG-Pipeline?",
        "Neo4j dient als persistenter Graph-Speicher für Entitäten und Relationen und ermöglicht Cypher-basierte sowie vektorbasierte Abfragen für das Retrieval.",
        "cross_topic",
    ),
    # Fragen mit visuellen Inhalten (3x)
    TestQuestion(
        "Erkläre die typische Architektur eines Transformer-Modells anhand einer Abbildung.",
        "Ein Transformer besteht aus Encoder- und Decoder-Blöcken mit Multi-Head-Attention-Schichten, positionsweisen Feed-Forward-Netzwerken und Layer-Normalisierung.",
        "visual",
    ),
    TestQuestion(
        "Beschreibe den Aufbau einer RAG-Pipeline wie in wissenschaftlichen Arbeiten dargestellt.",
        "Eine RAG-Pipeline besteht typischerweise aus Indexierungsphase, Retrieval-Komponente (Ähnlichkeitssuche) und Generierungskomponente (LLM mit Kontext-Augmentierung).",
        "visual",
    ),
    TestQuestion(
        "Wie wird die Attention-Matrix in Self-Attention visualisiert?",
        "Die Attention-Matrix zeigt als Heatmap, welche Token-Paare hohe Aufmerksamkeitswerte erhalten; diagonale Muster deuten auf lokale, breitere Muster auf globale Abhängigkeiten hin.",
        "visual",
    ),
    # Fragen für Halluzinationstest (2x)
    TestQuestion(
        "Was ist BERT und wie unterscheidet es sich von GPT?",
        "BERT ist ein bidirektionales Transformer-Modell von Google (Devlin et al., 2019), das durch Masked Language Modeling vortrainiert wird, während GPT autoregressive Vorhersage verwendet.",
        "false_context",
    ),
    TestQuestion(
        "Wie funktioniert das GPT-Sprachmodell?",
        "GPT ist ein autoregressive Sprachmodell, das auf dem Transformer-Decoder basiert und Text token-für-token von links nach rechts durch kausale Selbst-Attention generiert.",
        "false_context",
    ),
]

# Falschinformationen für den Halluzinationstest — pro Kurs thematisch passend
_FALSE_FACTS_BY_KURS: dict[str, list[str]] = {
    "kurs1_ai_literacy_kmu": [
        "Generative KI-Systeme wie ChatGPT sind vollständig zuverlässig, da sie ausschließlich aus verifizierten Quellen lernen und keine falschen Informationen produzieren können.",
        "Machine-Learning-Modelle lernen kontinuierlich aus jeder Nutzereingabe weiter, weshalb Unternehmen nach der Einführung keine weiteren Trainingsmaßnahmen oder Kontrollen benötigen.",
        "KI-Systeme treffen Entscheidungen nach denselben logischen Prinzipien wie menschliche Experten und sind daher in allen Unternehmensbereichen ohne menschliche Aufsicht einsetzbar.",
        "Für den Einsatz von KI-Anwendungen in KMU ist kein gesondertes Datenschutzkonzept erforderlich, da trainierte Sprachmodelle keine personenbezogenen Daten speichern oder verarbeiten.",
        "Große Sprachmodelle wie GPT-4 verstehen Sprache im selben Sinne wie Menschen — sie besitzen echtes semantisches Verständnis und handeln auf Basis von Intentionen und Bedeutungen.",
    ],
    "kurs2_ki_strategie_governance": [
        "Laut EU AI Act sind ausschließlich die Hersteller (Anbieter) von KI-Systemen rechtlich verantwortlich; Unternehmen, die KI einsetzen (Nutzer/Deployer), tragen keinerlei Compliance-Pflichten.",
        "KI-Governance bedeutet, dass Unternehmen sämtliche KI-Entscheidungen vollständig automatisieren und auf menschliche Kontrolle verzichten können, sofern das System zertifiziert ist.",
        "Die Entwicklung einer KI-Strategie ist ausschließlich Aufgabe der IT-Abteilung; Unternehmensführung und Vorstand müssen keine strategischen KI-Entscheidungen treffen, da es sich um ein rein technisches Thema handelt.",
        "Ethische KI-Leitlinien sind für privatwirtschaftliche Unternehmen freiwillig und haben nachweislich keinen Einfluss auf Unternehmensreputation, Kundenbindung oder wirtschaftlichen Erfolg.",
        "Transparenzpflichten für KI-Algorithmen gelten im Rahmen der KI-Governance ausschließlich für öffentliche Behörden und staatliche Institutionen; privatwirtschaftliche Unternehmen sind vollständig davon ausgenommen.",
    ],
    "kurs3_ki_recht_eu_ai_act": [
        "Der EU AI Act teilt KI-Systeme in zwei Risikoklassen ein: 'sicher' und 'gefährlich', wobei alle KI-Systeme im unternehmerischen Einsatz automatisch als hochriskant eingestuft werden.",
        "Kleine und mittlere Unternehmen (KMU) sind vollständig vom EU AI Act ausgenommen, da das Gesetz ausschließlich für Konzerne und Großunternehmen mit mehr als 500 Mitarbeitenden gilt.",
        "Der EU AI Act ist seit Januar 2024 vollständig in Kraft getreten; alle Unternehmen müssen sämtliche Anforderungen aller Risikoklassen ab sofort ohne Übergangsfrist erfüllen.",
        "Hochrisiko-KI-Systeme dürfen nach einmaliger Konformitätsbewertung dauerhaft ohne weitere Überprüfung betrieben werden, da die Zertifizierung unbefristet und ohne Rezertifizierungspflicht gilt.",
        "Natürliche Personen haben unter dem EU AI Act kein Recht, automatisierte KI-Entscheidungen anzufechten oder eine menschliche Überprüfung zu verlangen — das Gesetz sieht ausschließlich Pflichten für Unternehmen vor.",
    ],
}
# Fallback für DEFAULT_TEST_QUESTIONS (kein kurs_id)
_FALSE_FACTS = [f for facts in _FALSE_FACTS_BY_KURS.values() for f in facts]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _build_neo_client() -> Neo4jClient:
    """Erstellt einen Neo4jClient mit den Standard-Umgebungsvariablen."""
    return Neo4jClient()


def _retrieve_supports(neo: Neo4jClient, question: str) -> list[dict]:
    """Retrieval immer mit aktuellem LLM_MODE – Aufrufer muss cfg.LLM_MODE sicherstellen."""
    result = concept_based_retrieve(neo, question)
    return result.get("supports", [])


def _supports_to_contexts(supports: list[dict]) -> list[str]:
    """Extrahiert die Texte aller Paragraph-Supports als Kontextliste für RAGAS."""
    return [s["text"] for s in supports if s.get("type") == "paragraph" and s.get("text")]


def _make_ragas_llm():
    """Erstellt den RAGAS-LLM-Judge (gpt-4o-mini) mit ausreichendem max_tokens-Limit für Faithfulness-NLI."""
    # 8192 tokens: Faithfulness-NLI erzeugt ~200 Output-Tokens pro Aussage; sichere Obergrenze für lange Antworten
    return llm_factory("gpt-4o-mini", client=_OpenAI(api_key=cfg.OPENAI_API_KEY), max_tokens=8192)


def _make_ragas_embeddings():
    """Erstellt LangChain-Embeddings für RAGAS AnswerRelevancy (embed_query/embed_documents Interface)."""
    return _LCOpenAIEmbeddings(api_key=cfg.OPENAI_API_KEY, model="text-embedding-3-large")


def _make_metrics(llm, emb) -> list:
    """Erstellt die vier RAGAS-Standardmetriken mit dem angegebenen LLM und Embedding-Modell."""
    return [
        Faithfulness(llm=llm),
        AnswerRelevancy(llm=llm, embeddings=emb, strictness=1),
        ContextPrecision(llm=llm),
        ContextRecall(llm=llm),
    ]


_NON_METRIC_COLS = {
    "question", "answer", "contexts", "ground_truth",
    "user_input", "response", "retrieved_contexts", "reference",
}

# Lange Kursantworten auf ~400 Wörter kappen, damit Faithfulness-NLI nicht
# mit 30+ Aussagen überflutet wird und der max_tokens-Limit sicher eingehalten wird.
_ANSWER_MAX_CHARS = 2000


def _truncate_answer(text: str) -> str:
    """Kürzt lange Antworten satzweise auf _ANSWER_MAX_CHARS Zeichen für das Faithfulness-NLI."""
    if len(text) <= _ANSWER_MAX_CHARS:
        return text
    cut = text[:_ANSWER_MAX_CHARS]
    last_period = max(cut.rfind(". "), cut.rfind(".\n"))
    return cut[: last_period + 1] if last_period > _ANSWER_MAX_CHARS // 2 else cut


def _run_ragas(rows: dict, llm, emb, metrics: list) -> dict:
    """Führt RAGAS-Evaluation auf einem Zeilen-Dict aus und gibt gemittelte Metrik-Scores zurück."""
    if not rows.get("question"):
        return {}
    rows = {**rows, "answer": [_truncate_answer(a) for a in rows["answer"]]}
    ds = Dataset.from_dict(rows)
    result = evaluate(ds, metrics=metrics)
    # RAGAS 0.4.x gibt EvaluationResult zurück (kein dict), Scores via to_pandas()
    df = result.to_pandas()
    out = {}
    for col in df.columns:
        if col in _NON_METRIC_COLS:
            continue
        series = df[col].dropna()
        if series.empty:
            continue
        try:
            out[col] = float(series.mean())
        except (TypeError, ValueError):
            pass
    return out


# ---------------------------------------------------------------------------
# 1. RAGAS-Evaluation
# ---------------------------------------------------------------------------

def run_ragas_evaluation(
    test_questions: list[TestQuestion],
    neo: Neo4jClient | None = None,
) -> dict:
    """Führt alle vier RAGAS-Metriken über den vollständigen Testdatensatz aus."""
    if neo is None:
        neo = _build_neo_client()

    ragas_llm = _make_ragas_llm()
    ragas_emb = _make_ragas_embeddings()
    metrics = _make_metrics(ragas_llm, ragas_emb)

    rows: dict[str, list] = {"question": [], "answer": [], "contexts": [], "ground_truth": []}

    for tq in test_questions:
        try:
            supports = _retrieve_supports(neo, tq.question)
            answer = grounded_answer(tq.question, supports)
            contexts = _supports_to_contexts(supports)
            rows["question"].append(tq.question)
            rows["answer"].append(answer)
            rows["contexts"].append(contexts or [""])
            rows["ground_truth"].append(tq.ground_truth)
        except Exception as e:
            log.error("Frage übersprungen '%s': %s", tq.question, e)

    return _run_ragas(rows, ragas_llm, ragas_emb, metrics)


# ---------------------------------------------------------------------------
# 2. Halluzinationstest
# ---------------------------------------------------------------------------

def run_hallucination_test(
    test_questions: list[TestQuestion],
    neo: Neo4jClient | None = None,
) -> dict:
    """Vergleicht Faithfulness mit echtem vs. falschem Kontext; höhere Differenz bedeutet höhere Halluzinationsanfälligkeit."""
    if neo is None:
        neo = _build_neo_client()

    ragas_llm = _make_ragas_llm()
    ragas_emb = _make_ragas_embeddings()

    # Retrieval + Generierung immer im Cloud-Modus (wie alle anderen Eval-Funktionen)
    original_mode = cfg.LLM_MODE
    cfg.LLM_MODE = "cloud"
    log.info("Halluzinationstest: LLM_MODE auf 'cloud' gesetzt (war: '%s')", original_mode)

    normal_rows: dict[str, list] = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    false_rows: dict[str, list] = {"question": [], "answer": [], "contexts": [], "ground_truth": []}

    for i, tq in enumerate(test_questions):
        try:
            supports = _retrieve_supports(neo, tq.question)
            answer_normal = grounded_answer(tq.question, supports)

            # Alle Falsch-Fakten des Kurses als Kontext übergeben (nicht nur einen),
            # damit die Kontextgröße vergleichbar mit dem echten Retrieval ist.
            course_facts = _FALSE_FACTS_BY_KURS.get(tq.kurs_id or "", _FALSE_FACTS)
            fake_supports = [
                {"type": "paragraph", "paragraph_id": f"FAKE_{i}_{j}", "text": fact, "paper_id": "fake"}
                for j, fact in enumerate(course_facts)
            ]
            answer_false = grounded_answer(tq.question, fake_supports)

            normal_rows["question"].append(tq.question)
            normal_rows["answer"].append(answer_normal)
            normal_rows["contexts"].append(_supports_to_contexts(supports) or [""])
            normal_rows["ground_truth"].append(tq.ground_truth)

            false_rows["question"].append(tq.question)
            false_rows["answer"].append(answer_false)
            false_rows["contexts"].append(course_facts)
            false_rows["ground_truth"].append(tq.ground_truth)

        except Exception as e:
            log.error("Halluzinationstest übersprungen '%s': %s", tq.question, e)

    cfg.LLM_MODE = original_mode

    result_normal = _run_ragas(normal_rows, ragas_llm, ragas_emb, [Faithfulness(llm=ragas_llm)])
    result_false = _run_ragas(false_rows, ragas_llm, ragas_emb, [Faithfulness(llm=ragas_llm)])

    faith_normal = result_normal.get("faithfulness") or 0.0
    faith_false = result_false.get("faithfulness") or 0.0

    return {
        "faithfulness_echter_kontext": round(faith_normal, 4),
        "faithfulness_falscher_kontext": round(faith_false, 4),
        "halluzinations_anfaelligkeit": round(faith_normal - faith_false, 4),
        "interpretation": (
            "hoch" if (faith_normal - faith_false) > 0.3
            else "mittel" if (faith_normal - faith_false) > 0.1
            else "niedrig"
        ),
    }


# ---------------------------------------------------------------------------
# 3. Cloud vs. Lokal Vergleich (LM Studio)
# ---------------------------------------------------------------------------

# GPT-4o-mini Preise (Stand: 2025)
_GPT4O_MINI_INPUT_PER_1K_USD = 0.000150
_GPT4O_MINI_OUTPUT_PER_1K_USD = 0.000600
_CHARS_PER_TOKEN = 4


def _estimate_openai_cost(prompt: str, answer: str) -> float:
    tokens_in = len(prompt) / _CHARS_PER_TOKEN
    tokens_out = len(answer) / _CHARS_PER_TOKEN
    return (tokens_in / 1000 * _GPT4O_MINI_INPUT_PER_1K_USD) + (tokens_out / 1000 * _GPT4O_MINI_OUTPUT_PER_1K_USD)


def run_llm_comparison(
    test_questions: list[TestQuestion],
    local_model_name: str | None = None,
    neo: Neo4jClient | None = None,
) -> dict:
    """Vergleicht GPT-4o-mini (Cloud) mit LM Studio (Lokal) auf RAGAS-Metriken bei gleichen Fragen und gleichem Retrieval."""
    if neo is None:
        neo = _build_neo_client()

    local_model = local_model_name or cfg.LMSTUDIO_CHAT_MODEL
    ragas_llm = _make_ragas_llm()
    ragas_emb = _make_ragas_embeddings()
    metrics = [Faithfulness(llm=ragas_llm), AnswerRelevancy(llm=ragas_llm, embeddings=ragas_emb, strictness=1)]

    # Retrieval immer mit Cloud-Embeddings
    original_mode = cfg.LLM_MODE
    cfg.LLM_MODE = "cloud"
    retrieved: list[tuple[str, list[dict], list[str]]] = []

    for tq in test_questions:
        try:
            supports = _retrieve_supports(neo, tq.question)
            contexts = _supports_to_contexts(supports)
            retrieved.append((tq.question, supports, contexts))
        except Exception as e:
            log.error("Retrieval fehlgeschlagen '%s': %s", tq.question, e)
            retrieved.append((tq.question, [], []))

    cfg.LLM_MODE = original_mode

    results: dict[str, Any] = {}

    for mode, model_label in [("cloud", "gpt-4o-mini"), ("local", local_model)]:
        cfg.LLM_MODE = mode
        rows: dict[str, list] = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
        total_cost = 0.0
        start = time.time()

        for tq, (question, supports, contexts) in zip(test_questions, retrieved):
            try:
                answer = grounded_answer(question, supports)
                rows["question"].append(question)
                rows["answer"].append(answer)
                rows["contexts"].append(contexts or [""])
                rows["ground_truth"].append(tq.ground_truth)
                if mode == "cloud":
                    total_cost += _estimate_openai_cost(question, answer)
            except Exception as e:
                log.error("[%s] Generierung fehlgeschlagen '%s': %s", mode, question, e)

        elapsed = round(time.time() - start, 2)
        cfg.LLM_MODE = original_mode

        ragas_scores = _run_ragas(rows, ragas_llm, ragas_emb, metrics)
        results[mode] = {
            "modell": model_label,
            "ragas_scores": ragas_scores,
            "inferenz_zeit_sek": elapsed,
            "geschaetzte_kosten_usd": round(total_cost, 5) if mode == "cloud" else "n/a (lokal)",
        }

    return results


# ---------------------------------------------------------------------------
# 4. Baseline-Vergleich: Vektor-RAG vs. GraphRAG
# ---------------------------------------------------------------------------

def run_baseline_comparison(
    test_questions: list[TestQuestion],
    neo: Neo4jClient | None = None,
) -> dict:
    """Vergleicht reines Vektor-RAG (hybrid_retrieve) mit GraphRAG (concept_based_retrieve) auf allen RAGAS-Metriken."""
    if neo is None:
        neo = _build_neo_client()

    ragas_llm = _make_ragas_llm()
    ragas_emb = _make_ragas_embeddings()
    metrics = _make_metrics(ragas_llm, ragas_emb)

    runs = {
        "baseline_vektor_rag": {
            "beschreibung": "Reines Vektor-RAG ohne Konzeptgraph (hybrid_retrieve)",
            "retrieve_fn": lambda q: hybrid_retrieve(neo, q).get("supports", []),
        },
        "graphrag": {
            "beschreibung": "GraphRAG mit Konzeptexpansion (concept_based_retrieve)",
            "retrieve_fn": lambda q: concept_based_retrieve(neo, q).get("supports", []),
        },
    }

    output: dict[str, Any] = {}

    for run_name, run_cfg in runs.items():
        log.info("Baseline-Vergleich: starte '%s' …", run_name)
        rows: dict[str, list] = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
        context_counts: list[int] = []
        start = time.time()

        for tq in test_questions:
            try:
                supports = run_cfg["retrieve_fn"](tq.question)
                answer = grounded_answer(tq.question, supports)
                contexts = _supports_to_contexts(supports)
                rows["question"].append(tq.question)
                rows["answer"].append(answer)
                rows["contexts"].append(contexts or [""])
                rows["ground_truth"].append(tq.ground_truth)
                context_counts.append(len(contexts))
            except Exception as e:
                log.error("[%s] Frage übersprungen '%s': %s", run_name, tq.question, e)

        elapsed = round(time.time() - start, 2)
        ragas_scores = _run_ragas(rows, ragas_llm, ragas_emb, metrics)

        output[run_name] = {
            "beschreibung": run_cfg["beschreibung"],
            "ragas_scores": ragas_scores,
            "durchschnittliche_kontexte": round(sum(context_counts) / len(context_counts), 1) if context_counts else 0,
            "inferenz_zeit_sek": elapsed,
        }

    # Differenz: GraphRAG minus Baseline (positiv = GraphRAG besser)
    score_keys = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    diff: dict[str, Any] = {}
    for k in score_keys:
        g = output["graphrag"]["ragas_scores"].get(k)
        b = output["baseline_vektor_rag"]["ragas_scores"].get(k)
        if g is not None and b is not None:
            diff[k] = round(g - b, 4)
    output["differenz_graphrag_minus_baseline"] = diff

    return output


# ---------------------------------------------------------------------------
# 5. Ergebnis-Export
# ---------------------------------------------------------------------------

def export_results_to_json(results: dict, output_path: str) -> None:
    """Serialisiert das Ergebnis-Dict als JSON-Datei in den angegebenen Pfad."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info("Ergebnisse gespeichert: %s", output_path)


def print_results_table(results: dict) -> None:
    """Gibt alle Evaluationsergebnisse als formatierte Tabelle auf stdout aus."""
    width = 72
    print("\n" + "=" * width)
    print(f"{'RAGAS EVALUATION ERGEBNISSE':^{width}}")
    print("=" * width)

    for section, data in results.items():
        print(f"\n  [{section.upper().replace('_', ' ')}]")
        if not isinstance(data, dict):
            print(f"    {data}")
            continue
        for key, value in data.items():
            if isinstance(value, dict):
                print(f"    {key}:")
                for k, v in value.items():
                    v_str = f"{v:.4f}" if isinstance(v, float) else str(v)
                    print(f"      {k:<38} {v_str}")
            else:
                v_str = f"{value:.4f}" if isinstance(value, float) else str(value)
                print(f"    {key:<40} {v_str}")

    print("\n" + "=" * width + "\n")


# ---------------------------------------------------------------------------
# 5b. Kursspezifische Evaluation
# ---------------------------------------------------------------------------

QUESTIONS_PER_KURS = 5  # Thesis-Vorgabe: 5 Fragen je Kurs


def _sample_evenly(questions: list, n: int) -> list:
    """Wählt n Fragen gleichmäßig verteilt über die gesamte Liste."""
    if len(questions) <= n:
        return questions
    step = len(questions) / n
    return [questions[int(i * step)] for i in range(n)]


def load_course_questions(kurs_id: str) -> list[TestQuestion]:
    """Lädt kursspezifische Testfragen aus data/eval/questions_{kurs_id}.json und gibt gleichmäßig verteilte QUESTIONS_PER_KURS zurück."""
    import json
    path = Path(__file__).resolve().parents[1] / "data" / "eval" / f"questions_{kurs_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Keine Fragen für '{kurs_id}' gefunden: {path}\n"
            f"Bitte zuerst ausführen: python scripts/generate_course_questions.py --kurs {kurs_id}"
        )
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    all_questions = [
        TestQuestion(
            question=q["question"],
            ground_truth=q.get("ground_truth") or q.get("abschnitt_titel", ""),
            question_type=q.get("question_type", "factual"),
            kurs_id=kurs_id,
        )
        for q in raw
        if q.get("question")
    ]
    questions = _sample_evenly(all_questions, QUESTIONS_PER_KURS)
    log.info(
        "Geladen: %d/%d Fragen für Kurs '%s' (gleichmäßiges Sampling)",
        len(questions), len(all_questions), kurs_id,
    )
    return questions


def run_course_evaluation(
    kurs_id: str,
    neo: Neo4jClient | None = None,
) -> dict:
    """Führt vollständige RAGAS-Evaluation (4 Metriken) für einen Kurs auf Basis der gespeicherten Testfragen durch."""
    questions = load_course_questions(kurs_id)
    result = run_ragas_evaluation(questions, neo=neo)
    return {
        "kurs_id": kurs_id,
        "n_fragen": len(questions),
        "ragas_scores": result,
    }


def run_all_courses_evaluation(
    kurs_ids: list[str] | None = None,
    neo: Neo4jClient | None = None,
) -> dict[str, Any]:
    """Evaluiert alle drei Kurse sequenziell mit RAGAS und gibt vergleichbare Scores zurück; kurs_ids=None → alle drei."""
    from scripts.generate_course_questions import KURSE
    ids = kurs_ids or list(KURSE.keys())
    _close = neo is None
    if neo is None:
        neo = _build_neo_client()

    ragas_llm = _make_ragas_llm()
    ragas_emb = _make_ragas_embeddings()
    metrics = _make_metrics(ragas_llm, ragas_emb)

    original_mode = cfg.LLM_MODE
    cfg.LLM_MODE = "cloud"
    log.info("Kurs-Evaluation: LLM_MODE auf 'cloud' gesetzt (war: '%s')", original_mode)

    all_results: dict[str, Any] = {}
    for kid in ids:
        log.info("=== Kurs-Evaluation: %s ===", kid)
        try:
            questions = load_course_questions(kid)
            rows: dict[str, list] = {
                "question": [], "answer": [], "contexts": [], "ground_truth": []
            }
            for tq in questions:
                try:
                    supports = _retrieve_supports(neo, tq.question)
                    answer = grounded_answer(tq.question, supports)
                    rows["question"].append(tq.question)
                    rows["answer"].append(answer)
                    rows["contexts"].append(_supports_to_contexts(supports) or [""])
                    rows["ground_truth"].append(tq.ground_truth)
                except Exception as e:
                    log.error("[%s] übersprungen '%s': %s", kid, tq.question[:50], e)

            scores = _run_ragas(rows, ragas_llm, ragas_emb, metrics)
            all_results[kid] = {
                "kurs_name": KURSE[kid]["name"],
                "n_fragen": len(questions),
                "ragas_scores": scores,
            }
            log.info("[%s] Scores: %s", kid, scores)
        except Exception as e:
            log.error("[%s] Fehlgeschlagen: %s", kid, e)
            all_results[kid] = {"fehler": str(e)}

    cfg.LLM_MODE = original_mode

    if _close:
        neo.close()

    # Vergleichstabelle auf Konsole
    _print_course_comparison(all_results)
    return all_results


def _print_course_comparison(results: dict) -> None:
    """Gibt eine RAGAS-Vergleichstabelle aller Kurse auf stdout aus."""
    metric_keys = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    metric_labels = ["Faithfulness", "Ans. Relevancy", "Ctx. Precision", "Ctx. Recall"]
    kids = list(results.keys())
    col = 18

    print("\n" + "=" * (20 + col * len(kids)))
    print(f"{'KURS-VERGLEICH (RAGAS)':^{20 + col * len(kids)}}")
    print("=" * (20 + col * len(kids)))
    header = f"{'Metrik':<20}" + "".join(
        f"{results[k].get('kurs_name', k)[:col-1]:>{col}}" for k in kids
    )
    print(header)
    print("-" * (20 + col * len(kids)))
    for mk, ml in zip(metric_keys, metric_labels):
        row = f"{ml:<20}"
        for k in kids:
            v = (results[k].get("ragas_scores") or {}).get(mk)
            row += f"{(f'{v:.4f}' if isinstance(v, float) else 'N/A'):>{col}}"
        print(row)
    print("=" * (20 + col * len(kids)) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    output_dir = os.path.join(os.path.dirname(__file__), "..", "data", "eval")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "ragas_results.json")

    neo = _build_neo_client()
    all_results: dict[str, Any] = {}

    log.info("=== 1/4  RAGAS Evaluation (alle 4 Metriken) ===")
    try:
        all_results["ragas_evaluation"] = run_ragas_evaluation(DEFAULT_TEST_QUESTIONS, neo)
        log.info("Fertig: %s", all_results["ragas_evaluation"])
    except Exception as e:
        log.error("RAGAS Evaluation fehlgeschlagen: %s", e)

    log.info("=== 2/4  Halluzinationstest ===")
    try:
        # Kursfragen bevorzugen (gleiche Logik wie GUI) — Fallback nur wenn keine Dateien vorhanden
        _hall_questions: list[TestQuestion] = []
        try:
            from scripts.generate_course_questions import KURSE as _KURSE_HALL
            _eval_dir = Path(__file__).resolve().parents[1] / "data" / "eval"
            for _kid in _KURSE_HALL:
                if (_eval_dir / f"questions_{_kid}.json").exists():
                    try:
                        _hall_questions.extend(load_course_questions(_kid))
                    except Exception as _e:
                        log.warning("Kursfragen für '%s' nicht ladbar: %s", _kid, _e)
        except Exception:
            pass

        if _hall_questions:
            hall_questions = _sample_evenly(_hall_questions, 15)
            log.info("Halluzinationstest: %d Kursfragen geladen", len(hall_questions))
        else:
            log.warning(
                "Keine Kursfragen-Dateien gefunden — Fallback auf DEFAULT_TEST_QUESTIONS. "
                "Themen-Mismatch möglich (generische NLP-Fragen vs. Graphinhalt)!"
            )
            hall_questions = [q for q in DEFAULT_TEST_QUESTIONS if q.question_type == "false_context"]
            hall_questions += [q for q in DEFAULT_TEST_QUESTIONS if q.question_type == "factual"][:3]

        all_results["halluzinationstest"] = run_hallucination_test(hall_questions, neo)
        log.info("Fertig: %s", all_results["halluzinationstest"])
    except Exception as e:
        log.error("Halluzinationstest fehlgeschlagen: %s", e)

    log.info("=== 3/4  Cloud vs. Lokal Vergleich (LM Studio) ===")
    try:
        sample = DEFAULT_TEST_QUESTIONS[:5]
        all_results["llm_vergleich"] = run_llm_comparison(sample, neo=neo)
        log.info("Fertig: %s", all_results["llm_vergleich"])
    except Exception as e:
        log.error("LLM-Vergleich fehlgeschlagen: %s", e)

    log.info("=== 4/4  Baseline-Vergleich: Vektor-RAG vs. GraphRAG ===")
    try:
        all_results["baseline_vergleich"] = run_baseline_comparison(DEFAULT_TEST_QUESTIONS, neo)
        log.info("Fertig: %s", all_results["baseline_vergleich"])
    except Exception as e:
        log.error("Baseline-Vergleich fehlgeschlagen: %s", e)

    neo.close()
    export_results_to_json(all_results, output_path)
    print_results_table(all_results)
