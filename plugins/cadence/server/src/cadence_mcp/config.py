"""Configuration: JSON file (CADENCE_MCP_CONFIG) + environment variable overrides.

Access levels
-------------
read  : inspection only (versions, libraries, cellviews, logs, results). No tool runs.
run   : read + batch runs (Spectre, OCEAN, Genus/Innovus/Tempus, Xcelium, Allegro scripts)
        whose outputs go to a dedicated run directory. SKILL code that edits the design
        database is refused. (default)
write : run + design edits (dbSave, dbCreate*, schCheck with save, ...).

Shell escapes (system(), sh(), ipcBeginProcess, Tcl exec, ...) are refused at every level
unless allow_shell is true.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACCESS_LEVELS = {"read": 0, "run": 1, "write": 2}


@dataclass
class Profile:
    """A named Cadence environment (one installation / one version)."""

    name: str
    setup: str | None = None  # shell script to source (.sh/.bash/.csh/.tcsh/.bat)
    env: dict[str, str] = field(default_factory=dict)  # extra variables, ${VAR} expanded
    description: str = ""


@dataclass
class Settings:
    access: str = "run"
    allow_shell: bool = False
    workdir: Path = field(default_factory=lambda: Path.home() / "cadence_mcp_runs")
    allowed_roots: list[Path] = field(default_factory=list)
    default_profile: str | None = None
    profiles: dict[str, Profile] = field(default_factory=dict)
    binaries: dict[str, str] = field(default_factory=dict)  # tool -> absolute path override
    # Virtuoso
    skillbridge_id: str | None = None
    skillbridge_force_tcp: bool = False
    virtuoso_batch_flag: str = "-replay"  # or "-restore"
    virtuoso_nocdsinit: bool = False
    cds_log: str | None = None
    project_dir: Path | None = None  # where batch Virtuoso/OCEAN start (cds.lib, .cdsinit)
    # Limits
    timeout_default_s: int = 3600
    timeout_version_s: int = 60
    max_output_chars: int = 60000

    @property
    def level(self) -> int:
        return ACCESS_LEVELS[self.access]

    def all_roots(self) -> list[Path]:
        roots = [self.workdir.resolve()] + [r.resolve() for r in self.allowed_roots]
        seen: list[Path] = []
        for r in roots:
            if r not in seen:
                seen.append(r)
        return seen


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on", "oui"}


def _paths(v: Any) -> list[Path]:
    if not v:
        return []
    if isinstance(v, str):
        v = [p for p in v.split(os.pathsep) if p]
    return [Path(os.path.expandvars(str(p))).expanduser() for p in v]


def load_settings(config_path: str | None = None, environ: dict[str, str] | None = None) -> Settings:
    env = {k: v for k, v in (os.environ if environ is None else environ).items() if v != ""}
    data: dict[str, Any] = {}
    path = config_path or env.get("CADENCE_MCP_CONFIG")
    if path:
        p = Path(path).expanduser()
        with p.open(encoding="utf-8") as fh:
            data = json.load(fh)

    s = Settings()
    s.access = str(env.get("CADENCE_MCP_ACCESS", data.get("access", s.access))).lower()
    if s.access not in ACCESS_LEVELS:
        raise ValueError(f"access must be one of {sorted(ACCESS_LEVELS)}, got {s.access!r}")
    s.allow_shell = _bool(env.get("CADENCE_MCP_ALLOW_SHELL", data.get("allow_shell", False)))
    wd = env.get("CADENCE_MCP_WORKDIR", data.get("workdir"))
    if wd:
        s.workdir = Path(os.path.expandvars(wd)).expanduser()
    roots = env.get("CADENCE_MCP_ALLOWED_ROOTS", data.get("allowed_roots"))
    s.allowed_roots = _paths(roots) or [Path.cwd()]
    s.default_profile = env.get("CADENCE_MCP_PROFILE", data.get("default_profile"))

    for name, prof in (data.get("profiles") or {}).items():
        s.profiles[name] = Profile(
            name=name,
            setup=prof.get("setup"),
            env={k: str(v) for k, v in (prof.get("env") or {}).items()},
            description=prof.get("description", ""),
        )
    setup_env = env.get("CADENCE_MCP_SETUP")  # quick single-profile setup
    if setup_env and "default" not in s.profiles:
        s.profiles["default"] = Profile(name="default", setup=setup_env, description="CADENCE_MCP_SETUP")
        s.default_profile = s.default_profile or "default"
    if s.default_profile and s.default_profile not in s.profiles:
        raise ValueError(f"default_profile {s.default_profile!r} is not defined in profiles")

    s.binaries = {k: str(v) for k, v in (data.get("binaries") or {}).items()}
    for key, value in env.items():
        if key.startswith("CADENCE_MCP_BIN_") and value:
            s.binaries[key[len("CADENCE_MCP_BIN_"):].lower()] = value

    v = data.get("virtuoso") or {}
    s.skillbridge_id = env.get("CADENCE_MCP_SKILLBRIDGE_ID", v.get("skillbridge_id"))
    s.skillbridge_force_tcp = _bool(env.get("CADENCE_MCP_SKILLBRIDGE_TCP", v.get("force_tcp", False)))
    s.virtuoso_batch_flag = v.get("batch_flag", s.virtuoso_batch_flag)
    if s.virtuoso_batch_flag not in ("-replay", "-restore"):
        raise ValueError("virtuoso.batch_flag must be -replay or -restore")
    s.virtuoso_nocdsinit = _bool(env.get("CADENCE_MCP_NOCDSINIT", v.get("nocdsinit", False)))
    s.cds_log = env.get("CADENCE_MCP_CDS_LOG", v.get("cds_log"))
    proj = env.get("CADENCE_MCP_PROJECT_DIR", v.get("project_dir"))
    s.project_dir = Path(os.path.expandvars(proj)).expanduser() if proj else None

    t = data.get("timeouts") or {}
    s.timeout_default_s = int(env.get("CADENCE_MCP_TIMEOUT", t.get("default_s", s.timeout_default_s)))
    s.timeout_version_s = int(t.get("version_s", s.timeout_version_s))
    s.max_output_chars = int(env.get("CADENCE_MCP_MAX_OUTPUT", data.get("max_output_chars", s.max_output_chars)))

    s.workdir.mkdir(parents=True, exist_ok=True)
    return s
