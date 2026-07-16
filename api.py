from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import imageio_ffmpeg
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DOWNLOAD_ROOT = (BASE_DIR / os.getenv("DOWNLOAD_ROOT", "downloads")).resolve()
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
API_KEY = os.getenv("API_KEY", "")
COOKIE_BROWSER = os.getenv("COOKIE_BROWSER", "").strip()
ALLOWED_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()

app = FastAPI(title="Instagram yt-dlp API", version="1.0.0")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_key(authorization: str | None = Header(default=None)) -> None:
    if not API_KEY:
        raise HTTPException(503, "API_KEY is not configured")
    expected = f"Bearer {API_KEY}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Invalid or missing bearer token")


class DownloadRequest(BaseModel):
    url: str | None = None
    urls: list[str] = Field(default_factory=list, max_length=5000)
    mode: Literal["audio", "video"] = "audio"
    max_items: int | None = Field(default=None, ge=1, le=5000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("Only https://instagram.com URLs are accepted")
        return value.strip()

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, values: list[str]) -> list[str]:
        return [cls.validate_url(value) for value in values]  # type: ignore[list-item]

    @model_validator(mode="after")
    def require_source(self):
        if not self.url and not self.urls:
            raise ValueError("Provide url or urls")
        return self


def save_job(job: dict) -> None:
    job_dir = DOWNLOAD_ROOT / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")


def safe_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def collect_items(job_id: str, request: Request | None = None) -> list[dict]:
    job_dir = DOWNLOAD_ROOT / job_id
    items = []
    for info_path in sorted(job_dir.glob("*.info.json")):
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        media = next((p for p in job_dir.glob(f"{info_path.name[:-10]}.*")
                      if not p.name.endswith(".info.json") and p.is_file()), None)
        item = {
            "id": info.get("id"),
            "post_url": info.get("webpage_url") or info.get("original_url"),
            "title": safe_text(info.get("title")),
            "description": safe_text(info.get("description")),
            "timestamp": info.get("timestamp"),
            "duration_seconds": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "uploader": info.get("uploader") or info.get("channel"),
            "filename": media.name if media else None,
        }
        if request and media:
            item["download_url"] = str(request.url_for("download_file", job_id=job_id, filename=media.name))
        items.append(item)
    return items


def run_download(job_id: str, payload: DownloadRequest) -> None:
    job_dir = DOWNLOAD_ROOT / job_id
    archive = DOWNLOAD_ROOT / "download-archive.txt"
    log_path = job_dir / "yt-dlp.log"
    output = str(job_dir / "%(id)s.%(ext)s")
    cmd = [
        os.sys.executable, "-m", "yt_dlp", *(payload.urls or [payload.url]),
        "--ignore-errors", "--no-overwrites", "--continue",
        "--download-archive", str(archive), "--write-info-json",
        "--no-write-playlist-metafiles", "--restrict-filenames",
        "--output", output, "--newline",
        "--sleep-requests", "1", "--sleep-interval", "2", "--max-sleep-interval", "5",
        "--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe(),
    ]
    if COOKIE_BROWSER:
        cmd.extend(["--cookies-from-browser", COOKIE_BROWSER])
    if payload.max_items:
        cmd.extend(["--playlist-end", str(payload.max_items)])
    if payload.mode == "audio":
        cmd.extend(["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"])
    else:
        cmd.extend(["--format", "bv*+ba/b", "--merge-output-format", "mp4"])

    with LOCK:
        JOBS[job_id].update(status="running", started_at=now())
        save_job(JOBS[job_id])
    try:
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(cmd, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, text=True)
        items = collect_items(job_id)
        status = "completed" if result.returncode == 0 else "completed_with_errors" if items else "failed"
        with LOCK:
            JOBS[job_id].update(
                status=status, finished_at=now(), exit_code=result.returncode,
                item_count=len(items), error=None if items or result.returncode == 0 else "yt-dlp failed; inspect log",
            )
            save_job(JOBS[job_id])
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(status="failed", finished_at=now(), error=str(exc))
            save_job(JOBS[job_id])


@app.on_event("startup")
def load_jobs() -> None:
    for path in DOWNLOAD_ROOT.glob("*/job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if job.get("status") in {"queued", "running"}:
                job["status"] = "interrupted"
            JOBS[job["id"]] = job
        except (OSError, KeyError, json.JSONDecodeError):
            pass


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "instagram-yt-dlp-api"}


@app.post("/v1/jobs", status_code=202, dependencies=[Depends(require_key)])
def create_job(payload: DownloadRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    job_id = secrets.token_hex(8)
    job = {"id": job_id, "status": "queued", "created_at": now(), **payload.model_dump()}
    with LOCK:
        JOBS[job_id] = job
        save_job(job)
    background_tasks.add_task(run_download, job_id, payload)
    return {**job, "status_url": str(request.url_for("get_job", job_id=job_id))}


@app.get("/v1/jobs/{job_id}", name="get_job", dependencies=[Depends(require_key)])
def get_job(job_id: str, request: Request) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {**job, "items_url": str(request.url_for("get_items", job_id=job_id))}


@app.get("/v1/jobs/{job_id}/items", name="get_items", dependencies=[Depends(require_key)])
def get_items(job_id: str, request: Request) -> dict:
    if job_id not in JOBS:
        raise HTTPException(404, "Job not found")
    items = collect_items(job_id, request)
    return {"job_id": job_id, "count": len(items), "items": items}


@app.get("/v1/jobs/{job_id}/files/{filename}", name="download_file", dependencies=[Depends(require_key)])
def download_file(job_id: str, filename: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        raise HTTPException(400, "Invalid filename")
    path = (DOWNLOAD_ROOT / job_id / filename).resolve()
    expected_parent = (DOWNLOAD_ROOT / job_id).resolve()
    if path.parent != expected_parent or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=path.name)


@app.get("/v1/jobs/{job_id}/log", dependencies=[Depends(require_key)])
def get_log(job_id: str):
    path = DOWNLOAD_ROOT / job_id / "yt-dlp.log"
    if not path.is_file():
        raise HTTPException(404, "Log not found")
    return FileResponse(path, media_type="text/plain", filename=f"{job_id}.log")
