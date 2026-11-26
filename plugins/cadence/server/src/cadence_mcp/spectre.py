"""Spectre backend: version detection, runs (classic / APS / Spectre X), log analysis."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Settings
from .jobs import JobManager, new_run_dir, run_quick, tail
from .profiles import ProfileManager
from .safety import confine, require
from .versions import ToolVersion, parse_spectre, spectre_engine_args

_FORMATS = {"psfascii", "psfbin", "psfbinf", "psfxl", "sst2", "fsdb", "nutbin", "nutascii", "wdf"}


class SpectreBackend:
    def __init__(self, settings: Settings, jobs: JobManager, profiles: ProfileManager):
        self.settings = settings
        self.jobs = jobs
        self.profiles = profiles
        self._versions: dict[str | None, ToolVersion] = {}

    def exe(self, profile: str | None, tool: str = "spectre") -> str:
        exe = self.profiles.which(tool, profile)
        if not exe:
            raise FileNotFoundError(f"'{tool}' not found on PATH for profile {profile!r}")
        return exe

    def version(self, profile: str | None = None) -> ToolVersion:
        key = self.profiles.resolve_name(profile)
        if key not in self._versions:
            env = self.profiles.env(profile)
            exe = self.exe(profile)
            _, out_w = run_quick([exe, "-W"], env, self.settings.timeout_version_s)
            _, out_v = run_quick([exe, "-V"], env, self.settings.timeout_version_s)
            v = parse_spectre(out_w + "\n" + out_v)
            v.raw = (out_w.strip() + "\n" + out_v.strip()).strip()[:2000]
            self._versions[key] = v
        return self._versions[key]

    def run(self, netlist_path: str | None = None, netlist_text: str | None = None, engine: str = "spectre",
            preset: str | None = None, threads: int | None = None, output_format: str = "psfascii",
            extra_args: list[str] | None = None, timeout_s: float | None = None, wait: bool = True,
            profile: str | None = None) -> dict[str, Any]:
        require(self.settings, "run", "spectre_run")
        if bool(netlist_path) == bool(netlist_text):
            raise ValueError("give exactly one of netlist_path or netlist_text")
        if output_format not in _FORMATS:
            raise ValueError(f"output_format must be one of {sorted(_FORMATS)}")
        run = new_run_dir(self.settings.workdir, "spectre")
        if netlist_path:
            netlist = confine(netlist_path, self.settings)
            cwd = netlist.parent
        else:
            netlist = run / "input.scs"
            netlist.write_text(netlist_text or "", encoding="utf-8")
            cwd = run
        warnings: list[str] = []
        try:
            v: ToolVersion | None = self.version(profile)
        except Exception as exc:  # noqa: BLE001
            v = None
            warnings.append(f"could not query the Spectre version: {exc}")
        eng_args, w = spectre_engine_args(engine, preset, v)
        warnings += w
        log = run / "spectre.out"
        cmd = [self.exe(profile), str(netlist), "+escchars", "=log", str(log), "-format", output_format,
               "-raw", str(run / "psf"), "+lqtimeout", "900", "-maxw", "5", "-maxn", "5", "+logstatus"]
        cmd += eng_args
        if threads:
            cmd.append(f"+mt={int(threads)}")
        for a in extra_args or []:
            if a.startswith("=log") or a in ("-raw", "-r", "-format", "-f"):
                raise ValueError(f"{a!r} is managed by cadence-mcp; do not pass it in extra_args")
            cmd.append(a)
        job = self.jobs.start("spectre", cmd, cwd, run, self.profiles.env(profile), log_name="stdout.log",
                              spectre_log=str(log), raw_dir=str(run / "psf"),
                              spectre_version=v.label if v else None)
        timeout = self.settings.timeout_default_s if timeout_s is None else timeout_s
        done = self.jobs.wait(job, timeout) if wait else False
        out = job.to_dict(tail_lines=0 if done else 15)
        out["warnings"] = warnings
        if done:
            out["summary"] = summarize_log(log if log.exists() else Path(job.log))
            out["results"] = sorted(p.name for p in (run / "psf").glob("*") if p.is_file())[:200]
        else:
            out["message"] = "running in background: poll with cadence_get_run(run_id), then cadence_psf_read on the results"
        return out


_ERR = re.compile(r"^\s*ERROR\s*\(([A-Z]+-\d+)\)", re.M)
_WARN = re.compile(r"^\s*WARNING\s*\(([A-Z]+-\d+)\)", re.M)
_COMPLETE = re.compile(r"spectre completes with (\d+) errors?, (\d+) warnings?, and (\d+) notices?", re.I)
_ELAPSED = re.compile(r"Total time required for (\S+) analysis `(\S+)': CPU = ([^,]+), elapsed = ([^,\n]+)")
_CONV = re.compile(r"(No DC solution found|Convergence failure|failed to converge|Timestep too small|"
                   r"dynamically reducing|Newton iteration fails)", re.I)


def summarize_log(path: Path, max_messages: int = 25) -> dict[str, Any]:
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        return {"error": str(exc)}
    lines = text.splitlines()
    errors, warnings = [], []
    for i, ln in enumerate(lines):
        if _ERR.match(ln) and len(errors) < max_messages:
            errors.append("\n".join(x.strip() for x in lines[i:i + 3]))
        elif _WARN.match(ln) and len(warnings) < max_messages:
            warnings.append("\n".join(x.strip() for x in lines[i:i + 2]))
    m = _COMPLETE.search(text)
    out: dict[str, Any] = {
        "log": str(path),
        "completed": bool(m),
        "counts": {"errors": int(m.group(1)), "warnings": int(m.group(2)), "notices": int(m.group(3))} if m else None,
        "error_ids": dict(Counter(_ERR.findall(text)).most_common(20)),
        "warning_ids": dict(Counter(_WARN.findall(text)).most_common(20)),
        "errors": errors,
        "warnings_sample": warnings[:10],
        "analyses": [{"type": a, "name": n, "cpu": c.strip(), "elapsed": e.strip()} for a, n, c, e in _ELAPSED.findall(text)],
        "convergence_issues": sorted(set(x.lower() for x in _CONV.findall(text)))[:10],
    }
    ver = re.search(r"(?:sub-version|version)\s+([\d.]+\S*)", text)
    if ver:
        out["spectre_version"] = ver.group(1)
    if not m:
        out["tail"] = tail(path, 30)
    return out
