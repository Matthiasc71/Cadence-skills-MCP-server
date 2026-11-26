"""Virtuoso backend: live session through skillbridge, or batch (virtuoso -nograph)."""

from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Settings
from .jobs import JobManager, new_run_dir, tail
from .profiles import ProfileManager
from .safety import AccessDenied, check_skill, confine, require
from .versions import parse_virtuoso


def helper_path() -> Path:
    return Path(str(resources.files("cadence_mcp") / "skill" / "cadence_mcp.il"))


def skill_str(s: str) -> str:
    """Encode a Python string as a SKILL string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


@dataclass
class SkillResult:
    status: str  # ok | error | running
    output: str = ""
    value: str = ""
    kind: str = ""
    mode: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, limit: int) -> dict[str, Any]:
        def cut(s: str) -> str:
            return s if len(s) <= limit else s[:limit] + f"\n... [truncated, {len(s) - limit} more chars]"
        d = {"status": self.status, "mode": self.mode, "kind": self.kind,
             "value": cut(self.value), "output": cut(self.output)}
        d.update(self.extra)
        return d

    def json_value(self) -> Any:
        if self.status != "ok":
            raise RuntimeError(f"SKILL error ({self.mode}): {self.value}\n{self.output[-2000:]}")
        return json.loads(self.value, strict=False)


_MARK = re.compile(
    r"CDSMCP_STATUS (\S+)\s*\nCDSMCP_KIND (\S*)\s*\nCDSMCP_OUTPUT_BEGIN\n(.*?)\nCDSMCP_OUTPUT_END\n"
    r"CDSMCP_VALUE_BEGIN\n(.*?)\nCDSMCP_VALUE_END", re.S)


def parse_result_file(text: str) -> SkillResult | None:
    m = _MARK.search(text)
    if not m:
        return None
    return SkillResult(status=m.group(1), kind=m.group(2), output=m.group(3), value=m.group(4))


class VirtuosoBackend:
    def __init__(self, settings: Settings, jobs: JobManager, profiles: ProfileManager):
        self.settings = settings
        self.jobs = jobs
        self.profiles = profiles
        self._ws: Any = None
        self._ws_id: str | None = settings.skillbridge_id
        self._desynced = False
        self._last_fail = 0.0
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="skillbridge")

    # ------------------------------------------------------------------ live

    def _open_workspace(self, workspace_id: str | None, force_tcp: bool) -> Any:  # patched in tests
        from skillbridge import Workspace  # optional dependency
        return Workspace.open(workspace_id, force_tcp=force_tcp) if force_tcp else Workspace.open(workspace_id)

    def connect(self, workspace_id: str | None = None, force_tcp: bool | None = None) -> dict[str, Any]:
        with self._lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:  # noqa: BLE001
                    pass
            self._ws = None
            self._desynced = False
            self._ws_id = workspace_id if workspace_id is not None else self._ws_id
            tcp = self.settings.skillbridge_force_tcp if force_tcp is None else force_tcp
            try:
                self._ws = self._open_workspace(self._ws_id, tcp)
            except ImportError:
                self._last_fail = time.time()
                self.last_error = "skillbridge is not installed: pip install skillbridge"
                return {"connected": False, "error": self.last_error}
            except Exception as exc:  # noqa: BLE001
                self._last_fail = time.time()
                self.last_error = str(exc)
                return {"connected": False, "workspace_id": self._ws_id, "error": str(exc),
                        "hint": "In the Virtuoso CIW: load(\"<skillbridge path>\") then "
                                "pyStartServer(?id \"%s\"). Run 'cadence-mcp cdsinit' for the exact lines."
                                % (self._ws_id or "default")}
        self.last_error = None
        helpers = self._ensure_helpers()
        return {"connected": True, "workspace_id": self._ws_id or "default", "helpers": helpers}

    @property
    def live(self) -> bool:
        if self._ws is None and not self._desynced and time.time() - self._last_fail > 30:
            self.connect()
        return self._ws is not None and not self._desynced

    def _call(self, fn: str, *args: Any, timeout_s: float = 120) -> Any:
        if self._desynced:
            raise RuntimeError("skillbridge connection is out of sync after a timeout; in the CIW run "
                               "pyReloadScript() then pyStartServer(...), then call virtuoso_connect.")
        with self._lock:
            ws = self._ws
            fut = self._pool.submit(lambda: ws[fn](*args))
            try:
                return fut.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError:
                self._desynced = True
                raise TimeoutError(
                    f"Virtuoso did not answer within {timeout_s}s (busy, modal form open, or infinite loop). "
                    "The bridge is now out of sync: restart it with pyReloadScript() + pyStartServer() in the CIW."
                ) from None

    def _ensure_helpers(self) -> str:
        try:
            if self._call("isCallable", _Symbol("CdsMcpEval"), timeout_s=30):
                return "already loaded"
            self._call("load", str(helper_path()), timeout_s=60)
            return f"loaded {helper_path()}"
        except Exception as exc:  # noqa: BLE001
            return f"could not load helpers ({exc}); copy {helper_path()} next to Virtuoso and load it from .cdsinit"

    def _eval_live(self, code: str, timeout_s: float) -> SkillResult:
        if code.isascii():
            r = self._call("CdsMcpEval", code, timeout_s=timeout_s)
        else:  # skillbridge escapes non-ASCII as \\uXXXX, which SKILL does not understand
            tmp = new_run_dir(self.settings.workdir, "skill_live") / "code.il"
            tmp.write_text(code, encoding="utf-8")
            r = self._call("CdsMcpEvalFile", str(tmp), timeout_s=timeout_s)
        r = list(r) + ["", "", "", ""]
        return SkillResult(status=str(r[0]), output=str(r[1] or ""), value=str(r[2] or ""),
                           kind=str(r[3] or ""), mode="live")

    # ----------------------------------------------------------------- batch

    def virtuoso_bin(self, profile: str | None) -> str:
        exe = self.profiles.which("virtuoso", profile)
        if not exe:
            raise FileNotFoundError("'virtuoso' not found on PATH for this profile (set a profile setup script "
                                    "or CADENCE_MCP_BIN_VIRTUOSO)")
        return exe

    def _eval_batch(self, code: str, timeout_s: float, profile: str | None, cwd: str | None) -> SkillResult:
        exe = self.virtuoso_bin(profile)
        run = new_run_dir(self.settings.workdir, "virtuoso")
        user = run / "user_code.il"
        user.write_text(code, encoding="utf-8")
        result = run / "result.txt"
        wrapper = run / "wrapper.il"
        wrapper.write_text(
            f"load({skill_str(str(helper_path()))})\n"
            f"CdsMcpWriteResult({skill_str(str(result))} CdsMcpEvalFile({skill_str(str(user))}))\n"
            "exit()\n", encoding="utf-8")
        cmd = [exe, "-nograph", self.settings.virtuoso_batch_flag, str(wrapper), "-log", str(run / "CDS.log")]
        if self.settings.virtuoso_nocdsinit:
            cmd.insert(2, "-nocdsinit")
        workdir = self.batch_cwd(cwd)
        job = self.jobs.start("virtuoso", cmd, workdir, run, self.profiles.env(profile), log_name="stdout.log")
        done = self.jobs.wait(job, timeout_s)
        if not done:
            return SkillResult(status="running", mode="batch",
                               extra={"run_id": job.id, "run_dir": str(run),
                                      "message": "still running; poll with cadence_get_run(run_id)"})
        return self.collect_batch(run, job.returncode)

    def batch_cwd(self, cwd: str | None) -> Path:
        """Directory where batch Virtuoso/OCEAN start: it must hold cds.lib (and usually .cdsinit)."""
        if cwd:
            return confine(cwd, self.settings)
        return self.settings.project_dir or Path.cwd()

    @staticmethod
    def collect_batch(run: Path, returncode: int | None) -> SkillResult:
        result = run / "result.txt"
        parsed = parse_result_file(result.read_text(errors="replace")) if result.exists() else None
        log_tail = tail(run / "CDS.log", 40) or tail(run / "stdout.log", 40)
        if parsed is None:
            return SkillResult(status="error", mode="batch",
                               value="Virtuoso exited without writing a result (license, setup or crash?)",
                               extra={"returncode": returncode, "run_dir": str(run), "log_tail": log_tail})
        parsed.mode = "batch"
        parsed.extra = {"returncode": returncode, "run_dir": str(run)}
        if parsed.status != "ok":
            parsed.extra["log_tail"] = log_tail
        return parsed

    # ---------------------------------------------------------------- public

    def eval(self, code: str, mode: str = "auto", timeout_s: float = 120, profile: str | None = None,
             trusted: bool = False, cwd: str | None = None) -> SkillResult:
        mode = mode.lower()
        if mode not in ("auto", "live", "batch"):
            raise ValueError("mode must be auto, live or batch")
        if self._desynced and mode != "batch":
            raise RuntimeError("skillbridge connection is out of sync after a timeout; in the CIW run "
                               "pyReloadScript() then pyStartServer(...), then call virtuoso_connect "
                               "(or pass mode='batch' to use a separate virtuoso -nograph session).")
        use_live = mode == "live" or (mode == "auto" and self.live)
        if mode == "live" and not self.live:
            raise RuntimeError("no live Virtuoso session (skillbridge). Use mode='batch' or start the bridge.")
        if not trusted:
            check_skill(code, self.settings, live=use_live)
            if not use_live:
                require(self.settings, "run", "batch SKILL (virtuoso -nograph)")
        if use_live:
            return self._eval_live(code, timeout_s)
        return self._eval_batch(code, timeout_s, profile, cwd)

    def query_json(self, code: str, mode: str = "auto", timeout_s: float = 120,
                   profile: str | None = None, cwd: str | None = None) -> tuple[Any, str]:
        r = self.eval(f"CdsMcpJson({code})", mode=mode, timeout_s=timeout_s, profile=profile, trusted=True,
                      cwd=cwd)
        if r.status == "running":
            raise TimeoutError(f"batch query still running as run {r.extra.get('run_id')}; poll it with cadence_get_run")
        return r.json_value(), r.mode

    def info(self, mode: str = "auto", profile: str | None = None, cwd: str | None = None) -> dict[str, Any]:
        data, used = self.query_json("CdsMcpVersionInfo()", mode=mode, profile=profile, cwd=cwd)
        version = parse_virtuoso(f"{data.get('subVersion', '')} {data.get('version', '')}")
        features = {f["name"]: f["callable"] for f in data.get("features") or []}
        data["features"] = features
        data["parsed_version"] = version.as_dict()
        data["mode"] = used
        hints = list(version.notes)
        if features.get("maeOpenSetup"):
            hints.append("Runtime probe: the mae* API is callable -> drive maestro views with mae* functions; the "
                         "same code then runs on every release from IC6.1.7 to IC25.1.")
        else:
            hints.append("Runtime probe: no mae* API -> use OCEAN (portable) or the release's ADE L/XL APIs.")
        data["hints"] = hints
        return data

    def ciw_log(self, lines: int = 80) -> dict[str, Any]:
        path: Path | None = None
        if self.settings.cds_log:
            path = Path(self.settings.cds_log).expanduser()
        elif self.live:
            try:
                r = self._eval_live('if(isCallable(\'hiGetLogFileName) then hiGetLogFileName() else "")', 20)
                if r.status == "ok" and r.value:
                    path = Path(r.value)
            except Exception:  # noqa: BLE001
                path = None
        if path is None:
            candidates = sorted(
                [p for d in (Path.cwd(), Path.home()) for p in d.glob("CDS.log*") if p.is_file()],
                key=lambda p: p.stat().st_mtime, reverse=True)
            path = candidates[0] if candidates else None
        if path is None or not path.exists():
            return {"found": False, "hint": "set CADENCE_MCP_CDS_LOG to the CDS.log path"}
        text = tail(path, lines)
        errors = [ln for ln in text.splitlines() if re.search(r"\*Error\*|ERROR|\*WARNING\*", ln)]
        return {"found": True, "path": str(path), "tail": text, "error_lines": errors[-30:]}


class _Symbol:
    """Lazy wrapper so tests can run without skillbridge installed."""

    def __new__(cls, name: str):  # type: ignore[misc]
        try:
            from skillbridge import Symbol
            return Symbol(name)
        except ImportError:  # pragma: no cover
            return name


def ocean_command(backend: VirtuosoBackend, script: Path, run: Path, profile: str | None) -> list[str]:
    ocean = backend.profiles.which("ocean", profile)
    exe = ocean or backend.virtuoso_bin(profile)
    flag = "-restore" if ocean else backend.settings.virtuoso_batch_flag  # OCEAN documents -restore
    cmd = [exe, "-nograph", flag, str(script), "-log", str(run / "ocean.log")]
    if backend.settings.virtuoso_nocdsinit and not ocean:
        cmd.insert(2, "-nocdsinit")
    return cmd


def lint_code(path: Path, out: Path) -> str:
    return (f"sklint(?file {skill_str(str(path))} ?outputFile {skill_str(str(out))})\n"
            f"{skill_str(str(out))}")


def guard_file(path: Path, settings: Settings, live: bool) -> None:
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        raise AccessDenied(f"cannot read {path}: {exc}") from exc
    check_skill(text, settings, live=live)


__all__ = ["VirtuosoBackend", "SkillResult", "helper_path", "skill_str", "ocean_command", "lint_code",
           "guard_file", "parse_result_file"]
