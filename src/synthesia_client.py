from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional, List

import httpx
from pptx import Presentation
from PIL import Image, ImageDraw, ImageFont

import src.config as cfg


DEFAULT_W = 1280
DEFAULT_H = 720


def extract_slide_texts(pptx_path: str) -> List[str]:
    prs = Presentation(pptx_path)
    slides_texts = []
    for slide in prs.slides:
        parts = []
        for shape in slide.shapes:
            try:
                if hasattr(shape, "text"):
                    t = shape.text.strip()
                    if t:
                        parts.append(t)
            except Exception:
                continue
        slides_texts.append("\n\n".join(parts))
    return slides_texts


def _make_image_from_text(text: str, out_path: Path, width: int = DEFAULT_W, height: int = DEFAULT_H,
                          bg_color: str = "white", title: Optional[str] = None) -> str:
    img = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 36)
        small = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
        small = ImageFont.load_default()

    y = 40
    if title:
        draw.text((60, y), title, fill="black", font=font)
        y += 70

    margin = 60
    try:
        import textwrap
        lines = textwrap.wrap(text or "", width=80)
    except Exception:
        lines = (text or "").splitlines()

    for line in lines:
        draw.text((margin, y), line, fill="black", font=small)
        y += 28
        if y > height - 60:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return str(out_path)


def _download_file(url: str, dst: Path, headers: Optional[dict] = None, timeout: int = 60):
    try:
        with httpx.stream("GET", url, headers=headers or {}, timeout=timeout) as r:
            r.raise_for_status()
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "wb") as fh:
                for chunk in r.iter_bytes(chunk_size=8192):
                    fh.write(chunk)
    except Exception as e:
        raise RuntimeError(f"Failed to download file from {url}: {e}")


def generate_video_from_pptx_via_synthesia(pptx_path: str,
                                           out_dir: str,
                                           voice: str = "en-US",
                                           api_key: Optional[str] = None,
                                           api_base: Optional[str] = None,
                                           wait: bool = True,
                                           total_minutes: Optional[float] = None,
                                           per_slide_seconds: Optional[float] = None,
                                           fallback_local: bool = True,
                                           width: int = DEFAULT_W,
                                           height: int = DEFAULT_H) -> str:
    """
    Minimal Synthesia integration wrapper (best-effort):
    - Renders slides to images
    - Calls Synthesia-like POST /videos endpoint with a scenes payload
    - Polls GET /videos/{id} until completion and downloads resulting MP4 if available

    Note: The real Synthesia API shape may differ; this wrapper is intentionally
    flexible and resilient. Configure SYNTHESIA_API_KEY and optionally SYNTHESIA_API_BASE
    in your `.env`.
    """
    api_key = api_key or cfg.SYNTHESIA_API_KEY
    api_base = api_base or cfg.SYNTHESIA_API_BASE
    if not api_key or not api_base:
        raise RuntimeError("Synthesia API key or base URL not configured (set SYNTHESIA_API_KEY and optional SYNTHESIA_API_BASE in .env)")

    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)

    slides = extract_slide_texts(pptx_path)
    n_slides = max(1, len(slides))

    # determine duration per slide
    if per_slide_seconds and per_slide_seconds > 0:
        per_slide = float(per_slide_seconds)
    elif total_minutes and total_minutes > 0:
        per_slide = float(total_minutes * 60.0 / n_slides)
    else:
        per_slide = 4.0

    # render images for all slides
    slide_images = []
    for i, s in enumerate(slides, start=1):
        out_p = Path(out_dir) / f"{Path(pptx_path).stem}_slide_{i:03d}.png"
        _make_image_from_text(s, out_p, width=width, height=height, title=(s.splitlines()[0][:120] if s else None))
        slide_images.append(str(out_p))

    # Build payload for Synthesia-like API
    scenes = []
    for idx, s in enumerate(slides):
        sc = {
            "script": {"type": "text", "input": s},
            # include image path as base64 if Synthesia accepts it; many APIs accept urls or assets
            "image_path": slide_images[idx] if idx < len(slide_images) else slide_images[0],
            "duration": per_slide,
        }
        scenes.append(sc)

    payload = {
        "title": Path(pptx_path).stem,
        "scenes": scenes,
        "voice": voice,
        "total_duration_seconds": per_slide * n_slides,
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    create_url = f"{api_base.rstrip('/')}/videos"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(create_url, json=payload, headers=headers)
            try:
                data = resp.json()
            except Exception:
                data = {"raw_text": resp.text}

            status_file = out_dir_p / (Path(pptx_path).stem + "_synthesia_status.json")
            status_file.write_text(json.dumps({"status_code": resp.status_code, "response": data}, indent=2), encoding="utf-8")

            # handle common HTTP errors
            if resp.status_code in (401, 403):
                if fallback_local:
                    try:
                        from src.video_from_pptx import generate_video_from_pptx
                        return str(generate_video_from_pptx(pptx_path, out_dir, duration_per_slide=per_slide, width=width, height=height))
                    except Exception as e:
                        raise RuntimeError(f"Synthesia auth/permission error and fallback failed: {e}")
                raise RuntimeError(f"Synthesia request failed: {resp.status_code} - {data}")

            # If response includes an id, poll for completion
            job_id = None
            if isinstance(data, dict) and ("id" in data or "video_id" in data):
                job_id = data.get("id") or data.get("video_id")

            # try to find direct download URL
            for key in ("video_url", "result_url", "output_url", "url", "download_url", "downloadUrl"):
                if isinstance(data, dict) and key in data:
                    _download_file(data[key], out_dir_p / (Path(pptx_path).stem + "_synthesia_video.mp4"), headers=headers)
                    return str(out_dir_p / (Path(pptx_path).stem + "_synthesia_video.mp4"))

            # poll if job id present
            if job_id and wait:
                poll_url_candidates = [f"{create_url.rstrip('/')}/{job_id}", f"{api_base.rstrip('/')}/videos/{job_id}"]
                for poll in poll_url_candidates:
                    try:
                        for _ in range(120):
                            r = client.get(poll, headers=headers, timeout=30.0)
                            try:
                                jd = r.json()
                            except Exception:
                                jd = {"raw_text": r.text}
                            status_file.write_text(json.dumps({"polled_url": poll, "status_code": r.status_code, "response": jd}, indent=2), encoding="utf-8")
                            # check for download URL
                            for k in ("video_url", "result_url", "output_url", "url", "download_url"):
                                if isinstance(jd, dict) and k in jd:
                                    _download_file(jd[k], out_dir_p / (Path(pptx_path).stem + "_synthesia_video.mp4"), headers=headers)
                                    return str(out_dir_p / (Path(pptx_path).stem + "_synthesia_video.mp4"))
                            # check status
                            status = None
                            if isinstance(jd, dict):
                                status = jd.get("status") or jd.get("state") or jd.get("job_status")
                            if status and str(status).lower() in ("done", "finished", "succeeded", "completed"):
                                # try to find nested mp4
                                if isinstance(jd, dict):
                                    for v in jd.values():
                                        if isinstance(v, str) and v.endswith(".mp4"):
                                            _download_file(v, out_dir_p / (Path(pptx_path).stem + "_synthesia_video.mp4"), headers=headers)
                                            return str(out_dir_p / (Path(pptx_path).stem + "_synthesia_video.mp4"))
                                break
                            time.sleep(3)
                    except Exception:
                        continue

            # fallback: if no video obtained, try local fallback if requested
            if fallback_local:
                try:
                    from src.video_from_pptx import generate_video_from_pptx
                    return str(generate_video_from_pptx(pptx_path, out_dir, duration_per_slide=per_slide, width=width, height=height))
                except Exception as e:
                    raise RuntimeError(f"No synthesia video and fallback failed: {e}")

            raise RuntimeError(f"Synthesia did not return a downloadable video. See status file: {status_file}")
    except Exception as e:
        raise
