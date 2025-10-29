from __future__ import annotations
import base64
import json
import logging
from pathlib import Path
from typing import Optional

import requests
from pptx import Presentation
from PIL import Image, ImageDraw, ImageFont

import src.config as cfg

log = logging.getLogger(__name__)

DEFAULT_W = 1280
DEFAULT_H = 720


def extract_slide_texts(pptx_path: str):
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
    lines = []
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


def render_first_slide_image_from_pptx(pptx_path: str, out_dir: str) -> str:
    """Render the first slide as a simple image (text-based) and return path."""
    slides = extract_slide_texts(pptx_path)
    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)
    first = slides[0] if slides else ""
    title = None
    if "\n" in first:
        title = first.split("\n", 1)[0][:120]
    elif first:
        title = first[:120]
    out_path = out_dir_p / (Path(pptx_path).stem + "_first_slide.png")
    _make_image_from_text(first, out_path, width=DEFAULT_W, height=DEFAULT_H, title=title)
    return str(out_path)


def generate_video_from_pptx_via_did(pptx_path: str, out_dir: str, voice: str = "en-US", model: Optional[str] = None,
                                     api_key: Optional[str] = None, api_url: Optional[str] = None, wait: bool = True,
                                     total_minutes: Optional[float] = None, per_slide_seconds: Optional[float] = None,
                                     fallback_local: bool = True, width: int = DEFAULT_W, height: int = DEFAULT_H) -> str:
    """
    Best-effort D-ID integration:
    - Renders the first slide as an image
    - Concatenates slide texts into a single script
    - Sends a request to the D-ID API and (optionally) polls until a video URL is available
    - Downloads the MP4 into out_dir and returns the path.

    Note: Real D-ID API parameters may differ; this is implemented as a flexible, robust helper that
    inspects responses for common fields like 'id', 'video_url', 'result_url' and falls back to saving
    raw JSON for debugging.
    """
    api_key = api_key or cfg.DID_API_KEY
    api_url = api_url or cfg.DID_API_URL
    if not api_key or not api_url:
        raise RuntimeError("D-ID API key or URL not configured (set DID_API_KEY and DID_API_URL in .env)")

    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)

    # render all slides as images and build per-slide scripts
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

    # base64-encode images per slide
    images_b64 = []
    for p in slide_images:
        with open(p, "rb") as fh:
            images_b64.append(base64.b64encode(fh.read()).decode("ascii"))

    # Normalize api_key and build header variants.
    # Accept these common user inputs:
    # - raw token: "abcd..." -> try Bearer, raw Authorization, x-api-key
    # - full header: "Bearer abcd..." or "Basic xxxx" -> use as-is
    # - user:pass (contains ':') -> try Basic with base64(user:pass)
    token = (api_key or "").strip()
    common_header_variants = []
    if not token:
        common_header_variants = []
    else:
        low = token.lower()
        if low.startswith("bearer ") or low.startswith("basic ") or low.startswith("token "):
            # user pasted a full Authorization value
            common_header_variants.append({"Authorization": token})
        elif ":" in token and not token.startswith("http"):
            # maybe user pasted user:pass — try Basic base64
            import base64 as _b64
            b64 = _b64.b64encode(token.encode("utf-8")).decode("ascii")
            common_header_variants.append({"Authorization": f"Basic {b64}"})
            # also try raw and Bearer as fallback
            common_header_variants.append({"Authorization": f"Bearer {token}"})
            common_header_variants.append({"Authorization": token})
            common_header_variants.append({"x-api-key": token})
        else:
            # normal token: try Bearer, raw Authorization, x-api-key
            common_header_variants.append({"Authorization": f"Bearer {token}"})
            common_header_variants.append({"Authorization": token})
            common_header_variants.append({"x-api-key": token})

    # Best-effort payload: include scenes with per-slide durations if supported by the API
    scenes = []
    for idx, s in enumerate(slides):
        sc = {
            "script": {"type": "text", "input": s},
            "source_image": {"image_base64": images_b64[idx] if idx < len(images_b64) else images_b64[0]},
            "duration": per_slide,
        }
        scenes.append(sc)

    payload = {
        "scenes": scenes,
        "voice": voice,
        "duration_per_slide": per_slide,
        "total_duration_seconds": per_slide * n_slides,
    }
    if model:
        payload["model"] = model
    if model:
        payload["model"] = model

    # Save responses for each header attempt; prefer first that succeeds (non-401)
    status_file = out_dir_p / (Path(pptx_path).stem + "_did_status.json")
    resp = None
    data = None
    used_headers = None
    for hdr in common_header_variants:
        headers = {**hdr, "Content-Type": "application/json", "Accept": "application/json"}
        try:
            resp = requests.post(api_url, json=payload, headers=headers, timeout=60)
        except Exception as e:
            # save this attempt and try next header style
            status_file.write_text(json.dumps({"attempt_header": hdr, "error": str(e)}, indent=2), encoding="utf-8")
            resp = None
            continue

        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": resp.text}

        # save this attempt
        status_file.write_text(json.dumps({"attempt_header": hdr, "status_code": resp.status_code, "response": data}, indent=2), encoding="utf-8")

        # if unauthorized, try next header variant
        if resp.status_code == 401:
            resp = None
            data = None
            continue

        # detect insufficient credits / payment required
        if resp.status_code in (402, 403) or (isinstance(data, dict) and any(k in str(data).lower() for k in ("credit", "credits", "insufficient", "balance", "payment", "quota"))):
            status_file.write_text(json.dumps({"attempt_header": hdr, "status_code": resp.status_code, "response": data}, indent=2), encoding="utf-8")
            # fallback to local generation if requested
            if fallback_local:
                try:
                    from src.video_from_pptx import generate_video_from_pptx
                    local_mp4 = Path(out_dir) / (Path(pptx_path).stem + "_fallback_local.mp4")
                    mp = generate_video_from_pptx(pptx_path, out_dir, duration_per_slide=per_slide, width=width, height=height)
                    return str(mp)
                except Exception as e:
                    # save fallback error
                    status_file.write_text(json.dumps({"fallback_error": str(e)}, indent=2), encoding="utf-8")
            # otherwise continue trying other headers
            resp = None
            data = None
            continue

        used_headers = headers
        break

    # If no successful response was obtained, raise with pointer to status file
    if resp is None:
        raise RuntimeError(f"D-ID did not return a downloadable video. See status file: {status_file}")

    # Heuristics to find video URL or job id from successful response
    possible_video_url = None
    job_id = None
    for key in ("video_url", "result_url", "output_url", "url", "videoUrl", "resultUrl"):
        if isinstance(data, dict) and key in data:
            possible_video_url = data[key]
            break
    if isinstance(data, dict) and "id" in data:
        job_id = data["id"]

    # If we have a direct video URL, download
    if possible_video_url:
        mp4_path = out_dir_p / (Path(pptx_path).stem + "_did_video.mp4")
        _download_file(possible_video_url, mp4_path, headers=used_headers)
        return str(mp4_path)

    # If we have job id and wait is True, poll
    if job_id and wait:
        # attempt to poll a job-status endpoint (api_url/{id} or /jobs/{id})
        poll_urls = [f"{api_url.rstrip('/')}/{job_id}", f"{api_url.rstrip('/')}/jobs/{job_id}"]
        for poll in poll_urls:
            try:
                for _ in range(60):
                    r = requests.get(poll, headers=used_headers or {}, timeout=30)
                    try:
                        jd = r.json()
                    except Exception:
                        jd = {"raw_text": r.text}
                    # save last poll
                    status_file.write_text(json.dumps({"polled_url": poll, "status_code": r.status_code, "response": jd}, indent=2), encoding="utf-8")
                    # look for video URL
                    for k in ("video_url", "result_url", "output_url", "url", "videoUrl", "resultUrl"):
                        if isinstance(jd, dict) and k in jd:
                            _download_file(jd[k], out_dir_p / (Path(pptx_path).stem + "_did_video.mp4"), headers=used_headers)
                            return str(out_dir_p / (Path(pptx_path).stem + "_did_video.mp4"))
                    # check status fields
                    status = None
                    if isinstance(jd, dict):
                        status = jd.get("status") or jd.get("state") or jd.get("job_status")
                    if status and str(status).lower() in ("done", "finished", "succeeded", "completed"):
                        # maybe there's a nested output
                        if isinstance(jd, dict):
                            for v in jd.values():
                                if isinstance(v, str) and v.endswith(".mp4"):
                                    _download_file(v, out_dir_p / (Path(pptx_path).stem + "_did_video.mp4"), headers=headers)
                                    return str(out_dir_p / (Path(pptx_path).stem + "_did_video.mp4"))
                        break
                    import time
                    time.sleep(2)
            except Exception:
                continue

    # Fallback: return path to status file for debugging
    raise RuntimeError(f"D-ID did not return a downloadable video. See status file: {status_file}")


def _download_file(url: str, dst: Path, headers: Optional[dict] = None):
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=60)
        r.raise_for_status()
        with open(dst, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
    except Exception as e:
        raise RuntimeError(f"Failed to download file from {url}: {e}")
