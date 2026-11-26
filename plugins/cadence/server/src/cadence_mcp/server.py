"""cadence_mcp: MCP server for Cadence Design Systems tools.

Works with the MCP Python SDK 1.x (FastMCP) and 2.x (MCPServer). Every tool:
- is named cadence_<area>_<action> and carries full annotations;
- validates its arguments with Pydantic (Annotated[..., Field(...)]);
- returns markdown (default, compact) or json (response_format="json");
- paginates lists with limit/offset and returns total/count/has_more/next_offset;
- reports failures as tool errors that say what to do next.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

import anyio
from pydantic import Field

from . import __version__
from .config import Settings, load_settings
from .digital import TCL_TOOLS, DigitalBackend, parse_report, summarize_tool_log, summarize_xrun_log
from .env import TOOLS as KNOWN_TOOLS
from .env import discover
from .formatting import ResponseFormat, paginate, render
from .jobs import JobManager, describe_run, list_runs, new_run_dir, tail
from .pcb import PcbBackend
from .profiles import ProfileManager
from .psf import list_results, read_psf_ascii
from .safety import AccessDenied, check_skill, confine, require
from .spectre import SpectreBackend, summarize_log
from .versions import COMPAT_MATRIX_MD
from .virtuoso import VirtuosoBackend, guard_file, helper_path, lint_code, ocean_command, parse_result_file, skill_str

try:  # SDK 2.x
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

SERVER_NAME = "cadence_mcp"

INSTRUCTIONS = """\
Cadence tools (Virtuoso/SKILL/OCEAN, Spectre, Genus/Innovus/Tempus/Voltus, Xcelium, Allegro/OrCAD),
Virtuoso IC6.1.7 -> IC25.1 and the matching Spectre/digital/SPB releases.

1. Start with cadence_get_environment (tools + versions per profile, access level) and, for custom IC,
   cadence_virtuoso_get_info (live session via skillbridge if connected, else batch virtuoso -nograph).
2. Choose code from runtime feature probes (features in cadence_virtuoso_get_info, isCallable(),
   is_common_ui_mode) rather than version numbers. IC23.1 removed the ADE L/XL environments (adexl views must
   become maestro views) but kept the axl* session functions, so isCallable('axl...) never identifies a release.
3. Inspect before writing. Design edits need access level 'write'; explain refusals, never bypass them.
4. Long runs: wait=false, then cadence_get_run(run_id). Past runs: cadence_list_runs.
5. Keep Spectre output_format='psfascii' so cadence_psf_read can decode results.
6. Lists are paginated (limit/offset, next_offset). Use response_format='json' when you need every field.
"""

# ----------------------------------------------------------------------------- shared argument types

Fmt = Annotated[ResponseFormat, Field(
    description="'markdown' (default): compact, readable. 'json': every field, for precise extraction.")]
ProfileArg = Annotated[str | None, Field(
    description="Profile = one Cadence installation/version (see cadence_list_profiles), e.g. 'ic617', "
                "'ic231'. Omit for the default profile.")]
Mode = Annotated[Literal["auto", "live", "batch"], Field(
    description="'auto': the user's live Virtuoso (skillbridge) if connected, else batch; 'live': only the "
                "user's session; 'batch': a separate 'virtuoso -nograph' process.")]
Cwd = Annotated[str | None, Field(
    description="Batch only: directory holding cds.lib (default: configured project_dir, else the server cwd).")]
Limit = Annotated[int, Field(ge=1, le=500, description="Maximum items to return (page size).")]
Offset = Annotated[int, Field(ge=0, description="Items to skip; pass next_offset from the previous page.")]
Wait = Annotated[bool, Field(
    description="true: wait up to timeout_s and return results; false: return a run_id at once and poll "
                "with cadence_get_run.")]
TimeoutArg = Annotated[float | None, Field(
    gt=0, le=604800, description="Seconds to wait (default: server setting, 3600). The run keeps going "
                                 "in the background if the wait expires.")]
PathArg = Annotated[str, Field(min_length=1, description="Absolute path inside the allowed roots "
                                                         "(see cadence_get_environment).")]
LibName = Annotated[str, Field(min_length=1, max_length=256, description="Library name as in cds.lib, e.g. 'analogLib'.")]
CellName = Annotated[str, Field(min_length=1, max_length=256, description="Cell name, e.g. 'ota_2stage'.")]
ViewName = Annotated[str, Field(min_length=1, max_length=256, description="View name, e.g. 'schematic', 'layout', 'symbol'.")]

DigitalTool = Annotated[Literal["genus", "innovus", "tempus", "voltus", "joules"], Field(
    description="Digital implementation tool.")]

_RUN_ID = re.compile(r"^[\w.\-]+$")


def _ann(title: str, read_only: bool, destructive: bool = False, idempotent: bool = False) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=read_only, destructiveHint=destructive,
                           idempotentHint=idempotent, openWorldHint=False)


def _error_text(exc: Exception) -> str:
    msg = str(exc).strip("'\"") or type(exc).__name__
    if isinstance(exc, FileNotFoundError):
        return (f"not found: {msg}. Check the path (it must be inside the allowed roots listed by "
                "cadence_get_environment) or, for an executable, the profile's setup script or "
                "CADENCE_MCP_BIN_<TOOL>.")
    if isinstance(exc, (AccessDenied, TimeoutError, KeyError)):
        return msg
    if isinstance(exc, ValueError):
        return f"invalid argument: {msg}"
    return f"{type(exc).__name__}: {msg}"


def _make_server() -> tuple[Any, int]:
    try:
        from mcp.server.mcpserver import MCPServer  # SDK 2.x
        return MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=__version__), 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # SDK 1.x
        return FastMCP(SERVER_NAME, instructions=INSTRUCTIONS), 1


def build_server(settings: Settings | None = None) -> Any:
    settings = settings or load_settings()
    jobs = JobManager()
    profiles = ProfileManager(settings)
    virt = VirtuosoBackend(settings, jobs, profiles)
    spec = SpectreBackend(settings, jobs, profiles)
    dig = DigitalBackend(settings, jobs, profiles)
    pcb = PcbBackend(settings, jobs, profiles)
    server, sdk = _make_server()
    limit_chars = settings.max_output_chars

    def tool(title: str, read_only: bool, destructive: bool = False,
             idempotent: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a blocking tool: it runs in a worker thread so the event loop stays responsive
        during long Cadence runs, and every failure reaches the model as an actionable ToolError."""
        register = server.tool(annotations=_ann(title, read_only, destructive, idempotent),
                               structured_output=False)

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
                except ToolError:
                    raise
                except Exception as exc:  # noqa: BLE001 - surfaced to the model on purpose
                    raise ToolError(_error_text(exc)) from exc
            register(wrapper)
            return fn
        return deco

    def out(data: Any, fmt: ResponseFormat, title: str) -> str:
        return render(data, fmt, title, limit_chars)

    def run_path(run_id: str) -> Path:
        if not _RUN_ID.match(run_id):
            raise ValueError("run_id must be a run directory name as returned by cadence_list_runs")
        p = settings.workdir / "runs" / run_id
        if not p.is_dir():
            raise KeyError(f"unknown run {run_id!r}; use cadence_list_runs to see available runs")
        return p

    def run_summary(run: Path, info: dict[str, Any]) -> dict[str, Any] | None:
        tool_name = info.get("tool", "")
        if tool_name == "spectre" and (run / "spectre.out").exists():
            s = summarize_log(run / "spectre.out")
            s["results"] = sorted(p.name for p in (run / "psf").glob("*") if p.is_file())[:100]
            return s
        if tool_name in TCL_TOOLS:
            logs = sorted(run.glob(f"{tool_name}*.log*"))
            return summarize_tool_log(logs[0]) if logs else None
        if tool_name == "xrun" and (run / "xrun.log").exists():
            return summarize_xrun_log(run / "xrun.log")
        if tool_name == "virtuoso":
            return virt.collect_batch(run, info.get("returncode")).as_dict(limit_chars)
        if tool_name == "sklint" and (run / "sklint.txt").exists():
            return {"report": (run / "sklint.txt").read_text(errors="replace")[:limit_chars]}
        if tool_name in ("allegro", "capture") and (run / "result.txt").exists():
            r = parse_result_file((run / "result.txt").read_text(errors="replace"))
            return r.as_dict(limit_chars) if r else {"result": tail(run / "result.txt", 40)}
        if tool_name in ("ocean", "netlist") and (run / "ocean.log").exists():
            return {"ocean_log_tail": tail(run / "ocean.log", 40)}
        return None

    # ========================================================================= general

    @tool("Discover Cadence environment", read_only=True, idempotent=True)
    def cadence_get_environment(profile: ProfileArg = None,
                                check_versions: Annotated[bool, Field(description="Query each tool's version (can take up to a minute per tool because of license checks).")] = True,
                                tools: Annotated[list[str] | None, Field(description=f"Restrict to these tools: {', '.join(KNOWN_TOOLS)}.")] = None,
                                response_format: Fmt = "markdown") -> str:
        """Discover installed Cadence tools for a profile, with versions and version-specific notes,
        plus the server access level, work directory, allowed roots and profiles. Call this first.

        Returns: {profile, tools: {name: {path, version, notes}}, missing: [...], env: {CDSHOME...},
        server: {version, access, allow_shell, workdir, allowed_roots, skillbridge_live, profiles}}.
        Don't use for Virtuoso runtime features (use cadence_virtuoso_get_info)."""
        d = discover(settings, profiles, profile, check_versions=check_versions, only=tools)
        d["server"] = {"name": SERVER_NAME, "version": __version__, "mcp_sdk": sdk, "access": settings.access,
                       "allow_shell": settings.allow_shell, "workdir": str(settings.workdir),
                       "allowed_roots": [str(r) for r in settings.all_roots()],
                       "skillbridge_live": virt._ws is not None, "profiles": profiles.names()}
        return out(d, response_format, f"Cadence environment ({d['profile'] or 'default env'})")

    @tool("List Cadence profiles", read_only=True, idempotent=True)
    def cadence_list_profiles(response_format: Fmt = "markdown") -> str:
        """List configured profiles (one per Cadence installation/version, e.g. ic617, ic231, ic251) with
        their description and setup script. Every run/info tool accepts profile=<name>.

        Returns: [{name, description, setup, env_keys, default, loaded}]."""
        return out(profiles.describe(), response_format, "Profiles")

    @tool("Cadence version compatibility matrix", read_only=True, idempotent=True)
    def cadence_get_compatibility_matrix() -> str:
        """Markdown matrix of version differences: Virtuoso IC6.1.7..IC25.1 (ADE L/XL -> maestro),
        Spectre engines (APS, Spectre X from 19.1, FX), Genus/Innovus/Tempus default UI (Stylus vs
        legacy), Xcelium (irun -> xrun), Allegro/OrCAD batch scripting."""
        return COMPAT_MATRIX_MD

    @tool("List runs", read_only=True, idempotent=True)
    def cadence_list_runs(tool_name: Annotated[str | None, Field(description="Filter by tool: spectre, virtuoso, ocean, netlist, genus, innovus, tempus, voltus, joules, xrun, allegro, capture, sklint.")] = None,
                          state: Annotated[Literal["running", "done", "failed", "unknown"] | None, Field(description="Filter by state.")] = None,
                          limit: Limit = 20, offset: Offset = 0, response_format: Fmt = "markdown") -> str:
        """List runs stored in the work directory (this session's and earlier ones), newest first.
        Each run is a directory holding the generated script, logs and results.

        Returns: {total, count, offset, has_more, next_offset, items: [{run_id, tool, state, returncode,
        started, ended, elapsed_s, run_dir}]}. Use cadence_get_run(run_id) for logs and parsed results."""
        runs = list_runs(settings.workdir, jobs, tool_name, state)
        keep = ("run_id", "tool", "state", "returncode", "started", "ended", "elapsed_s", "run_dir")
        page = paginate([{k: r.get(k) for k in keep} for r in runs], offset, limit)
        return out(page, response_format, "Runs")

    @tool("Get run status and results", read_only=True, idempotent=True)
    def cadence_get_run(run_id: Annotated[str, Field(min_length=1, description="run_id from a run tool or cadence_list_runs, e.g. '20261125_101500_spectre_a1b2c3'.")],
                        tail_lines: Annotated[int, Field(ge=0, le=2000, description="Lines of the main log to include (0 = none).")] = 40,
                        response_format: Fmt = "markdown") -> str:
        """State of a run (running/done/failed/unknown), timing, command, files, the log tail and a
        parsed summary for finished runs: Spectre (errors, warnings, analyses, convergence, result
        files), Genus/Innovus/Tempus (message IDs, WNS/TNS), xrun (*E/*W, UVM counts), batch SKILL
        (status/value/output), Allegro/Capture (result).

        Returns: {run_id, tool, state, returncode, started, ended, elapsed_s, command, files, log_tail,
        summary}. Then use cadence_psf_read / cadence_read_text_file on the listed files."""
        run = run_path(run_id)
        d = describe_run(run, jobs)
        if d.get("state") != "running":
            summary = run_summary(run, d)
            if summary is not None:
                d["summary"] = summary
        d["files"] = [{"name": str(p.relative_to(run)), "bytes": p.stat().st_size}
                      for p in sorted(run.rglob("*")) if p.is_file()][:60]
        if tail_lines:
            log = Path(d["log"]) if d.get("log") else run / "stdout.log"
            d["log_tail"] = tail(log, tail_lines)
        return out(d, response_format, f"Run {run_id}")

    @tool("Cancel a run", read_only=False, destructive=True, idempotent=True)
    def cadence_cancel_run(run_id: Annotated[str, Field(min_length=1, description="run_id of a running job started by this server session.")]) -> str:
        """Terminate a running job (whole process group) started by this server session. Outputs written so
        far are kept. Returns the final state and the last log lines."""
        require(settings, "run", "cadence_cancel_run")
        return out(jobs.cancel(run_id), "markdown", f"Cancelled {run_id}")

    @tool("Find files", read_only=True, idempotent=True)
    def cadence_find_files(pattern: Annotated[str, Field(min_length=1, description="Glob relative to root, e.g. '**/spectre.out', '**/*.tran', '**/innovus*.log', '**/cds.lib'.")],
                           root: Annotated[str | None, Field(description="Directory to search (default: the work directory). Must be inside the allowed roots.")] = None,
                           limit: Limit = 50, offset: Offset = 0, response_format: Fmt = "markdown") -> str:
        """Find files by glob under an allowed directory (logs, reports, PSF results, netlists, cds.lib),
        sorted by path. Scans at most 50 000 entries.

        Returns: {total, count, offset, has_more, next_offset, items: [{path, bytes, modified}]}."""
        base = confine(root, settings) if root else settings.workdir
        matches = []
        for i, p in enumerate(base.glob(pattern)):
            if i >= 50_000:
                break
            if p.is_file():
                matches.append(p)
        matches.sort()
        items = [{"path": str(p), "bytes": p.stat().st_size,
                  "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
                 for p in matches[offset:offset + limit]]
        page = paginate(items, offset, limit, total=len(matches))
        return out(page, response_format, f"Files matching {pattern}")

    @tool("Read a text file", read_only=True, idempotent=True)
    def cadence_read_text_file(path: PathArg,
                               tail_lines: Annotated[int | None, Field(ge=1, le=20000, description="Return only the last N lines (big logs).")] = None,
                               head_lines: Annotated[int | None, Field(ge=1, le=20000, description="Return only the first N lines.")] = None,
                               response_format: Fmt = "markdown") -> str:
        """Read a text file (log, report, netlist, script, .cdsinit) inside the allowed roots. Prefer
        tail_lines for logs; output is truncated to the server limit.

        Returns: {path, bytes, truncated, text}."""
        p = confine(path, settings)
        if tail_lines:
            text = tail(p, tail_lines)
        else:
            with p.open("r", errors="replace") as fh:
                text = "".join(fh.readline() for _ in range(head_lines)) if head_lines else fh.read(limit_chars + 1)
        d = {"path": str(p), "bytes": p.stat().st_size, "truncated": len(text) > limit_chars, "text": text[:limit_chars]}
        return out(d, response_format, p.name)

    # ========================================================================= Virtuoso

    @tool("Connect to live Virtuoso", read_only=False, idempotent=True)
    def cadence_virtuoso_connect(workspace_id: Annotated[str | None, Field(description="skillbridge workspace id (the ?id given to pyStartServer); omit for the default.")] = None,
                                 force_tcp: Annotated[bool | None, Field(description="Use TCP sockets (Windows, or ?forceTcp t on the Virtuoso side).")] = None) -> str:
        """(Re)connect to the user's running Virtuoso through skillbridge and load the SKILL helpers.
        Prerequisite in the CIW: load("<skillbridge python_server.il>") then pyStartServer(...)
        ('cadence-mcp cdsinit' prints the exact lines). Use after a timeout desynchronised the bridge.

        Returns: {connected, workspace_id, helpers} or {connected: false, error, hint}."""
        return out(virt.connect(workspace_id, force_tcp), "markdown", "skillbridge")

    @tool("Virtuoso version and features", read_only=True, idempotent=True)
    def cadence_virtuoso_get_info(mode: Mode = "auto", profile: ProfileArg = None, cwd: Cwd = None,
                                  response_format: Fmt = "markdown") -> str:
        """Virtuoso version (getVersion), database type, graphic mode, CDSHOME and runtime feature probes:
        maeOpenSetup/maeRunSimulation (maestro), axlGetWindowSession (axl* session API, present in every
        release), sevSession (ADE L), schCheck, sklint, pcreCompile... plus hints derived from version and probes.

        Returns: {version, subVersion, parsed_version: {label, family, major, minor, notes}, dbType,
        graphic, cdsHome, workingDir, features: {fn: bool}, hints, mode}."""
        return out(virt.info(mode=mode, profile=profile, cwd=cwd), response_format, "Virtuoso")

    @tool("Evaluate SKILL code", read_only=False, destructive=True)
    def cadence_skill_eval(code: Annotated[str, Field(min_length=1, max_length=200_000, description="SKILL code; several expressions allowed, e.g. 'cv = dbOpenCellViewByType(\"amp\" \"ota\" \"schematic\" nil \"r\") length(cv~>instances)'.")],
                           mode: Mode = "auto",
                           timeout_s: Annotated[float, Field(gt=0, le=86400, description="Seconds before giving up (live: the bridge is then out of sync).")] = 120,
                           profile: ProfileArg = None, cwd: Cwd = None, response_format: Fmt = "markdown") -> str:
        """Evaluate SKILL in the user's live Virtuoso or a batch virtuoso -nograph. printf output is captured;
        the value of the last expression is returned (strings as-is, other values printed with %L).
        Guardrails: DB edits (dbSave, dbCreate*, obj~>attr = v...) need access 'write'; system()/sh()
        need allow_shell; exit() is refused in live mode. Avoid code that opens modal forms in live mode.
        Prefer the dedicated cadence_virtuoso_* tools for listing libraries/cells/cellviews.

        Returns: {status: ok|error|running, mode, kind, value, output, run_id?, run_dir?}."""
        return out(virt.eval(code, mode=mode, timeout_s=timeout_s, profile=profile, cwd=cwd).as_dict(limit_chars),
                   response_format, "SKILL")

    @tool("Load a SKILL file", read_only=False, destructive=True)
    def cadence_skill_load_file(path: PathArg, mode: Mode = "auto",
                                timeout_s: Annotated[float, Field(gt=0, le=86400, description="Seconds before giving up.")] = 300,
                                profile: ProfileArg = None, cwd: Cwd = None, response_format: Fmt = "markdown") -> str:
        """load() a .il/.ils file after screening its content with the same guardrails as cadence_skill_eval.
        Returns: {status, mode, kind, value, output}."""
        p = confine(path, settings)
        guard_file(p, settings, live=(mode != "batch" and virt.live))
        r = virt.eval(f"load({skill_str(str(p))})", mode=mode, timeout_s=timeout_s, profile=profile,
                      trusted=True, cwd=cwd)
        return out(r.as_dict(limit_chars), response_format, f"load {p.name}")

    @tool("Lint a SKILL file", read_only=True, idempotent=True)
    def cadence_skill_lint(path: PathArg, mode: Mode = "auto", profile: ProfileArg = None, cwd: Cwd = None,
                           response_format: Fmt = "markdown") -> str:
        """Run Cadence SKILL Lint (sklint) on a file: errors, warnings, hints and score.
        Returns: {status, mode, report}."""
        p = confine(path, settings)
        run = new_run_dir(settings.workdir, "sklint")
        report = run / "sklint.txt"
        r = virt.eval(lint_code(p, report), mode=mode, timeout_s=600, profile=profile, trusted=True, cwd=cwd)
        d = r.as_dict(limit_chars)
        if report.exists():
            d["report"] = report.read_text(errors="replace")[:limit_chars]
        return out(d, response_format, f"sklint {p.name}")

    @tool("List Virtuoso libraries", read_only=True, idempotent=True)
    def cadence_virtuoso_list_libraries(pattern: Annotated[str | None, Field(description="Glob on library names, e.g. 'tsmc*', '*_lib'.")] = None,
                                        limit: Limit = 50, offset: Offset = 0, mode: Mode = "auto",
                                        profile: ProfileArg = None, cwd: Cwd = None,
                                        response_format: Fmt = "markdown") -> str:
        """Libraries defined by cds.lib as seen by Virtuoso, sorted by name.
        Returns: {total, count, offset, has_more, next_offset, items: [{name, path}], mode}."""
        data, used = virt.query_json("CdsMcpListLibs()", mode=mode, profile=profile, cwd=cwd)
        libs = sorted(data or [], key=lambda x: x["name"])
        if pattern:
            libs = [x for x in libs if fnmatch.fnmatchcase(x["name"], pattern)]
        page = paginate(libs, offset, limit)
        page["mode"] = used
        return out(page, response_format, "Libraries")

    @tool("List cells of a library", read_only=True, idempotent=True)
    def cadence_virtuoso_list_cells(library: LibName,
                                    pattern: Annotated[str | None, Field(description="SKILL regular expression (rexMatchp) on cell names, e.g. '^ota', 'bias'.")] = None,
                                    limit: Limit = 50, offset: Offset = 0, mode: Mode = "auto",
                                    profile: ProfileArg = None, cwd: Cwd = None,
                                    response_format: Fmt = "markdown") -> str:
        """Cells of a library (sorted by name) with their views, one page at a time.
        Returns: {library, path, total, count, offset, has_more, next_offset, items: [{name, views}], mode}."""
        pat = skill_str(pattern) if pattern else "nil"
        data, used = virt.query_json(
            f"CdsMcpListCells({skill_str(library)} {pat} {int(offset)} {int(limit)})",
            mode=mode, profile=profile, cwd=cwd)
        page = paginate(data.get("cells") or [], offset, limit, total=int(data.get("total") or 0))
        page.update({"library": data.get("library"), "path": data.get("path"), "mode": used})
        return out(page, response_format, f"Cells of {library}")

    @tool("Describe a cellview", read_only=True, idempotent=True)
    def cadence_virtuoso_get_cellview(library: LibName, cell: CellName, view: ViewName = "schematic",
                                      limit: Annotated[int, Field(ge=1, le=500, description="Page size for the terminals, instances, nets and layers lists.")] = 50,
                                      offset: Offset = 0, mode: Mode = "auto", profile: ProfileArg = None,
                                      cwd: Cwd = None, response_format: Fmt = "markdown") -> str:
        """Open a cellview read-only and summarise it: type, bBox, counts, terminals with direction,
        instances (lib/cell/view, and CDF parameters for schematics), nets, layers (layout). The four
        lists are paged together with limit/offset; counts give the totals.

        Returns: {lib, cell, view, cellViewType, bBox, instanceCount, netCount, terminalCount, shapeCount,
        terminals, instances, nets, layers, offset, limit, mode}."""
        data, used = virt.query_json(
            f"CdsMcpCellViewInfo({skill_str(library)} {skill_str(cell)} {skill_str(view)} {int(offset)} {int(limit)})",
            mode=mode, profile=profile, timeout_s=300, cwd=cwd)
        data.update({"offset": offset, "limit": limit, "mode": used})
        biggest = max(int(data.get(k) or 0) for k in ("instanceCount", "netCount", "terminalCount"))
        data["has_more"] = offset + limit < biggest
        data["next_offset"] = offset + limit if data["has_more"] else None
        return out(data, response_format, f"{library}/{cell}/{view}")

    @tool("Describe the cellview open in Virtuoso", read_only=True, idempotent=True)
    def cadence_virtuoso_get_current_cellview(limit: Limit = 50, offset: Offset = 0,
                                              response_format: Fmt = "markdown") -> str:
        """Summary of the cellview open in the user's current Virtuoso window (live session only), same
        fields as cadence_virtuoso_get_cellview."""
        data, _ = virt.query_json(f"CdsMcpCurrentCellView({int(offset)} {int(limit)})", mode="live")
        return out(data, response_format, "Current cellview")

    @tool("Check a schematic", read_only=False, destructive=True, idempotent=True)
    def cadence_virtuoso_check_schematic(library: LibName, cell: CellName, view: ViewName = "schematic",
                                         save: Annotated[bool, Field(description="Save the cellview after checking (check-and-save).")] = False,
                                         mode: Mode = "auto", profile: ProfileArg = None, cwd: Cwd = None,
                                         response_format: Fmt = "markdown") -> str:
        """Run schCheck on a schematic (and save it when save=true). Needs access 'write' because schCheck
        updates markers and the check timestamp. Returns: {errors, warnings, saved, mode}."""
        require(settings, "write", "cadence_virtuoso_check_schematic")
        data, used = virt.query_json(
            f"CdsMcpSchCheck({skill_str(library)} {skill_str(cell)} {skill_str(view)} {'t' if save else 'nil'})",
            mode=mode, profile=profile, timeout_s=300, cwd=cwd)
        data["mode"] = used
        return out(data, response_format, f"schCheck {library}/{cell}/{view}")

    @tool("Read the CIW log", read_only=True, idempotent=True)
    def cadence_virtuoso_read_ciw_log(lines: Annotated[int, Field(ge=1, le=5000, description="Lines to return from the end of CDS.log.")] = 80,
                                      response_format: Fmt = "markdown") -> str:
        """Tail of the CIW log (CDS.log) with *Error*/*WARNING* lines extracted.
        Returns: {found, path, tail, error_lines}."""
        return out(virt.ciw_log(lines), response_format, "CIW log")

    @tool("Run an OCEAN script", read_only=False)
    def cadence_ocean_run_script(script: Annotated[str, Field(min_length=1, max_length=200_000, description="OCEAN code, e.g. simulator('spectre) design(...) analysis('ac ...) run() ocnPrint(...). exit() is appended.")],
                                 cwd: Cwd = None, timeout_s: TimeoutArg = None, wait: Wait = True,
                                 profile: ProfileArg = None, response_format: Fmt = "markdown") -> str:
        """Run an OCEAN script in batch (ocean -restore, or virtuoso -nograph when ocean is absent). Works on
        every Virtuoso release, so it is the portable way to script simulations. Write numbers with
        ocnPrint(?output "file") to read them back with cadence_read_text_file.

        Returns: {run_id, state, returncode, run_dir, log_tail, ocean_log_tail}."""
        require(settings, "run", "cadence_ocean_run_script")
        check_skill(script, settings, live=False)
        run = new_run_dir(settings.workdir, "ocean")
        f = run / "script.ocn"
        f.write_text(script.rstrip() + "\nexit()\n", encoding="utf-8")
        job = jobs.start("ocean", ocean_command(virt, f, run, profile), virt.batch_cwd(cwd), run,
                         profiles.env(profile), log_name="stdout.log")
        done = jobs.wait(job, settings.timeout_default_s if timeout_s is None else timeout_s) if wait else False
        d = job.to_dict(tail_lines=40 if done else 10)
        if done:
            d["ocean_log_tail"] = tail(run / "ocean.log", 60)
        return out(d, response_format, "OCEAN")

    @tool("Netlist a cellview", read_only=False)
    def cadence_virtuoso_create_netlist(library: LibName, cell: CellName, view: ViewName = "schematic",
                                        simulator: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", description="OCEAN simulator name: spectre, spectreVerilog, ams, hspiceD...")] = "spectre",
                                        cwd: Cwd = None,
                                        timeout_s: Annotated[float, Field(gt=0, le=86400, description="Seconds to wait.")] = 900,
                                        profile: ProfileArg = None, response_format: Fmt = "markdown") -> str:
        """Netlist a cellview with OCEAN (simulator/design/createNetlist) in a fresh run directory.
        Returns: {run_id, state, netlists: [paths], netlist_head (first 60 lines)} — pass the netlist path
        to cadence_spectre_run."""
        require(settings, "run", "cadence_virtuoso_create_netlist")
        run = new_run_dir(settings.workdir, "netlist")
        script = (
            f'envSetVal("asimenv.startup" "projectDir" \'string {skill_str(str(run / "simulation"))})\n'
            f"simulator('{simulator})\n"
            f"design({skill_str(library)} {skill_str(cell)} {skill_str(view)})\n"
            f"resultsDir({skill_str(str(run / 'results'))})\n"
            'printf("CDSMCP_NETLIST %L\\n" createNetlist(?recreateAll t ?display nil))\n'
            "exit()\n")
        f = run / "netlist.ocn"
        f.write_text(script, encoding="utf-8")
        job = jobs.start("netlist", ocean_command(virt, f, run, profile), virt.batch_cwd(cwd), run,
                         profiles.env(profile), log_name="stdout.log")
        jobs.wait(job, timeout_s)
        d = job.to_dict()
        netlists = [p for p in run.rglob("*") if p.is_file() and p.name in ("input.scs", "netlist", "input.ckt", "verilog.inc")]
        d["netlists"] = [str(p) for p in netlists]
        if netlists:
            d["netlist_head"] = "\n".join(netlists[0].read_text(errors="replace").splitlines()[:60])
        else:
            d["log_tail"] = tail(run / "ocean.log", 60) or tail(Path(job.log), 60)
        return out(d, response_format, f"Netlist {library}/{cell}/{view}")

    # ========================================================================= Spectre

    @tool("Spectre version", read_only=True, idempotent=True)
    def cadence_spectre_get_info(profile: ProfileArg = None, response_format: Fmt = "markdown") -> str:
        """Spectre version (spectre -W / -V) with engine notes (APS everywhere; Spectre X presets cx..vx from
        SPECTRE 19.1). Returns: {label, family, major, minor, patch, isr, notes, raw}."""
        return out(spec.version(profile).as_dict(), response_format, "Spectre")

    @tool("Run Spectre", read_only=False)
    def cadence_spectre_run(netlist_path: Annotated[str | None, Field(description="Netlist file; Spectre runs in its directory so relative includes resolve.")] = None,
                            netlist_text: Annotated[str | None, Field(max_length=2_000_000, description="Netlist content, when there is no file.")] = None,
                            engine: Annotated[Literal["spectre", "aps", "x"], Field(description="'spectre' (classic), 'aps' (+aps), 'x' (Spectre X; falls back to +aps before SPECTRE 19.1).")] = "spectre",
                            preset: Annotated[Literal["cx", "ax", "mx", "lx", "vx"] | None, Field(description="Spectre X preset: cx most accurate ... vx fastest (default cx).")] = None,
                            threads: Annotated[int | None, Field(ge=1, le=256, description="+mt=N multithreading.")] = None,
                            output_format: Annotated[Literal["psfascii", "psfbin", "psfxl", "sst2", "fsdb", "nutascii", "nutbin"], Field(description="Keep 'psfascii' to read results with cadence_psf_read.")] = "psfascii",
                            extra_args: Annotated[list[str] | None, Field(description="Additional Spectre arguments, e.g. ['+postlayout=upa']. =log, -raw and -format are managed.")] = None,
                            timeout_s: TimeoutArg = None, wait: Wait = True, profile: ProfileArg = None,
                            response_format: Fmt = "markdown") -> str:
        """Run Spectre on a netlist file or text (exactly one of the two). Results go to <run_dir>/psf and the
        log to <run_dir>/spectre.out.

        Returns: {run_id, state, returncode, run_dir, raw_dir, spectre_version, warnings,
        summary: {completed, counts, error_ids, errors, analyses, convergence_issues}, results: [files]}."""
        d = spec.run(netlist_path, netlist_text, engine, preset, threads, output_format, extra_args,
                     timeout_s, wait, profile)
        return out(d, response_format, "Spectre run")

    @tool("Summarise a Spectre log", read_only=True, idempotent=True)
    def cadence_spectre_summarize_log(log_path: PathArg, response_format: Fmt = "markdown") -> str:
        """Parse a Spectre log (spectre.out): completion counts, error/warning IDs with the first messages,
        analyses with CPU/elapsed time, convergence issues, Spectre version."""
        return out(summarize_log(confine(log_path, settings)), response_format, "Spectre log")

    @tool("List PSF result files", read_only=True, idempotent=True)
    def cadence_psf_list_results(raw_dir: PathArg, limit: Limit = 50, offset: Offset = 0,
                                 response_format: Fmt = "markdown") -> str:
        """List the files of a Spectre results directory (psf) and whether each is PSF ASCII (readable by
        cadence_psf_read). Returns: {total, count, offset, has_more, next_offset, items: [{file, bytes, psf_ascii}]}."""
        return out(paginate(list_results(confine(raw_dir, settings)), offset, limit), response_format, "PSF results")

    @tool("Read PSF results", read_only=True, idempotent=True)
    def cadence_psf_read(path: PathArg,
                         signals: Annotated[list[str] | None, Field(description="Signal names or globs, e.g. ['out', 'I0:*']. Omit to list the signals.")] = None,
                         max_points: Annotated[int, Field(ge=2, le=5000, description="Rows returned per signal after uniform downsampling.")] = 200,
                         signal_limit: Annotated[int, Field(ge=1, le=2000, description="Page size of the signal-name list.")] = 100,
                         signal_offset: Offset = 0, response_format: Fmt = "markdown") -> str:
        """Read a PSF ASCII result file (tran, ac, dc, noise, dcOp...). Without signals: header, sweep and a
        page of signal names (or the scalar values of non-swept results such as dcOp). With signals:
        downsampled rows plus min/max/first/last/mean; for complex (AC) data mag_db, phase, the -3 dB
        frequency (x_at_minus_3db), unity-gain frequency (x_at_unity) and phase there.

        Returns: {analysis_type, sweep, points, x_range, signal_count, signals: {total, items...},
        data: {signal: {...}}, values (non-swept)}."""
        r = read_psf_ascii(confine(path, settings), signals, max_points)
        all_signals = r.pop("signals", [])
        r["signals"] = paginate(all_signals, signal_offset, signal_limit, total=None) if not signals else {
            "total": r.get("signal_count")}
        return out(r, response_format, Path(path).name)

    # ========================================================================= digital

    @tool("Digital tool version", read_only=True, idempotent=True)
    def cadence_digital_get_info(tool_name: Annotated[Literal["genus", "innovus", "tempus", "voltus", "joules", "xrun"], Field(description="Tool to query.")],
                                 profile: ProfileArg = None, response_format: Fmt = "markdown") -> str:
        """Version (<tool> -version) and UI notes: Genus defaults to Stylus Common UI (-legacy_ui for legacy);
        Innovus/Tempus/Voltus default to legacy (-stylus for Common UI). Returns: {label, major, minor, isr, notes}."""
        return out(dig.version(tool_name, profile).as_dict(), response_format, tool_name)

    @tool("Run a Tcl script in a digital tool", read_only=False)
    def cadence_digital_run_tcl(tool_name: DigitalTool,
                                script: Annotated[str | None, Field(max_length=500_000, description="Tcl script content.")] = None,
                                script_path: Annotated[str | None, Field(description="Existing Tcl script (inside the allowed roots).")] = None,
                                ui: Annotated[Literal["auto", "stylus", "legacy"], Field(description="'auto' detects Stylus (set_db, place_opt_design...) vs legacy (setDesignMode, dbGet...) commands.")] = "auto",
                                cpus: Annotated[int | None, Field(ge=1, le=512, description="CPU count, set with the dialect's own command.")] = None,
                                cwd: Annotated[str | None, Field(description="Working directory (inside the allowed roots); default: the run directory.")] = None,
                                timeout_s: TimeoutArg = None, wait: Wait = True, profile: ProfileArg = None,
                                response_format: Fmt = "markdown") -> str:
        """Run a Tcl script (exactly one of script / script_path) in genus, innovus, tempus, voltus or joules
        without GUI. Adds -stylus (Innovus/Tempus/Voltus) or -legacy_ui (Genus) when needed, appends exit.

        Returns: {run_id, state, ui, tool_logs, summary: {errors, warnings, error_ids, warning_ids,
        first_errors, timing: {wns_ns_table_last, tns_ns_table_last, violating_paths_last,
        worst_path_slack, area_last}}}."""
        d = dig.run_tcl(tool_name, script, script_path, ui, cpus, cwd, timeout_s, wait, profile)
        return out(d, response_format, f"{tool_name} run")

    @tool("Summarise a digital tool log", read_only=True, idempotent=True)
    def cadence_digital_summarize_log(path: PathArg, response_format: Fmt = "markdown") -> str:
        """Summarise a Genus/Innovus/Tempus/Voltus log: error and warning counts by message ID, first errors,
        WNS/TNS/violating paths/worst slack/area found, tool version."""
        return out(summarize_tool_log(confine(path, settings)), response_format, "Digital log")

    @tool("Parse a timing or area report", read_only=True, idempotent=True)
    def cadence_digital_parse_report(path: PathArg, response_format: Fmt = "markdown") -> str:
        """Extract WNS/TNS/violating paths/worst slack/area from a report file, plus its first 40 lines."""
        return out(parse_report(confine(path, settings)), response_format, "Report")

    @tool("Run Xcelium", read_only=False)
    def cadence_xcelium_run(sources: Annotated[list[str], Field(min_length=1, description="HDL source files and -f file lists' targets (inside the allowed roots).")],
                            top: Annotated[str | None, Field(description="Top-level unit (-top).")] = None,
                            options: Annotated[list[str] | None, Field(description="Extra xrun options, e.g. ['-sv', '-access', '+rwc', '+UVM_TESTNAME=smoke'].")] = None,
                            uvm: Annotated[bool, Field(description="Add -uvm.")] = False,
                            cwd: Annotated[str | None, Field(description="Working directory (inside the allowed roots); default: the run directory.")] = None,
                            timeout_s: TimeoutArg = None, wait: Wait = True, profile: ProfileArg = None,
                            response_format: Fmt = "markdown") -> str:
        """Single-step xrun (compile + elaborate + simulate); falls back to irun on pre-Xcelium installs.
        Returns: {run_id, state, returncode, summary: {errors, warnings, error_ids, first_errors, uvm_summary, finished}}."""
        return out(dig.xrun(sources, top, options, uvm, cwd, timeout_s, wait, profile), response_format, "xrun")

    @tool("Summarise an xrun log", read_only=True, idempotent=True)
    def cadence_xcelium_summarize_log(path: PathArg, response_format: Fmt = "markdown") -> str:
        """Summarise an xrun/irun log: *E/*W/*F counts by ID, first errors, UVM report counts, tool version."""
        return out(summarize_xrun_log(confine(path, settings)), response_format, "xrun log")

    # ========================================================================= PCB

    @tool("PCB tools", read_only=True, idempotent=True)
    def cadence_pcb_get_info(profile: ProfileArg = None, response_format: Fmt = "markdown") -> str:
        """Allegro PCB Editor and OrCAD Capture executables and SPB release (16.6, 17.x, 22.1-25.1)."""
        return out(pcb.info(profile), response_format, "PCB")

    @tool("Run SKILL in Allegro", read_only=False, destructive=True)
    def cadence_allegro_run_skill(board: Annotated[str, Field(min_length=1, description="Board/package design file (.brd, .mcm, .sip) inside the allowed roots.")],
                                  code: Annotated[str, Field(min_length=1, max_length=200_000, description="Allegro SKILL (axl* API), e.g. 'length(axlDBGetDesign()->nets)'.")],
                                  timeout_s: TimeoutArg = None, wait: Wait = True, profile: ProfileArg = None,
                                  extra_args: Annotated[list[str] | None, Field(description="Extra allegro arguments (product selection...).")] = None,
                                  response_format: Fmt = "markdown") -> str:
        """Run SKILL on a design in Allegro batch mode (allegro -s script.scr -nograph). The design is not
        saved unless the code saves it (needs access 'write'). Returns: {status, value, output, run_id}."""
        return out(pcb.allegro_skill(board, code, timeout_s, wait, profile, extra_args), response_format, "Allegro")

    @tool("Allegro board summary", read_only=True, idempotent=True)
    def cadence_allegro_get_board_summary(board: Annotated[str, Field(min_length=1, description="Board file inside the allowed roots.")],
                                          profile: ProfileArg = None, response_format: Fmt = "markdown") -> str:
        """Board summary in batch: design name, Allegro version, component/net/symbol counts, etch layers, units."""
        return out(pcb.board_summary(board, profile), response_format, "Board")

    @tool("Run Tcl in OrCAD Capture", read_only=False, destructive=True)
    def cadence_capture_run_tcl(script: Annotated[str, Field(min_length=1, max_length=200_000, description="Capture Tcl; it must open the design itself.")],
                                timeout_s: TimeoutArg = None, wait: Wait = True, profile: ProfileArg = None,
                                response_format: Fmt = "markdown") -> str:
        """Run a Tcl script in OrCAD (X) Capture (capture.exe script.tcl); its last value is returned.
        Experimental: Capture is a GUI application. Returns: {status, value, run_id}."""
        return out(pcb.capture_tcl(script, timeout_s, wait, profile), response_format, "Capture")

    # ========================================================================= resources

    @server.resource("cadence://compatibility")
    def compatibility_resource() -> str:
        """Version compatibility matrix (markdown)."""
        return COMPAT_MATRIX_MD

    @server.resource("cadence://skill-helpers")
    def skill_helpers_resource() -> str:
        """Source of the SKILL helper file loaded into Virtuoso/Allegro (cadence_mcp.il)."""
        return helper_path().read_text(encoding="utf-8")

    server._cadence = {"settings": settings, "jobs": jobs, "virtuoso": virt, "spectre": spec,
                       "digital": dig, "pcb": pcb, "sdk": sdk}
    return server


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
