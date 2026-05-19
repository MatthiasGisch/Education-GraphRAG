"""Systemvergleich: evaluiert das eigene GraphRAG-System gegen MS GraphRAG und LightRAG mit RAGAS-Metriken."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

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
from src.retriever import concept_based_retrieve
from scripts.ragas_eval import (
    TestQuestion,
    QUESTIONS_PER_KURS,
    load_course_questions,
    _supports_to_contexts,
    _run_ragas,
    _make_ragas_llm,
    _make_ragas_embeddings,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------

MSRAPHRAG_ROOT   = ROOT / "data" / "graphrag_index"
LIGHTRAG_DIR     = ROOT / "data" / "lightrag_index"
OUTPUT_PATH      = ROOT / "data" / "eval" / "system_comparison_results.json"
UPLOADS_DIR      = ROOT / "data" / "uploads"


# ---------------------------------------------------------------------------
# Abstrakte Adapter-Basisklasse
# ---------------------------------------------------------------------------

class SystemAdapter(ABC):
    name: str
    description: str

    @abstractmethod
    def query(self, question: str) -> tuple[str, list[str]]:
        """Gibt (answer, contexts) zurück. contexts=[] wenn nicht exponiert."""

    def is_available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Adapter 1: Eigenes GraphRAG-System
# ---------------------------------------------------------------------------

class OwnSystemAdapter(SystemAdapter):
    name = "Eigenes GraphRAG-System"
    description = "Neo4j + concept_based_retrieve + grounded_answer (GPT-4o-mini)"

    def __init__(self, neo: Neo4jClient):
        self._neo = neo

    def query(self, question: str) -> tuple[str, list[str]]:
        """Beantwortet eine Frage per concept_based_retrieve und grounded_answer und gibt Antwort und Kontexte zurück."""
        result = concept_based_retrieve(self._neo, question)
        supports = result.get("supports", [])
        answer = grounded_answer(question, supports)
        contexts = _supports_to_contexts(supports)
        return answer, contexts


# ---------------------------------------------------------------------------
# Adapter 2: MS GraphRAG
# ---------------------------------------------------------------------------

class MSGraphRAGAdapter(SystemAdapter):
    name = "MS GraphRAG"
    description = "Microsoft GraphRAG Local Search (GPT-4o-mini)"

    def __init__(self, root_dir: Path = MSRAPHRAG_ROOT):
        self._root = root_dir
        self._config = None
        self._dfs: dict | None = None  # einmalig geladene Index-DataFrames

    def is_available(self) -> bool:
        output_dir = self._root / "output"
        if not output_dir.exists():
            return False
        return len(list(output_dir.rglob("*.parquet"))) > 0

    def _load(self):
        """Lädt alle MS-GraphRAG-Index-Parquet-Dateien einmalig in DataFrames."""
        if self._dfs is not None:
            return
        try:
            from graphrag.config.load_config import load_config
            from graphrag.cli.query import _resolve_output_files
        except ImportError:
            raise ImportError("graphrag nicht installiert — pip install graphrag")

        self._config = load_config(root_dir=self._root)
        self._dfs = _resolve_output_files(
            config=self._config,
            output_list=[
                "communities", "community_reports",
                "text_units", "relationships", "entities",
            ],
            optional_list=["covariates"],
        )

    def query(self, question: str) -> tuple[str, list[str]]:
        """Fragt MS GraphRAG per Python-API ab; fällt bei Fehler auf CLI-Fallback zurück."""
        try:
            self._load()
            import graphrag.api as api

            response, context_data = asyncio.run(api.local_search(
                config=self._config,
                entities=self._dfs["entities"],
                communities=self._dfs["communities"],
                community_reports=self._dfs["community_reports"],
                text_units=self._dfs["text_units"],
                relationships=self._dfs["relationships"],
                covariates=self._dfs.get("covariates"),
                community_level=2,
                response_type="Multiple Paragraphs",
                query=question,
            ))

            # Texte aus den Retrieval-DataFrames extrahieren (text_units haben "text"-Spalte)
            contexts: list[str] = []
            if isinstance(context_data, dict):
                for df in context_data.values():
                    if hasattr(df, "columns") and "text" in df.columns:
                        contexts.extend(
                            str(t) for t in df["text"].dropna().tolist() if t
                        )

            log.info("[MS GraphRAG] Antwort (%d Z.), %d Kontexte", len(str(response)), len(contexts))
            return str(response), contexts

        except Exception as e:
            log.warning("MS GraphRAG Python-API fehlgeschlagen (%s) — Fallback auf CLI", e)
            return self._query_cli(question)

    def _query_cli(self, question: str) -> tuple[str, list[str]]:
        """CLI-Fallback (kein Kontext, aber korrekte Positionsargument-Syntax für graphrag ≥ 2.x)."""
        for cmd_prefix in (
            [sys.executable, "-m", "graphrag", "query"],
            ["graphrag", "query"],
        ):
            cmd = cmd_prefix + [
                "--root", str(self._root),
                "--method", "local",
                question,
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=120, cwd=str(ROOT),
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    answer = self._parse_output(proc.stdout)
                    log.info("[MS GraphRAG CLI] Antwort (%d Zeichen)", len(answer))
                    return answer, []
                log.debug("MS GraphRAG CLI fehlgeschlagen: rc=%d stderr=%s",
                          proc.returncode, proc.stderr[:200])
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                return "[MS GraphRAG: Timeout nach 120s]", []
        return "[MS GraphRAG: nicht verfügbar]", []

    @staticmethod
    def _parse_output(raw: str) -> str:
        """Extrahiert die eigentliche Antwort aus der MS-GraphRAG-CLI-Ausgabe."""
        for marker in (
            "SUCCESS: Local Search Response:\n",
            "Local Search Response:\n",
            "Response:\n",
        ):
            if marker in raw:
                return raw.split(marker, 1)[1].strip()
        return raw.strip()


# ---------------------------------------------------------------------------
# Adapter 3: LightRAG
# ---------------------------------------------------------------------------

_LIGHTRAG_SYSTEM_PROMPT_DE = (
    "Du bist ein wissenschaftlicher Assistent. "
    "Beantworte Fragen ausschließlich auf Deutsch, präzise und faktenbasiert, "
    "nur auf Basis der gegebenen Informationen."
)


class LightRAGAdapter(SystemAdapter):
    name = "LightRAG"
    description = "LightRAG Hybrid Mode (GPT-4o-mini + text-embedding-3-large)"

    def __init__(self, working_dir: Path = LIGHTRAG_DIR):
        self._dir = working_dir
        self._rag = None
        self._QueryParam = None

    def is_available(self) -> bool:
        if not self._dir.exists():
            return False
        index_files = list(self._dir.glob("*.json")) + list(self._dir.glob("*.graphml"))
        return len(index_files) > 0

    def _load(self):
        """Lädt den LightRAG-Index lazily beim ersten Aufruf."""
        if self._rag is not None:
            return
        try:
            from lightrag import LightRAG, QueryParam
            from lightrag.llm.openai import openai_complete_if_cache, openai_embed
            from lightrag.utils import EmbeddingFunc
        except ImportError:
            raise ImportError("LightRAG nicht installiert — pip install lightrag-hku")

        api_key = cfg.OPENAI_API_KEY

        # LLM-Wrapper: gpt-4o-mini über openai_complete_if_cache (v1.4.x API)
        async def _llm(prompt, system_prompt=None, history_messages=None, **kwargs):
            return await openai_complete_if_cache(
                model="gpt-4o-mini",
                prompt=prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=api_key,
                **kwargs,
            )

        # Direkter OpenAI-Aufruf — liefert immer genau len(texts) Vektoren.
        # openai_embed nicht verwenden: dessen interne Text-Aufteilung bei langen
        # Texten erzeugt mehr Vektoren als Eingabetexte → LightRAG Vector-Count-Mismatch.
        async def _embed(texts: list[str]):
            import openai as _oai
            import numpy as _np
            MAX_CHARS = 30000   # ~7500 Tokens, weit unter dem 8191-Token-Limit
            truncated = [t[:MAX_CHARS] for t in texts]
            client = _oai.AsyncOpenAI(api_key=api_key)
            resp = await client.embeddings.create(
                model="text-embedding-3-large", input=truncated
            )
            return _np.array(
                [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
            )

        self._rag = LightRAG(
            working_dir=str(self._dir),
            llm_model_func=_llm,
            embedding_func=EmbeddingFunc(
                embedding_dim=3072,
                max_token_size=8191,
                func=_embed,
            ),
        )
        self._QueryParam = QueryParam

    def query(self, question: str) -> tuple[str, list[str]]:
        """Fragt LightRAG im Hybrid-Modus ab und gibt Antwort und Chunk-Kontexte zurück."""
        self._load()
        try:
            async def _run():
                await self._rag.initialize_storages()
                answer_text = await self._rag.aquery(
                    question,
                    param=self._QueryParam(mode="hybrid"),
                    system_prompt=_LIGHTRAG_SYSTEM_PROMPT_DE,
                )
                # Retrieval-Kontexte aus dem Chunk-Vektorindex extrahieren
                contexts: list[str] = []
                try:
                    chunks = await self._rag.chunks_vdb.query(question, top_k=5)
                    contexts = [c.get("content", "") for c in chunks if c.get("content")]
                except Exception as ce:
                    log.warning("LightRAG chunk retrieval fehlgeschlagen: %s", ce)
                await self._rag.finalize_storages()
                return str(answer_text), contexts
            return asyncio.run(_run())
        except Exception as e:
            log.error("LightRAG query fehlgeschlagen: %s", e)
            return f"[LightRAG Fehler: {e}]", []


# ---------------------------------------------------------------------------
# Fragen laden (identisch zur Kurs-Evaluation)
# ---------------------------------------------------------------------------

def load_comparison_questions() -> list[TestQuestion]:
    """5 Fragen je Kurs — gleichmäßig gesampelt wie in run_all_courses_evaluation."""
    from scripts.generate_course_questions import KURSE
    all_qs: list[TestQuestion] = []
    for kurs_id in KURSE:
        try:
            qs = load_course_questions(kurs_id)
            all_qs.extend(qs)
            log.info("Geladen: %d Fragen für '%s'", len(qs), kurs_id)
        except FileNotFoundError as e:
            log.error("%s", e)
    return all_qs


# ---------------------------------------------------------------------------
# RAGAS-Auswertung pro System
# ---------------------------------------------------------------------------

def _eval_adapter(
    adapter: SystemAdapter,
    questions: list[TestQuestion],
    ragas_llm,
    ragas_emb,
) -> dict[str, Any]:
    """Beantwortet alle Fragen via Adapter, berechnet RAGAS-Scores und gibt das Ergebnisdict zurück."""
    rows: dict[str, list] = {
        "question": [], "answer": [], "contexts": [], "ground_truth": []
    }
    latencies: list[float] = []
    has_contexts = False

    for tq in questions:
        t0 = time.time()
        try:
            answer, contexts = adapter.query(tq.question)
            latencies.append(round(time.time() - t0, 2))
            if contexts:
                has_contexts = True
            rows["question"].append(tq.question)
            rows["answer"].append(answer)
            rows["contexts"].append(contexts or [""])
            rows["ground_truth"].append(tq.ground_truth)
            log.info("[%s] '%s...' → %d Zeichen", adapter.name, tq.question[:45], len(answer))
        except Exception as e:
            log.error("[%s] Frage übersprungen '%s': %s", adapter.name, tq.question[:50], e)
            latencies.append(round(time.time() - t0, 2))

    if not rows["question"]:
        return {"system": adapter.name, "fehler": "Keine Antworten gesammelt"}

    # Metriken: Faithfulness/CP/CR nur wenn Kontexte vorhanden
    if has_contexts:
        metrics = [
            Faithfulness(llm=ragas_llm),
            AnswerRelevancy(llm=ragas_llm, embeddings=ragas_emb, strictness=1),
            ContextPrecision(llm=ragas_llm),
            ContextRecall(llm=ragas_llm),
        ]
        hinweis = "Alle 4 RAGAS-Metriken (Retrieval-Kontexte verfügbar)"
    else:
        metrics = [AnswerRelevancy(llm=ragas_llm, embeddings=ragas_emb, strictness=1)]
        hinweis = "Nur Answer Relevancy — Retrieval-Kontexte nicht über Standard-API exponiert"

    ragas_scores = _run_ragas(rows, ragas_llm, ragas_emb, metrics)
    avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else None

    return {
        "system": adapter.name,
        "beschreibung": adapter.description,
        "n_fragen": len(rows["question"]),
        "kontexte_verfuegbar": has_contexts,
        "metriken_hinweis": hinweis,
        "ragas_scores": ragas_scores,
        "durchschnittliche_latenz_sek": avg_lat,
    }


# ---------------------------------------------------------------------------
# Konsolentabelle
# ---------------------------------------------------------------------------

def _print_comparison_table(results: dict) -> None:
    """Gibt die RAGAS-Vergleichstabelle aller Systeme formatiert auf stdout aus."""
    metric_keys   = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    metric_labels = ["Faithfulness", "Ans. Relevancy", "Ctx. Precision", "Ctx. Recall"]
    sys_keys = list(results.keys())
    col = 26

    width = 20 + col * len(sys_keys)
    print("\n" + "=" * width)
    print(f"{'SYSTEMVERGLEICH (RAGAS)':^{width}}")
    print("=" * width)
    header = f"{'Metrik':<20}" + "".join(
        f"{results[k].get('system', k)[:col - 1]:>{col}}" for k in sys_keys
    )
    print(header)
    print("-" * width)

    for mk, ml in zip(metric_keys, metric_labels):
        row = f"{ml:<20}"
        for k in sys_keys:
            v = (results[k].get("ragas_scores") or {}).get(mk)
            row += f"{(f'{v:.4f}' if isinstance(v, float) else 'N/A'):>{col}}"
        print(row)

    print("-" * width)
    lat_row = f"{'Latenz Ø (sek)':<20}"
    for k in sys_keys:
        v = results[k].get("durchschnittliche_latenz_sek")
        lat_row += f"{(f'{v:.1f}' if v is not None else 'N/A'):>{col}}"
    print(lat_row)
    print("=" * width)
    print(
        "\nHinweis: N/A = Metrik nicht berechenbar"
        " (keine Retrieval-Kontexte über Standard-API exponiert).\n"
    )


# ---------------------------------------------------------------------------
# Indexierungs-Hilfsfunktionen
# ---------------------------------------------------------------------------

def _extract_pdf_texts() -> list[tuple[str, str]]:
    """Extrahiert Text aus allen PDFs in data/uploads/. Gibt (name, text)-Paare zurück."""
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber nicht installiert — pip install pdfplumber")

    results = []
    pdfs = sorted(UPLOADS_DIR.glob("*.pdf"))
    log.info("Extrahiere Text aus %d PDFs …", len(pdfs))
    for pdf_path in pdfs:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            if text.strip():
                results.append((pdf_path.stem, text))
                log.info("  ✓ %s (%d Zeichen)", pdf_path.name, len(text))
            else:
                log.warning("  Kein Text extrahiert: %s", pdf_path.name)
        except Exception as e:
            log.error("  Fehler bei %s: %s", pdf_path.name, e)
    return results


def index_lightrag() -> None:
    """Indexiert alle PDFs aus data/uploads/ in LightRAG (v1.4.x API)."""
    try:
        from lightrag import LightRAG
        from lightrag.llm.openai import openai_complete_if_cache, openai_embed
        from lightrag.utils import EmbeddingFunc
    except ImportError:
        print("LightRAG nicht installiert — pip install lightrag-hku pdfplumber")
        sys.exit(1)

    LIGHTRAG_DIR.mkdir(parents=True, exist_ok=True)
    api_key = cfg.OPENAI_API_KEY

    async def _llm(prompt, system_prompt=None, history_messages=None, **kwargs):
        return await openai_complete_if_cache(
            model="gpt-4o-mini",
            prompt=prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            api_key=api_key,
            **kwargs,
        )

    async def _embed(texts: list[str]):
        import openai as _oai
        import numpy as _np
        MAX_CHARS = 30000
        truncated = [t[:MAX_CHARS] for t in texts]
        client = _oai.AsyncOpenAI(api_key=api_key)
        resp = await client.embeddings.create(
            model="text-embedding-3-large", input=truncated
        )
        return _np.array(
            [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]
        )

    rag = LightRAG(
        working_dir=str(LIGHTRAG_DIR),
        llm_model_func=_llm,
        embedding_func=EmbeddingFunc(embedding_dim=3072, max_token_size=8191, func=_embed),
    )

    texts = _extract_pdf_texts()
    log.info("Indexiere %d Dokumente in LightRAG …", len(texts))

    async def _insert_all():
        await rag.initialize_storages()   # v1.4.x: zwingend vor erstem Insert
        for name, text in texts:
            try:
                await rag.ainsert(text)
                log.info("  ✓ indexiert: %s", name)
            except Exception as e:
                log.error("  Fehler bei %s: %s", name, e)
        await rag.finalize_storages()

    asyncio.run(_insert_all())
    log.info("LightRAG Indexierung abgeschlossen: %s", LIGHTRAG_DIR)


def prepare_msraphrag_input() -> None:
    """Konvertiert PDFs zu .txt-Dateien für MS GraphRAG (muss manuell indexiert werden)."""
    input_dir = MSRAPHRAG_ROOT / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    texts = _extract_pdf_texts()
    for name, text in texts:
        out = input_dir / f"{name}.txt"
        out.write_text(text, encoding="utf-8")
        log.info("  ✓ geschrieben: %s", out.name)

    print(f"\n{len(texts)} Textdateien in {input_dir} erstellt.")
    print("\nNächste Schritte für MS GraphRAG:")
    print(f"  1. graphrag init --root {MSRAPHRAG_ROOT}")
    print(f"  2. {MSRAPHRAG_ROOT / 'settings.yaml'} anpassen:")
    print("       api_key: <OPENAI_API_KEY>")
    print("       model: gpt-4o-mini")
    print(f"  3. graphrag index --root {MSRAPHRAG_ROOT}")
    print(f"  4. python scripts/system_comparison.py --system msraphrag\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Parsed CLI-Argumente, lädt Fragen, evaluiert alle gewählten Systeme und speichert JSON-Ergebnisse."""
    parser = argparse.ArgumentParser(
        description="Systemvergleich: Eigenes GraphRAG vs. MS GraphRAG vs. LightRAG"
    )
    parser.add_argument(
        "--system",
        choices=["own", "msraphrag", "lightrag", "all"],
        default="all",
        help="Zu evaluierendes System (default: all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Nur Fragen anzeigen, keine API-Calls",
    )
    parser.add_argument(
        "--index-lightrag", action="store_true",
        help="PDFs in LightRAG indexieren und beenden",
    )
    parser.add_argument(
        "--index-msraphrag", action="store_true",
        help="PDFs als .txt für MS GraphRAG aufbereiten und beenden",
    )
    parser.add_argument(
        "--output", default=str(OUTPUT_PATH),
        help="Ausgabepfad für JSON-Ergebnisse",
    )
    args = parser.parse_args()

    if args.index_lightrag:
        index_lightrag()
        return

    if args.index_msraphrag:
        prepare_msraphrag_input()
        return

    # Fragen laden
    questions = load_comparison_questions()
    if not questions:
        log.error(
            "Keine Fragen gefunden. Bitte zuerst ausführen:\n"
            "  python scripts/generate_course_questions.py"
        )
        sys.exit(1)

    log.info("Vergleich: %d Fragen (%d Kurse × %d)", len(questions), 3, QUESTIONS_PER_KURS)

    if args.dry_run:
        print(f"\n{'='*64}\nDRY RUN — {len(questions)} Fragen:\n{'='*64}")
        for i, tq in enumerate(questions, 1):
            print(f"  {i:2}. [{tq.kurs_id}] {tq.question}")
        print()
        return

    # Immer Cloud-Modus für Evaluation
    cfg.LLM_MODE = "cloud"
    ragas_llm = _make_ragas_llm()
    ragas_emb = _make_ragas_embeddings()

    neo = Neo4jClient()
    adapters: list[SystemAdapter] = []

    if args.system in ("own", "all"):
        adapters.append(OwnSystemAdapter(neo))

    if args.system in ("msraphrag", "all"):
        ms = MSGraphRAGAdapter()
        if not ms.is_available():
            log.warning(
                "MS GraphRAG nicht indexiert (%s/output/ fehlt oder leer).\n"
                "  Zuerst ausführen: python scripts/system_comparison.py --index-msraphrag",
                MSRAPHRAG_ROOT,
            )
            if args.system == "msraphrag":
                sys.exit(1)
        else:
            adapters.append(ms)

    if args.system in ("lightrag", "all"):
        lr = LightRAGAdapter()
        if not lr.is_available():
            log.warning(
                "LightRAG nicht indexiert (%s fehlt oder leer).\n"
                "  Zuerst ausführen: python scripts/system_comparison.py --index-lightrag",
                LIGHTRAG_DIR,
            )
            if args.system == "lightrag":
                sys.exit(1)
        else:
            adapters.append(lr)

    if not adapters:
        log.error("Kein System verfügbar. Externe Systeme zuerst indexieren.")
        sys.exit(1)

    # Evaluation
    results: dict[str, Any] = {}
    meta = {
        "n_fragen_gesamt": len(questions),
        "fragen_pro_kurs": QUESTIONS_PER_KURS,
        "ragas_judge": "gpt-4o-mini",
        "embedding_modell_ragas": "text-embedding-3-large",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    system_results: dict[str, Any] = {}
    for adapter in adapters:
        key = adapter.name.lower().replace(" ", "_").replace("-", "_")
        log.info("=== Evaluiere: %s ===", adapter.name)
        system_results[key] = _eval_adapter(adapter, questions, ragas_llm, ragas_emb)

    neo.close()

    results["meta"] = meta
    results.update(system_results)

    # Ausgabe
    _print_comparison_table(system_results)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info("Ergebnisse gespeichert: %s", out)


if __name__ == "__main__":
    main()
