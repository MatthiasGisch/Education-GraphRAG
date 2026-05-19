"""FastAPI-Endpunkte zur Präsentationsgenerierung aus Nutzeranfragen via Graph-Retrieval."""
from __future__ import annotations
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import os

from .neo import Neo4jClient
from .agent import answer_query
from .gamma_client import generate_presentation

app = FastAPI(title="Presentation Generator")

class CreateReq(BaseModel):
    """Anfrage-Schema für den POST /presentations Endpunkt."""
    query: str
    use_gamma: Optional[bool] = True
    web_mode: Optional[str] = None


@app.post("/presentations")
def create_presentation(req: CreateReq):
    """Beantwortet die Anfrage per Graph-Retrieval und generiert daraus eine Präsentation."""
    # 1) Connect to Neo4j
    try:
        neo = Neo4jClient()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j connection failed: {e}")

    # 2) Answer / retrieve
    out = answer_query(req.query, neo, web_mode=req.web_mode)
    answer = out.get("answer") or ""
    supports = out.get("supports") or []

    # 3) Generate presentation
    try:
        gen = generate_presentation(title=req.query, answer_text=answer, supports=supports, use_gamma=req.use_gamma)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Presentation generation failed: {e}")

    # 4) If local, return download path
    if gen.get("method") == "local":
        path = gen["result"]["path"]
        if not os.path.exists(path):
            raise HTTPException(status_code=500, detail="Generated file not found")
        return {"method": "local", "download_url": f"/presentations/download?path={os.path.abspath(path)}"}

    # 5) Gamma method -> return API response
    return {"method": "gamma", "result": gen.get("result")}


@app.get("/presentations/download")
def download_presentation(path: str):
    """Liefert eine generierte PPTX-Datei als Download-Response."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    # stream file
    return FileResponse(path, media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation', filename=os.path.basename(path))
