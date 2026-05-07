"""
Framework-Vergleich: Mein GraphRAG vs. MS GraphRAG vs. LightRAG
================================================================
Evaluiert alle drei Systeme mit RAGAS auf demselben Textkorpus.

Setup:
    pip install graphrag lightrag-hku

Ausführung:
    python scripts/framework_comparison.py [--skip-indexing] [--questions N]

ACHTUNG: MS GraphRAG Indexierung dauert 20–40 Minuten und erzeugt LLM-Kosten!
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: E402
from ragas.llms import LangchainLLMWrapper  # noqa: E402
from ragas.metrics.collections import (  # noqa: E402
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

from src import config as cfg  # noqa: E402
from src.neo import Neo4jClient  # noqa: E402
from src.openai_client import grounded_answer  # noqa: E402
from src.retriever import concept_based_retrieve  # noqa: E402
from scripts.ragas_eval import (  # noqa: E402
    DEFAULT_TEST_QUESTIONS,
    TestQuestion,
    _make_ragas_embeddings,
    _make_ragas_llm,
    _run_ragas,
    _supports_to_contexts,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

METRICS = [faithfulness, answer_relevancy, context_precision, context_recall]

_WORKSPACE_ROOT = ROOT / "data" / "framework_workspaces"
DEFAULT_GRAPHRAG_WORKSPACE = str(_WORKSPACE_ROOT / "graphrag_workspace")
DEFAULT_LIGHTRAG_WORKSPACE = str(_WORKSPACE_ROOT / "lightrag_workspace")
DEFAULT_CORPUS_DIR = str(_WORKSPACE_ROOT / "corpus_txt")
RESULTS_PATH = str(_WORKSPACE_ROOT / "comparison_results.json")

_METRIC_LABELS = {
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Answer Relevancy",
    "context_precision": "Context Precision",
    "context_recall": "Context Recall",
}
_SYSTEM_LABELS = {
    "mein_graphrag": "Mein GraphRAG",
    "ms_graphrag": "MS GraphRAG",
    "lightrag": "LightRAG",
}


# ---------------------------------------------------------------------------
# Schritt 1: Paper-Texte aus Neo4j exportieren
# ---------------------------------------------------------------------------

def export_papers_as_txt(output_dir: str, neo: Neo4jClient | None = None) -> list[str]:
    """
    Exportiert alle Paragraphen aus Neo4j pro Paper als .txt-Datei.
    Gibt Pfade der erzeugten Dateien zurück.
    """
    _close = neo is None
    if neo is None:
        neo = Neo4jClient()

    os.makedirs(output_dir, exist_ok=True)

    rows = neo.run(
        """
        MATCH (p:Paper)-[:HAS_PARAGRAPH]->(par:Paragraph)
        RETURN p.title AS title, p.paper_id AS paper_id,
               par.text AS text, par.page AS page
        ORDER BY p.paper_id, par.page
        """
    )

    papers: dict[str, dict] = {}
    for r in rows:
        pid = r["paper_id"]
        if pid not in papers:
            papers[pid] = {"title": r.get("title") or pid, "paragraphs": []}
        if r.get("text"):
            papers[pid]["paragraphs"].append(r["text"])

    written: list[str] = []
    for pid, data in papers.items():
        safe_pid = "".join(c if (c.isalnum() or c in "-_") else "_" for c in pid)
        fpath = os.path.join(output_dir, f"{safe_pid}.txt")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(f"# {data['title']}\n\n")
            f.write("\n\n".join(data["paragraphs"]))
        written.append(fpath)
        log.info("Exportiert: %s (%d Paragraphen)", Path(fpath).name, len(data["paragraphs"]))

    if _close:
        neo.close()

    log.info("Paper-Export: %d Dateien → %s", len(written), output_dir)
    return written


# ---------------------------------------------------------------------------
# Schritt 2: Mein GraphRAG-System
# ---------------------------------------------------------------------------

def run_my_system_evaluation(
    test_questions: list[TestQuestion],
    ragas_llm: LangchainLLMWrapper,
    ragas_emb: LangchainEmbeddingsWrapper,
    neo: Neo4jClient | None = None,
) -> dict:
    """Evaluiert das eigene System (concept_based_retrieve + grounded_answer)."""
    _close = neo is None
    if neo is None:
        neo = Neo4jClient()

    rows: dict[str, list] = {
        "question": [], "answer": [], "contexts": [], "ground_truth": []
    }
    times: list[float] = []

    for tq in test_questions:
        try:
            t0 = time.time()
            supports = concept_based_retrieve(neo, tq.question).get("supports", [])
            answer = grounded_answer(tq.question, supports)
            times.append(time.time() - t0)
            rows["question"].append(tq.question)
            rows["answer"].append(answer)
            rows["contexts"].append(_supports_to_contexts(supports) or [""])
            rows["ground_truth"].append(tq.ground_truth)
        except Exception as e:
            log.error("[MeinSystem] übersprungen '%s': %s", tq.question, e)

    if _close:
        neo.close()

    return {
        "beschreibung": "Eigenes GraphRAG-System (Neo4j + concept_based_retrieve)",
        "ragas_scores": _run_ragas(rows, ragas_llm, ragas_emb, METRICS),
        "inferenz_zeit_avg_sek": round(sum(times) / len(times), 2) if times else 0,
        "n_fragen": len(rows["question"]),
    }


# ---------------------------------------------------------------------------
# Schritt 3: MS GraphRAG Evaluator
# ---------------------------------------------------------------------------

class MSGraphRAGEvaluator:
    """Wrappt MS GraphRAG (microsoft/graphrag) für den RAGAS-Vergleich."""

    def __init__(self, workspace_dir: str, input_dir: str) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.input_dir = Path(input_dir)
        self._indexed = False

    # ------------------------------------------------------------------
    def setup_graphrag(self) -> None:
        """
        Kopiert Corpus-Dateien, initialisiert den Workspace mit
        `graphrag init` und überschreibt anschließend die erzeugte
        settings.yaml mit unseren Modell-Konfigurationen.
        """
        target_input = self.workspace_dir / "input"
        target_input.mkdir(parents=True, exist_ok=True)

        txt_files = list(self.input_dir.glob("*.txt"))
        if not txt_files:
            raise FileNotFoundError(f"Keine .txt-Dateien in {self.input_dir}")
        for f in txt_files:
            shutil.copy2(f, target_input / f.name)
        log.info("[MSGraphRAG] %d Dateien kopiert → %s", len(txt_files), target_input)

        # graphrag init (erzeugt Verzeichnisstruktur + Template-settings.yaml)
        log.info("[MSGraphRAG] graphrag init …")
        _run_cmd(
            [sys.executable, "-m", "graphrag", "init", "--root", str(self.workspace_dir)],
            label="MSGraphRAG-init",
        )

        # settings.yaml mit unseren Parametern überschreiben
        api_key = cfg.OPENAI_API_KEY or ""
        settings = textwrap.dedent(f"""\
            encoding_model: cl100k_base

            models:
              default_chat_model:
                api_key: {api_key}
                type: openai_chat
                model: gpt-4o-mini
                model_supports_json: true
                max_tokens: 4000
                temperature: 0.0
              default_embedding_model:
                api_key: {api_key}
                type: openai_embedding
                model: text-embedding-3-large

            input:
              type: file
              file_type: text
              base_dir: "input"
              file_encoding: utf-8
              file_pattern: ".*\\.txt$"

            cache:
              type: file
              base_dir: "cache"

            reporting:
              type: file
              base_dir: "output/logs"

            storage:
              type: file
              base_dir: "output/artifacts"

            entity_extraction:
              entity_types: [organization, person, geo, event, concept]
              max_gleanings: 0

            community_reports:
              max_length: 2000
              max_input_length: 8000

            cluster_graph:
              max_cluster_size: 10

            embed_graph:
              enabled: false

            umap:
              enabled: false

            local_search:
              top_k_entities: 10
              top_k_relationships: 10
              max_tokens: 12000

            global_search:
              max_tokens: 12000
              map_max_tokens: 1000
              reduce_max_tokens: 2000
              concurrency: 32
        """)
        settings_path = self.workspace_dir / "settings.yaml"
        settings_path.write_text(settings, encoding="utf-8")
        log.info("[MSGraphRAG] settings.yaml geschrieben.")

    # ------------------------------------------------------------------
    def index_corpus(self) -> None:
        """Baut den Index (20–40 min, erzeugt LLM-Kosten)."""
        log.info("[MSGraphRAG] Starte Indexierung (20–40 min) …")
        _run_cmd(
            [sys.executable, "-m", "graphrag", "index", "--root", str(self.workspace_dir)],
            label="MSGraphRAG-index",
            live_output=True,
        )
        self._indexed = True
        log.info("[MSGraphRAG] Indexierung abgeschlossen.")

    # ------------------------------------------------------------------
    def query(self, question: str) -> tuple[str, list[str]]:
        """Fragt MS GraphRAG (Global Search) ab."""
        try:
            return self._query_python_api(question)
        except Exception as e:
            log.warning("[MSGraphRAG] Python-API fehlgeschlagen (%s), fallback auf CLI.", e)
            return self._query_cli(question)

    def _query_python_api(self, question: str) -> tuple[str, list[str]]:
        from graphrag.query.api import global_search  # type: ignore

        artifacts_dir = self.workspace_dir / "output" / "artifacts"

        async def _run() -> tuple[str, list[str]]:
            result = await global_search(
                config_filepath=str(self.workspace_dir / "settings.yaml"),
                data_dir=str(artifacts_dir),
                root_dir=str(self.workspace_dir),
                community_level=2,
                response_type="Single Paragraph",
                query=question,
            )
            answer = getattr(result, "response", None) or str(result)
            contexts: list[str] = []
            cd = getattr(result, "context_data", None)
            if isinstance(cd, dict):
                for r in cd.get("reports", []):
                    if r.get("content"):
                        contexts.append(r["content"])
            elif isinstance(cd, list):
                contexts = [str(c) for c in cd if c]
            return answer, contexts[:10]

        return asyncio.run(_run())

    def _query_cli(self, question: str) -> tuple[str, list[str]]:
        result = subprocess.run(
            [
                sys.executable, "-m", "graphrag", "query",
                "--root", str(self.workspace_dir),
                "--method", "global",
                "--query", question,
            ],
            capture_output=True, text=True,
            cwd=str(ROOT), timeout=180,
        )
        answer = result.stdout.strip() or "(keine Antwort)"
        return answer, [answer[:500]] if answer else [""]

    # ------------------------------------------------------------------
    def run_ragas_evaluation(
        self,
        test_questions: list[TestQuestion],
        ragas_llm: LangchainLLMWrapper,
        ragas_emb: LangchainEmbeddingsWrapper,
    ) -> dict:
        rows: dict[str, list] = {
            "question": [], "answer": [], "contexts": [], "ground_truth": []
        }
        times: list[float] = []

        for tq in test_questions:
            try:
                t0 = time.time()
                answer, contexts = self.query(tq.question)
                times.append(time.time() - t0)
                rows["question"].append(tq.question)
                rows["answer"].append(answer)
                rows["contexts"].append(contexts or [""])
                rows["ground_truth"].append(tq.ground_truth)
            except Exception as e:
                log.error("[MSGraphRAG] übersprungen '%s': %s", tq.question, e)

        return {
            "beschreibung": "MS GraphRAG (microsoft/graphrag, Global Search)",
            "ragas_scores": _run_ragas(rows, ragas_llm, ragas_emb, METRICS),
            "inferenz_zeit_avg_sek": round(sum(times) / len(times), 2) if times else 0,
            "n_fragen": len(rows["question"]),
        }


# ---------------------------------------------------------------------------
# Schritt 4: LightRAG Evaluator
# ---------------------------------------------------------------------------

class LightRAGEvaluator:
    """Wrappt LightRAG (HKUDS/LightRAG) für den RAGAS-Vergleich."""

    def __init__(self, working_dir: str) -> None:
        self.working_dir = working_dir
        self._rag: Any = None

    # ------------------------------------------------------------------
    def setup_lightrag(self) -> None:
        """Initialisiert den LightRAG-Workspace mit OpenAI-Modellen."""
        os.makedirs(self.working_dir, exist_ok=True)
        try:
            from lightrag import LightRAG  # type: ignore
            from lightrag.utils import EmbeddingFunc  # type: ignore
        except ImportError:
            raise ImportError("LightRAG nicht installiert. Bitte: pip install lightrag-hku")

        api_key = cfg.OPENAI_API_KEY or ""

        # Async LLM-Funktion (gpt-4o-mini)
        async def _llm_func(prompt: str, system_prompt: str | None = None, **kw) -> str:
            from openai import AsyncOpenAI  # type: ignore
            client = AsyncOpenAI(api_key=api_key)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.0,
            )
            return resp.choices[0].message.content or ""

        # Async Embedding-Funktion (text-embedding-3-large, 3072 dim)
        async def _embed_func(texts: list[str]) -> list[list[float]]:
            from openai import AsyncOpenAI  # type: ignore
            client = AsyncOpenAI(api_key=api_key)
            resp = await client.embeddings.create(
                model="text-embedding-3-large",
                input=texts,
            )
            return [item.embedding for item in resp.data]

        embed_fn = EmbeddingFunc(
            embedding_dim=3072,
            max_token_size=8192,
            func=_embed_func,
        )

        self._rag = LightRAG(
            working_dir=self.working_dir,
            llm_model_func=_llm_func,
            embedding_func=embed_fn,
        )
        log.info("[LightRAG] Workspace initialisiert: %s", self.working_dir)

    # ------------------------------------------------------------------
    def index_corpus(self, txt_files: list[str]) -> None:
        """Indiziert alle .txt-Dateien."""
        if self._rag is None:
            raise RuntimeError("setup_lightrag() zuerst aufrufen.")

        for fpath in txt_files:
            try:
                text = Path(fpath).read_text(encoding="utf-8")
                self._rag.insert(text)
                log.info("[LightRAG] Indiziert: %s (%d Zeichen)", Path(fpath).name, len(text))
            except Exception as e:
                log.error("[LightRAG] Fehler bei %s: %s", Path(fpath).name, e)

        log.info("[LightRAG] Indexierung abgeschlossen.")

    # ------------------------------------------------------------------
    def query(self, question: str, mode: str = "hybrid") -> tuple[str, list[str]]:
        """Gibt (Antwort, Kontexte) zurück."""
        if self._rag is None:
            raise RuntimeError("setup_lightrag() zuerst aufrufen.")

        from lightrag import QueryParam  # type: ignore

        # Kontext separat abrufen (only_need_context=True)
        contexts: list[str] = []
        try:
            ctx_raw = self._rag.query(
                question,
                param=QueryParam(mode=mode, only_need_context=True),
            )
            if isinstance(ctx_raw, str) and ctx_raw.strip():
                contexts = [ctx_raw]
            elif isinstance(ctx_raw, list):
                contexts = [str(c) for c in ctx_raw if c]
        except Exception:
            pass  # Kontext nicht verfügbar → contexts bleibt leer

        # Antwort abrufen
        try:
            answer_raw = self._rag.query(
                question,
                param=QueryParam(mode=mode, only_need_context=False),
            )
            answer = str(answer_raw) if answer_raw else ""
        except Exception as e:
            log.warning("[LightRAG] query fehlgeschlagen: %s", e)
            answer = ""

        if not contexts:
            contexts = [answer[:500]] if answer else [""]

        return answer, contexts[:10]

    # ------------------------------------------------------------------
    def run_ragas_evaluation(
        self,
        test_questions: list[TestQuestion],
        ragas_llm: LangchainLLMWrapper,
        ragas_emb: LangchainEmbeddingsWrapper,
    ) -> dict:
        rows: dict[str, list] = {
            "question": [], "answer": [], "contexts": [], "ground_truth": []
        }
        times: list[float] = []

        for tq in test_questions:
            try:
                t0 = time.time()
                answer, contexts = self.query(tq.question)
                times.append(time.time() - t0)
                rows["question"].append(tq.question)
                rows["answer"].append(answer)
                rows["contexts"].append(contexts or [""])
                rows["ground_truth"].append(tq.ground_truth)
            except Exception as e:
                log.error("[LightRAG] übersprungen '%s': %s", tq.question, e)

        return {
            "beschreibung": "LightRAG (HKUDS/LightRAG, Hybrid-Modus)",
            "ragas_scores": _run_ragas(rows, ragas_llm, ragas_emb, METRICS),
            "inferenz_zeit_avg_sek": round(sum(times) / len(times), 2) if times else 0,
            "n_fragen": len(rows["question"]),
        }


# ---------------------------------------------------------------------------
# Schritt 5: ComparisonRunner
# ---------------------------------------------------------------------------

def run_full_comparison(
    test_questions: list[TestQuestion],
    corpus_dir: str = DEFAULT_CORPUS_DIR,
    graphrag_workspace: str = DEFAULT_GRAPHRAG_WORKSPACE,
    lightrag_workspace: str = DEFAULT_LIGHTRAG_WORKSPACE,
    neo: Neo4jClient | None = None,
    skip_indexing: bool = False,
) -> dict:
    """
    Führt den vollständigen Vergleich aller drei Systeme aus.
    Fehler in einem System stoppen die anderen nicht.

    skip_indexing=True: Überspringt Indexierung (wenn bereits durchgeführt).
    """
    results: dict[str, Any] = {}
    ragas_llm = _make_ragas_llm()
    ragas_emb = _make_ragas_embeddings()

    # 1. Paper exportieren
    log.info("=== 1/5  Paper-Export ===")
    txt_files = export_papers_as_txt(corpus_dir, neo=neo)
    if not txt_files:
        raise RuntimeError("Keine Papers in Neo4j. Bitte zuerst Papers einlesen.")

    # 2. Mein System
    log.info("=== 2/5  Mein GraphRAG-System ===")
    try:
        results["mein_graphrag"] = run_my_system_evaluation(
            test_questions, ragas_llm, ragas_emb, neo=neo
        )
        log.info("[MeinSystem] Scores: %s", results["mein_graphrag"]["ragas_scores"])
    except Exception as e:
        log.error("[MeinSystem] Fehlgeschlagen: %s", e)
        results["mein_graphrag"] = {"fehler": str(e), "ragas_scores": None}

    # 3. LightRAG (zuerst, da schneller als MS GraphRAG)
    log.info("=== 3/5  LightRAG ===")
    lightrag_eval = LightRAGEvaluator(working_dir=lightrag_workspace)
    try:
        lightrag_eval.setup_lightrag()
        if not skip_indexing:
            lightrag_eval.index_corpus(txt_files)
        results["lightrag"] = lightrag_eval.run_ragas_evaluation(
            test_questions, ragas_llm, ragas_emb
        )
        log.info("[LightRAG] Scores: %s", results["lightrag"]["ragas_scores"])
    except Exception as e:
        log.error("[LightRAG] Fehlgeschlagen: %s", e)
        results["lightrag"] = {"fehler": str(e), "ragas_scores": None}

    # 4. MS GraphRAG (dauert am längsten)
    log.info("=== 4/5  MS GraphRAG ===")
    log.warning("ACHTUNG: MS GraphRAG Indexierung dauert 20–40 min und erzeugt LLM-Kosten!")
    ms_eval = MSGraphRAGEvaluator(workspace_dir=graphrag_workspace, input_dir=corpus_dir)
    try:
        ms_eval.setup_graphrag()
        if not skip_indexing:
            ms_eval.index_corpus()
        results["ms_graphrag"] = ms_eval.run_ragas_evaluation(
            test_questions, ragas_llm, ragas_emb
        )
        log.info("[MSGraphRAG] Scores: %s", results["ms_graphrag"]["ragas_scores"])
    except Exception as e:
        log.error("[MSGraphRAG] Fehlgeschlagen: %s", e)
        results["ms_graphrag"] = {"fehler": str(e), "ragas_scores": None}

    # 5. Exportieren
    log.info("=== 5/5  Export ===")
    export_comparison_table(results)

    return results


# ---------------------------------------------------------------------------
# Schritt 6: Export
# ---------------------------------------------------------------------------

def export_comparison_table(results: dict, output_path: str = RESULTS_PATH) -> None:
    """Speichert Rohdaten als JSON und gibt LaTeX-Tabelle auf stdout aus."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(_sanitize_json(results), f, ensure_ascii=False, indent=2)
    log.info("Rohdaten gespeichert: %s", output_path)

    systems = list(_SYSTEM_LABELS.keys())
    metrics = list(_METRIC_LABELS.keys())

    # Konsolentabelle
    w = 25 + 16 * len(systems)
    print("\n" + "=" * w)
    print(f"{'FRAMEWORK-VERGLEICH (RAGAS)':^{w}}")
    print("=" * w)
    print(f"{'Metrik':<25}" + "".join(f"{_SYSTEM_LABELS[s]:>16}" for s in systems))
    print("-" * w)

    for m in metrics:
        row = f"{_METRIC_LABELS[m]:<25}"
        for s in systems:
            v = ((results.get(s) or {}).get("ragas_scores") or {}).get(m)
            row += f"{(f'{v:.4f}' if isinstance(v, float) else 'N/A'):>16}"
        print(row)

    print("-" * w)
    row = f"{'Ø Inferenzzeit (s)':<25}"
    for s in systems:
        t = (results.get(s) or {}).get("inferenz_zeit_avg_sek")
        row += f"{(f'{t:.2f}' if t is not None else 'N/A'):>16}"
    print(row)
    print("=" * w)

    # LaTeX
    print("\n--- LaTeX-Tabelle (direkt kopierbar) ---\n")
    print(_build_latex_table(results, metrics, systems))
    print()


def _build_latex_table(results: dict, metrics: list[str], systems: list[str]) -> str:
    col_spec = "l" + "r" * len(systems)
    sys_headers = " & ".join(f"\\textbf{{{_SYSTEM_LABELS[s]}}}" for s in systems)

    lines = [
        "\\begin{table}[htbp]",
        "\\centering",
        f"\\begin{{tabular}}{{{col_spec}}}",
        "\\toprule",
        f"\\textbf{{Metrik}} & {sys_headers} \\\\",
        "\\midrule",
    ]

    for m in metrics:
        vals = [
            ((results.get(s) or {}).get("ragas_scores") or {}).get(m)
            for s in systems
        ]
        valid = [(i, v) for i, v in enumerate(vals) if isinstance(v, float)]
        best_idx = max(valid, key=lambda x: x[1])[0] if valid else None

        cells = []
        for i, v in enumerate(vals):
            if not isinstance(v, float):
                cells.append("--")
            elif i == best_idx:
                cells.append(f"\\textbf{{{v:.4f}}}")
            else:
                cells.append(f"{v:.4f}")
        lines.append(f"{_METRIC_LABELS[m]} & {' & '.join(cells)} \\\\")

    lines.append("\\midrule")
    time_cells = []
    for s in systems:
        t = (results.get(s) or {}).get("inferenz_zeit_avg_sek")
        time_cells.append(f"{t:.2f}s" if t is not None else "--")
    lines.append(f"\\O Inferenzzeit & {' & '.join(time_cells)} \\\\")

    lines += [
        "\\bottomrule",
        "\\end{tabular}",
        "\\caption{RAGAS-Evaluationsvergleich: "
        "Eigenes GraphRAG-System vs.\\ MS GraphRAG vs.\\ LightRAG}",
        "\\label{tab:framework_comparison}",
        "\\end{table}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _run_cmd(
    cmd: list[str],
    label: str,
    live_output: bool = False,
    timeout: int = 7200,
) -> subprocess.CompletedProcess:
    """Führt einen Subprozess aus mit optionalem Live-Output."""
    if live_output:
        result = subprocess.run(cmd, cwd=str(ROOT), timeout=timeout)
    else:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=timeout
        )
        if result.returncode != 0:
            log.warning("[%s] stderr: %s", label, (result.stderr or "")[:500])
    if result.returncode != 0:
        raise RuntimeError(f"{label} fehlgeschlagen (exit {result.returncode})")
    return result


def _sanitize_json(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Framework-Vergleich: Mein GraphRAG vs. MS GraphRAG vs. LightRAG"
    )
    parser.add_argument(
        "--skip-indexing", action="store_true",
        help="Indexierung überspringen (wenn bereits durchgeführt)",
    )
    parser.add_argument(
        "--corpus-dir", default=DEFAULT_CORPUS_DIR,
        help="Verzeichnis für exportierte Paper-Texte",
    )
    parser.add_argument(
        "--questions", type=int, default=0, metavar="N",
        help="Nur die ersten N Testfragen verwenden (0 = alle; für schnelle Tests)",
    )
    args = parser.parse_args()

    questions = DEFAULT_TEST_QUESTIONS
    if args.questions > 0:
        questions = DEFAULT_TEST_QUESTIONS[: args.questions]
        log.info("Verwende %d/%d Testfragen", len(questions), len(DEFAULT_TEST_QUESTIONS))

    if not args.skip_indexing:
        log.warning(
            "MS GraphRAG Indexierung dauert 20–40 Minuten und erzeugt LLM-Kosten!\n"
            "Tipp: Einmalig indizieren, danach mit --skip-indexing erneut evaluieren."
        )

    neo = Neo4jClient()
    try:
        run_full_comparison(
            test_questions=questions,
            corpus_dir=args.corpus_dir,
            skip_indexing=args.skip_indexing,
            neo=neo,
        )
    finally:
        neo.close()

    log.info("Fertig. Ergebnisse: %s", RESULTS_PATH)
