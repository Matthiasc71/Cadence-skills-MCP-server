"""Background job manager for long Cadence runs (simulations, P&R, batch SKILL...)."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def tail(path: Path, lines: int = 60, max_bytes: int = 256_000) -> str:
    """Return the last ``lines`` lines of a text file (cheap even for huge logs)."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read().decode(errors="replace")
    except FileNotFoundError:
        return ""
    return "\n".join(data.splitlines()[-lines:]) if lines > 0 else data


def new_run_dir(workdir: Path, tool: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run = workdir / "runs" / f"{stamp}_{tool}_{uuid.uuid4().hex[:6]}"
    run.mkdir(parents=True, exist_ok=False)
    return run


@dataclass
class Job:
    id: str
    tool: str
    cmd: list[str]
    cwd: str
    run_dir: str
    log: str
    started: float
    proc: subprocess.Popen | None = None
    ended: float | None = None
    returncode: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.returncode is None

    def poll(self) -> None:
        if self.proc is not None and self.returncode is None:
            rc = self.proc.poll()
            if rc is not None:
                self.returncode = rc
                self.ended = time.time()
                self._close_log()
                self.save()

    def save(self) -> None:
        """Persist the job state next to its outputs so past runs stay inspectable after a restart."""
        try:
            (Path(self.run_dir) / "job.json").write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass

    def _close_log(self) -> None:
        fh = self.meta.pop("_logfh", None)
        if fh:
            fh.close()

    def to_dict(self, tail_lines: int = 0) -> dict[str, Any]:
        if self.proc is not None and self.returncode is None and self.proc.poll() is not None:
            self.poll()
        d = {
            "run_id": self.id,
            "tool": self.tool,
            "state": "running" if self.running else ("done" if self.returncode == 0 else "failed"),
            "returncode": self.returncode,
            "command": self.cmd,
            "cwd": self.cwd,
            "run_dir": self.run_dir,
            "log": self.log,
            "started": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
            "ended": datetime.fromtimestamp(self.ended).isoformat(timespec="seconds") if self.ended else None,
            "elapsed_s": round((self.ended or time.time()) - self.started, 1),
        }
        d.update({k: v for k, v in self.meta.items() if not k.startswith("_")})
        if tail_lines:
            d["log_tail"] = tail(Path(self.log), tail_lines)
        return d


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, tool: str, cmd: list[str], cwd: Path, run_dir: Path, env: dict[str, str],
              log_name: str = "stdout.log", **meta: Any) -> Job:
        log = run_dir / log_name
        fh = log.open("ab")
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
        job = Job(id=run_dir.name, tool=tool, cmd=[str(c) for c in cmd], cwd=str(cwd),
                  run_dir=str(run_dir), log=str(log), started=time.time(), proc=proc,
                  meta={**meta, "_logfh": fh})
        with self._lock:
            self._jobs[job.id] = job
        job.save()
        return job

    def wait(self, job: Job, timeout_s: float) -> bool:
        """Wait until the job ends or the timeout expires. Returns True if finished."""
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            job.poll()
            if not job.running:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

    def get(self, job_id: str) -> Job:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"no running or recent job {job_id!r} in this server session; "
                               "use cadence_list_runs to find past runs")
            return self._jobs[job_id]

    def find(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [j.to_dict() for j in sorted(jobs, key=lambda j: j.started, reverse=True)]

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        job.poll()
        if job.running and job.proc is not None:
            try:
                if os.name == "nt":
                    job.proc.terminate()
                else:
                    os.killpg(job.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                job.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(job.proc.pid, signal.SIGKILL)
                else:
                    job.proc.kill()
            job.poll()
        return job.to_dict(tail_lines=20)


def run_quick(cmd: list[str], env: dict[str, str], timeout_s: float, cwd: str | None = None) -> tuple[int, str]:
    """Run a short command (version queries...). Returns (returncode, combined output)."""
    try:
        out = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, timeout=timeout_s,
                             stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"") + (exc.stderr or b"")
        return -1, partial.decode(errors="replace") + f"\n[timeout after {timeout_s}s]"
    except OSError as exc:
        return -2, str(exc)
    return out.returncode, (out.stdout + out.stderr).decode(errors="replace")


_RUN_NAME = re.compile(r"^(\d{8})_(\d{6})_(.+)_([0-9a-f]{6})$")


def describe_run(run: Path, jobs: JobManager | None = None) -> dict[str, Any]:
    """Describe a run directory, from the live job if this session started it, else from job.json."""
    live = jobs.find(run.name) if jobs else None
    if live is not None:
        return live.to_dict()
    d: dict[str, Any] = {}
    meta = run / "job.json"
    if meta.exists():
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            d = {}
        if d.get("state") == "running":
            d["state"] = "unknown"  # the server that started it is gone; check the log
    m = _RUN_NAME.match(run.name)
    d.setdefault("run_id", run.name)
    d.setdefault("run_dir", str(run))
    if m:
        d.setdefault("tool", m.group(3))
        d.setdefault("started", datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat())
    d.setdefault("state", "unknown")
    return d


def list_runs(workdir: Path, jobs: JobManager | None = None, tool: str | None = None,
              state: str | None = None) -> list[dict[str, Any]]:
    root = workdir / "runs"
    if not root.is_dir():
        return []
    runs = [describe_run(p, jobs) for p in sorted(root.iterdir(), reverse=True) if p.is_dir()]
    if tool:
        runs = [r for r in runs if r.get("tool") == tool]
    if state:
        runs = [r for r in runs if r.get("state") == state]
    return runs
