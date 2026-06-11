"""잡 생명주기, 디스크 레이아웃, 스레드 안전 이벤트 큐."""
from __future__ import annotations

import json
import queue
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class JobState(str, Enum):
    QUEUED = "queued"
    CONVERTING = "converting"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    dir: Path
    upload_paths: list[Path]
    request_text: str
    output_type: str = "report"
    state: JobState = JobState.QUEUED
    step: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    events: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)

    @property
    def converted_dir(self) -> Path:
        return self.dir / "converted"

    @property
    def report_path(self) -> Path:
        return self.dir / "report.md"

    @property
    def log_path(self) -> Path:
        return self.dir / "codex_log.jsonl"

    @property
    def result_path(self) -> Path:
        return self.dir / "result.hwpx"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}

    def create(
        self,
        uploads: list[tuple[str, bytes]],
        request_text: str,
        output_type: str = "report",
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.base_dir / job_id
        (job_dir / "upload").mkdir(parents=True, exist_ok=True)

        upload_paths: list[Path] = []
        for filename, file_bytes in uploads:
            upload_path = job_dir / "upload" / filename
            upload_path.write_bytes(file_bytes)
            upload_paths.append(upload_path)
        (job_dir / "request.txt").write_text(request_text, encoding="utf-8")

        now = _now()
        job = Job(
            id=job_id,
            dir=job_dir,
            upload_paths=upload_paths,
            request_text=request_text,
            output_type=output_type,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = job
        self._write_status(job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def set_state(self, job: Job, state: JobState, step: str = "", error: str = "") -> None:
        job.state = state
        if step:
            job.step = step
        if error:
            job.error = error
        job.updated_at = _now()
        self._write_status(job)
        job.events.put({"type": "status", "state": state.value, "step": job.step, "error": job.error})

    def push_event(self, job: Job, event: dict[str, Any]) -> None:
        with job.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        job.events.put(event)

    def _write_status(self, job: Job) -> None:
        status = {
            "id": job.id,
            "state": job.state.value,
            "step": job.step,
            "error": job.error,
            "output_type": job.output_type,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        (job.dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
