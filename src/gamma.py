"""Gamma-API-Client zur Erzeugung von Präsentationen via Gamma.app."""
from __future__ import annotations
import os, time, json, pathlib, re
import requests
from typing import Optional, Dict, Any, List

GAMMA_BASE = os.getenv("GAMMA_BASE", "https://public-api.gamma.app/v0.2")
DEFAULT_EXPORT = os.getenv("GAMMA_EXPORT", "pptx")  # 'pptx' oder 'pdf'

class GammaError(RuntimeError):
    """Fehlerklasse für fehlgeschlagene Gamma-API-Anfragen."""

class GammaClient:
    """HTTP-Client für die Gamma.app REST-API."""
    def __init__(self, api_key: Optional[str] = None, base: str = GAMMA_BASE):
        self.api_key = api_key or os.getenv("GAMMA_API_KEY")
        if not self.api_key:
            raise GammaError("GAMMA_API_KEY fehlt (.env).")
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"X-API-KEY": self.api_key, "Content-Type": "application/json"})

    def generate(self, body: Dict[str, Any]) -> str:
        """Startet eine Präsentationsgenerierung und gibt die generationId zurück."""
        url = f"{self.base}/generations"
        r = self.s.post(url, data=json.dumps(body), timeout=60)
        if r.status_code >= 400:
            raise GammaError(f"POST {url} failed [{r.status_code}]: {r.text}")
        gen_id = r.json().get("generationId")
        if not gen_id:
            raise GammaError(f"Kein generationId in Antwort: {r.text}")
        return gen_id

    def poll(self, generation_id: str, interval_sec: float = 5.0, timeout_sec: int = 600) -> Dict[str, Any]:
        """Wartet per Polling bis die Gamma-Generierung abgeschlossen ist und gibt das Ergebnis zurück."""
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
        """Lädt eine Gamma-Exportdatei herunter und gibt den lokalen Dateipfad zurück."""
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


    def list_themes(self) -> List[str]:
        """Versucht verfügbare Theme-Namen aus der Gamma-API zu ermitteln (Best-effort)."""
        candidates = [
            f"{self.base}/themes",
            f"{self.base}/templates",
            f"{self.base}/workspaces",
        ]

        names = []

        # 1) Try /themes or /templates which might return a list
        for url in candidates[:2]:
            try:
                r = self.s.get(url, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    # data could be list or dict with items
                    if isinstance(data, list):
                        # try to map element -> name
                        for it in data:
                            if isinstance(it, dict):
                                if "name" in it:
                                    names.append(it["name"])
                                elif "title" in it:
                                    names.append(it["title"])
                            elif isinstance(it, str):
                                names.append(it)
                    elif isinstance(data, dict):
                        # maybe {"themes": [...]}
                        for k in ("themes", "items", "data", "templates"):
                            if k in data and isinstance(data[k], list):
                                for it in data[k]:
                                    if isinstance(it, dict) and "name" in it:
                                        names.append(it["name"])
                                    elif isinstance(it, str):
                                        names.append(it)
                    if names:
                        return list(dict.fromkeys(names))
            except Exception:
                # ignore and try next
                pass

        # 2) Try workspaces -> for each workspace check /workspaces/{id}/themes
        try:
            wurl = f"{self.base}/workspaces"
            r = self.s.get(wurl, timeout=20)
            if r.status_code == 200:
                data = r.json()
                ws = []
                if isinstance(data, list):
                    ws = data
                elif isinstance(data, dict):
                    # common payload shape: {"workspaces":[...]}
                    for k in ("workspaces", "items", "data"):
                        if k in data and isinstance(data[k], list):
                            ws = data[k]
                            break
                for w in ws:
                    wid = None
                    if isinstance(w, dict):
                        wid = w.get("id") or w.get("workspaceId") or w.get("workspace_id")
                    elif isinstance(w, str):
                        wid = w
                    if not wid:
                        continue
                    try:
                        turl = f"{self.base}/workspaces/{wid}/themes"
                        rt = self.s.get(turl, timeout=20)
                        if rt.status_code == 200:
                            td = rt.json()
                            if isinstance(td, list):
                                for it in td:
                                    if isinstance(it, dict) and "name" in it:
                                        names.append(it["name"])
                                    elif isinstance(it, str):
                                        names.append(it)
                            elif isinstance(td, dict):
                                for k in ("themes", "items", "data"):
                                    if k in td and isinstance(td[k], list):
                                        for it in td[k]:
                                            if isinstance(it, dict) and "name" in it:
                                                names.append(it["name"])
                                            elif isinstance(it, str):
                                                names.append(it)
                    except Exception:
                        continue
                if names:
                    return list(dict.fromkeys(names))
        except Exception:
            pass

        raise GammaError("Couldn't discover themes from Gamma API (no themes endpoint responded).")


# ---- Helper: Text in Gamma-freundliches Input-Format umwandeln ----------------

def to_gamma_input_text(answer_text: str, supports: List[Dict[str, Any]], title: str = "Ergebnis") -> str:
    """Baut einen Gamma-inputText mit Folien-Trennern aus Antworttext, Bildern und Quellen."""
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
    # Include image supports as separate slides using Markdown image syntax
    for s in supports or []:
        img = s.get("image_uri") or s.get("image")
        if img:
            caption = (s.get("caption") or s.get("figure_label") or "Abbildung").strip()
            parts.append(f"# Abbildung\n![{caption}]({img})\n{caption}")
    if refs:
        parts.append("# Quellen\n" + "\n".join(refs[:25]))
    return "\n---\n".join(parts)
