# src/gamma.py
from __future__ import annotations
import os, time, json, pathlib, re
import requests
from typing import Optional, Dict, Any, List

GAMMA_BASE = os.getenv("GAMMA_BASE", "https://public-api.gamma.app/v0.2")
DEFAULT_EXPORT = os.getenv("GAMMA_EXPORT", "pptx")  # 'pptx' oder 'pdf'

class GammaError(RuntimeError):
    pass

class GammaClient:
    def __init__(self, api_key: Optional[str] = None, base: str = GAMMA_BASE):
        self.api_key = api_key or os.getenv("GAMMA_API_KEY")
        if not self.api_key:
            raise GammaError("GAMMA_API_KEY fehlt (.env).")
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"X-API-KEY": self.api_key, "Content-Type": "application/json"})

    def generate(self, body: Dict[str, Any]) -> str:
        """POST /generations → generationId"""
        url = f"{self.base}/generations"
        r = self.s.post(url, data=json.dumps(body), timeout=60)
        if r.status_code >= 400:
            raise GammaError(f"POST {url} failed [{r.status_code}]: {r.text}")
        gen_id = r.json().get("generationId")
        if not gen_id:
            raise GammaError(f"Kein generationId in Antwort: {r.text}")
        return gen_id

    def poll(self, generation_id: str, interval_sec: float = 5.0, timeout_sec: int = 600) -> Dict[str, Any]:
        """GET /generations/{id} (Status & ggf. Datei-URLs)"""
        url = f"{self.base}/generations/{generation_id}"
        t0 = time.time()
        while True:
            r = self.s.get(url, timeout=30)
            if r.status_code >= 400:
                raise GammaError(f"GET {url} failed [{r.status_code}]: {r.text}")
            data = r.json()
            status = data.get("status")
            if status == "completed":
                return data  # enthält gammaUrl und – wenn exportAs gesetzt – Datei-URLs
            if time.time() - t0 > timeout_sec:
                raise GammaError(f"Timeout beim Warten auf Generation {generation_id} (letzter Status: {status})")
            time.sleep(interval_sec)

    def download_file(self, file_url: str, out_dir: str = "outputs/gamma", filename: Optional[str] = None) -> str:
        pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
        name = filename or file_url.split("?")[0].split("/")[-1] or "deck.pptx"
        out_path = str(pathlib.Path(out_dir) / name)
        with self.s.get(file_url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 14):
                    if chunk:
                        f.write(chunk)
        return out_path


# ---- Helper: Text in Gamma-freundliches Input-Format umwandeln ----------------

def to_gamma_input_text(answer_text: str, supports: List[Dict[str, Any]], title: str = "Ergebnis") -> str:
    """
    Baut ein inputText mit expliziten Seiten-Trennern '---' zwischen Folien.
    Slide 1: Titel + kurzer Teaser
    Slide 2..n: Inhalte (aus Antwort – bereits gegliedert, wenn möglich)
    Letzte Slide: Quellen (aus supports)
    """
    def norm(s: str) -> str:
        s = re.sub(r"\r\n|\r", "\n", s or "")
        return s.strip()

    bullets = []
    # einfache Heuristik: Zeilen der Antwort → Bulletpoints (Erhalt vorhandener Listen)
    for line in norm(answer_text).split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("-", "*")) or re.match(r"^\d+\.", line):
            bullets.append(line)
        else:
            bullets.append(f"* {line}")

    # Quellen-Folie (aus den Paper-Belegen)
    seen = set()
    refs = []
    for s in supports or []:
        pid = (s.get("paper_id") or s.get("url") or s.get("doi"))
        if not pid or pid in seen:
            continue
        seen.add(pid)
        page = s.get("page")
        label = s.get("paper_title") or s.get("doi") or s.get("url") or pid
        if page is not None:
            refs.append(f"* {label} — S. {page}")
        else:
            refs.append(f"* {label}")

    parts = []
    parts.append(f"# {title}\n* Überblick der wichtigsten Ergebnisse")
    parts.append("\n".join(bullets[:40]))  # begrenzen, Gamma kann später verdichten
    if refs:
        parts.append("# Quellen\n" + "\n".join(refs[:25]))
    return "\n---\n".join(parts)
