"""Discovery of installed Cadence tools and their versions, per profile."""

from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from .config import Settings
from .jobs import run_quick
from .profiles import ProfileManager
from .versions import ToolVersion, parse_digital, parse_spb, parse_spectre, parse_virtuoso, parse_xcelium

# tool -> (binary candidates, version arguments or None, parser)
TOOLS: dict[str, tuple[tuple[str, ...], list[str] | None, Callable[[str], ToolVersion]]] = {
    "virtuoso": (("virtuoso",), ["-W"], parse_virtuoso),
    "ocean": (("ocean",), None, parse_virtuoso),
    "spectre": (("spectre",), ["-W"], parse_spectre),
    "spectrefx": (("spectrefx",), ["-W"], parse_spectre),
    "genus": (("genus",), ["-version"], lambda s: parse_digital("genus", s)),
    "innovus": (("innovus",), ["-version"], lambda s: parse_digital("innovus", s)),
    "tempus": (("tempus",), ["-version"], lambda s: parse_digital("tempus", s)),
    "voltus": (("voltus",), ["-version"], lambda s: parse_digital("voltus", s)),
    "joules": (("joules",), ["-version"], lambda s: parse_digital("joules", s)),
    "xrun": (("xrun", "irun"), ["-version"], parse_xcelium),
    "allegro": (("allegro", "allegro.exe"), None, parse_spb),
    "capture": (("capture", "capture.exe"), None, parse_spb),
}


def discover(settings: Settings, profiles: ProfileManager, profile: str | None = None,
             check_versions: bool = True, only: list[str] | None = None) -> dict[str, Any]:
    env = profiles.env(profile)
    found: dict[str, Any] = {}
    jobs: dict[str, tuple[str, list[str], Callable[[str], ToolVersion]]] = {}
    for tool, (names, args, parser) in TOOLS.items():
        if only and tool not in only:
            continue
        exe = None
        for n in names:
            exe = profiles.which(n, profile)
            if exe:
                break
        if not exe:
            continue
        found[tool] = {"path": exe}
        if args and check_versions:
            jobs[tool] = (exe, args, parser)
        else:
            v = parser(exe)  # version guessed from the install path (SPB_23.1, IC231...)
            found[tool]["version"] = v.label
            found[tool]["notes"] = v.notes

    def query(item: tuple[str, tuple[str, list[str], Callable[[str], ToolVersion]]]) -> tuple[str, ToolVersion, int]:
        tool, (exe, args, parser) = item
        rc, out = run_quick([exe, *args], env, settings.timeout_version_s)
        v = parser(out + "\n" + exe)
        return tool, v, rc

    if jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            for tool, v, rc in pool.map(query, jobs.items()):
                found[tool]["version"] = v.label
                found[tool]["notes"] = v.notes
                if v.major is None:
                    found[tool]["raw"] = v.raw[-400:]
                    found[tool]["returncode"] = rc

    return {
        "profile": profiles.resolve_name(profile),
        "tools": found,
        "missing": [t for t in TOOLS if t not in found and (not only or t in only)],
        "env": {k: env.get(k) for k in ("CDSHOME", "CDS_INST_DIR", "SPECTRE_HOME", "MMSIMHOME", "XCELIUMHOME",
                                         "CDSROOT", "CDS_LIC_FILE", "LM_LICENSE_FILE") if env.get(k)},
    }
