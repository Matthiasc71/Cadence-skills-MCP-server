"""Version parsing and version-dependent behaviour for Cadence tools.

Rule of thumb used everywhere in cadence-mcp: *detect features at runtime when you can*
(isCallable() in SKILL, ``info commands`` / is_common_ui_mode in Tcl) and use the version
tables below only to choose defaults and to explain differences.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ToolVersion:
    tool: str
    raw: str
    family: str | None = None  # IC, ICADVM, ICADV, SPECTRE, MMSIM, ...
    major: int | None = None
    minor: int | None = None
    patch: int | None = None
    isr: str | None = None
    display: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def tuple(self) -> tuple[int, int, int]:
        return (self.major or 0, self.minor or 0, self.patch or 0)

    @property
    def label(self) -> str:
        if self.display:
            return self.display
        if self.major is None:
            return "unknown"
        core = f"{self.major}.{self.minor}" + (f".{self.patch}" if self.patch is not None else "")
        return f"{self.family or ''}{core}" + (f" ISR{self.isr}" if self.isr else "")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["label"] = self.label
        return d


# ----------------------------------------------------------------------------- Virtuoso

_IC = re.compile(r"\b(ICADVM|ICADV|IC)(\d+)\.(\d+)(?:\.(\d+))?(?:-64b)?(?:\.(?:ISR)?(\d+))?", re.I)
_IC_PLAIN = re.compile(r"virtuoso version (\d+)\.(\d+)(?:\.(\d+))?")


def parse_virtuoso(raw: str) -> ToolVersion:
    v = ToolVersion("virtuoso", raw.strip())
    m = _IC.search(raw)
    if m:
        v.family = m.group(1).upper()
        v.major, v.minor = int(m.group(2)), int(m.group(3))
        v.patch = int(m.group(4)) if m.group(4) else None
        isr = re.search(r"ISR\s*(\d+)", raw, re.I)
        v.isr = isr.group(1) if isr else None
    else:
        m = _IC_PLAIN.search(raw)
        if m:
            v.family = "IC"
            v.major, v.minor = int(m.group(1)), int(m.group(2))
            v.patch = int(m.group(3)) if m.group(3) else None
        else:  # install-path style: IC617, IC618, ICADVM201, IC231, IC251
            m = re.search(r"\b(ICADVM|ICADV|IC)[_\-]?(\d)(\d)(\d)?(?!\d)", raw, re.I)
            if m:
                v.family = m.group(1).upper()
                a, b, c = m.group(2), m.group(3), m.group(4)
                if a == "6" and c:
                    v.major, v.minor, v.patch = 6, int(b), int(c)
                elif c:
                    v.major, v.minor = int(a + b), int(c)
    v.notes = virtuoso_notes(v)
    return v


def virtuoso_notes(v: ToolVersion) -> list[str]:
    n: list[str] = []
    if v.major is None:
        return ["Version not recognised: rely on isCallable() feature probes."]
    t = v.tuple
    if v.family == "IC" and t < (6, 1, 7) and v.major == 6:
        n.append("Older than IC6.1.7: skillbridge is not supported, use batch mode (virtuoso -nograph).")
    if v.major == 5:
        n.append("IC5.x (CDB database): outside the supported range; batch SKILL only, no skillbridge, no maestro.")
    if v.family == "IC" and v.major == 6 and t >= (6, 1, 7):
        n.append("ADE Explorer/Assembler (maestro views) available alongside the deprecated ADE L/XL; probe "
                 "isCallable('maeOpenSetup) for the mae* API, which then works the same on IC23.1.")
    if v.family in ("ICADVM", "ICADV"):
        n.append("Advanced-node stream (ICADVM): ADE L is not available; use ADE Explorer/Assembler (maestro). "
                 "IC23.1 is the successor of ICADVM20.1.")
    if v.family == "IC" and v.major >= 23:
        n.append("Virtuoso Studio: the ADE L and ADE XL environments are removed; load ADE L states into ADE "
                 "Explorer and convert adexl views to maestro views (ADE Assembler). The axl* session API still "
                 "exists (axlCreateSession, axlGetWindowSession... now drive maestro views), so isCallable on an "
                 "axl* function cannot tell IC6.1.x from IC23.1: test the release (getVersion) or which view exists.")
    if v.family == "IC" and v.major >= 25:
        n.append("IC25.1: ADE Artist introduced; .simrc netlist customisation is no longer supported; "
                 "the Schematic Parasitics menu moved to ADE Explorer/Assembler (Parasitics/LDE).")
    return n


# ----------------------------------------------------------------------------- Spectre

_SPECTRE = re.compile(r"(\d{2})\.(\d)\.(\d+)")


def parse_spectre(raw: str) -> ToolVersion:
    v = ToolVersion("spectre", raw.strip())
    anchor = re.search(r"(sub-version|version|SPECTRE|MMSIM)", raw, re.I)
    m = _SPECTRE.search(raw, anchor.start() if anchor else 0) or _SPECTRE.search(raw)
    if m:
        v.major, v.minor, v.patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        isr = re.search(r"isr\s*(\d+)", raw, re.I)
        v.isr = isr.group(1) if isr else None
        v.family = "MMSIM" if v.major <= 15 else "SPECTRE"
    else:  # install-path style: SPECTRE231, MMSIM151
        m = re.search(r"\b(SPECTRE|MMSIM)[_\-]?(\d{2})(\d)(?!\d)", raw, re.I)
        if m:
            v.family = m.group(1).upper()
            v.major, v.minor = int(m.group(2)), int(m.group(3))
    v.notes = spectre_notes(v)
    return v


def spectre_notes(v: ToolVersion) -> list[str]:
    if v.major is None:
        return ["Version not recognised."]
    n = []
    if v.tuple < (19, 1, 0):
        n.append("Spectre X (+preset=cx|ax|mx|lx|vx) is not available before SPECTRE 19.1: use +aps.")
    else:
        n.append("Spectre X available: +preset=cx (most accurate) ... vx (fastest); it ignores +aps/++aps.")
    if v.tuple < (16, 1, 0):
        n.append("MMSIM-era release: psfxl output format may be missing; prefer psfbin/psfascii.")
    return n


def spectre_engine_args(engine: str, preset: str | None, v: ToolVersion | None) -> tuple[list[str], list[str]]:
    """Return (args, warnings) for the requested engine, adapted to the Spectre version."""
    warnings: list[str] = []
    engine = engine.lower()
    if engine in ("spectre", "classic", ""):
        return [], warnings
    if engine == "aps":
        return ["+aps"], warnings
    if engine in ("x", "spectrex", "spectre_x"):
        preset = (preset or "cx").lower()
        if preset not in {"cx", "ax", "mx", "lx", "vx"}:
            raise ValueError("Spectre X preset must be one of cx, ax, mx, lx, vx")
        if v is not None and v.major is not None and v.tuple < (19, 1, 0):
            warnings.append(f"Spectre {v.label} predates Spectre X; falling back to +aps.")
            return ["+aps"], warnings
        return [f"+preset={preset}"], warnings
    raise ValueError("engine must be one of: spectre, aps, x")


# ----------------------------------------------------------------------------- Digital

_DIG = re.compile(r"\bv?(\d{2})\.(\d)(\d+)?-([se])(\d+)(?:_(\d+))?")


def parse_digital(tool: str, raw: str) -> ToolVersion:
    """Cadence digital numbering: 21.18-s099_1 = release 21.1, ISR 8, build s099."""
    v = ToolVersion(tool, raw.strip())
    m = _DIG.search(raw)
    if m:
        v.family = tool.upper()
        v.major, v.minor = int(m.group(1)), int(m.group(2))
        v.isr = str(int(m.group(3))) if m.group(3) else None
    v.notes = digital_notes(tool, v)
    return v


def digital_notes(tool: str, v: ToolVersion) -> list[str]:
    n = []
    if tool == "genus":
        n.append("Genus starts in Stylus Common UI by default; 'genus -legacy_ui' (or set_db common_ui false) "
                 "gives the RTL-Compiler-style legacy UI. 'is_common_ui_mode' returns 1/0.")
    elif tool in ("innovus", "tempus", "voltus"):
        n.append(f"{tool.capitalize()} starts in the legacy UI unless launched with -stylus (Common UI). "
                 "Use get_common_ui_map <legacy_cmd> to translate commands, convert_legacy_to_common_ui "
                 "<in.tcl> <out.tcl> for whole scripts and eval_legacy {...} as an escape hatch.")
    if tool == "innovus" and v.major is not None and v.major >= 19:
        n.append("Foundation Flow is in maintenance since 19.14; Stylus flows use 'flowtool' / write_flow_template.")
    return n


_XRUN = re.compile(r"(?:xrun|irun|ncsim|xmsim)\(?\d*\)?:?\s*(\d{2})\.(\d{2})-([sp])(\d+)", re.I)


def parse_xcelium(raw: str) -> ToolVersion:
    v = ToolVersion("xrun", raw.strip())
    m = _XRUN.search(raw) or re.search(r"\b(\d{2})\.(\d{2})-([sp])(\d+)", raw)
    if m:
        v.major, v.minor = int(m.group(1)), int(m.group(2))
        v.family = "INCISIVE" if "irun" in raw.lower() and "xrun" not in raw.lower() else "XCELIUM"
        v.display = f"{v.family} {m.group(1)}.{m.group(2)}-{m.group(3)}{m.group(4)}"
    v.notes = []
    if v.family == "INCISIVE":
        v.notes.append("Incisive/irun: replaced by Xcelium/xrun; nc* binaries became xm* (ncvlog -> xmvlog...).")
    else:
        v.notes.append("Use single-step 'xrun' (compile+elaborate+simulate); multi-step: xrun -compile / -elaborate / -R.")
    return v


# ----------------------------------------------------------------------------- PCB

_SPB = re.compile(r"SPB[_\-\s]?(\d{2})\.?(\d)", re.I)


def parse_spb(raw: str) -> ToolVersion:
    v = ToolVersion("allegro", raw.strip())
    m = _SPB.search(raw) or re.search(r"\b(1[67]|2[2-9])\.(\d)\b", raw)
    if m:
        v.major, v.minor = int(m.group(1)), int(m.group(2))
        v.family = "SPB"
    v.notes = []
    if v.major is not None:
        if v.major >= 22:
            v.notes.append("Allegro X / OrCAD X generation (22.1+): Tcl command window in OrCAD X Capture and "
                           "Presto; SKILL (axl*) unchanged for Allegro PCB Editor scripting.")
        else:
            v.notes.append("Allegro 16.x/17.x: SKILL axl* API; batch via 'allegro -s script.scr -nograph board.brd'.")
    return v


# ----------------------------------------------------------------------------- Matrix

COMPAT_MATRIX_MD = """\
# Matrice de compatibilité Cadence (cadence-mcp)

Principe : **détecter les fonctionnalités à l'exécution** (`isCallable('fn)` en SKILL,
`info commands`, `is_common_ui_mode` en Tcl) et n'utiliser ce tableau que pour choisir
les valeurs par défaut et expliquer les écarts.

## Virtuoso (IC)
| Version | Base | Simulation (ADE) | Points d'attention |
|---|---|---|---|
| IC6.1.7 | OpenAccess, 64 bits | ADE L/XL + ADE Explorer/Assembler (maestro, API mae*) | Version minimale de skillbridge ; AMS via xrun |
| IC6.1.8 / ICADVM18.1 | OpenAccess | Explorer/Assembler recommandés ; ADE L absent des releases ICADVM | Scripts sev*/axl* encore utilisables en IC6.1.8 |
| ICADVM20.1 | OpenAccess | Explorer/Assembler | Remplacé par IC23.1 (unification) |
| IC23.1 (Virtuoso Studio) | OpenAccess | **ADE L et ADE XL supprimés** → maestro uniquement | Migrer les states ADE L / vues adexl vers maestro. Les fonctions de session `axl*` existent toujours (elles pilotent maestro) : `isCallable('axl…)` ne distingue pas les versions |
| IC25.1 | OpenAccess | Explorer/Assembler + ADE Artist | `.simrc` supprimé ; menu Parasitics du schéma déplacé dans ADE |

## Spectre
| Version | Moteurs | Notes |
|---|---|---|
| MMSIM ≤ 15.x | spectre, +aps | Ancienne nomenclature MMSIM |
| SPECTRE 16.1–18.1 | spectre, +aps, ++aps | psfxl disponible à partir de 16.1 ISR14 |
| SPECTRE 19.1+ | + Spectre X `+preset=cx/ax/mx/lx/vx` | Spectre X ignore +aps/++aps |
| SPECTRE 23.1–25.1 | + Spectre FX (`spectrefx`, transitoire, mode SPICE par défaut) | Formats : psfascii, psfbin, psfxl, sst2, fsdb |

## Numérique (Genus / Innovus / Tempus)
| Outil | UI par défaut | Autre UI | Aides de migration |
|---|---|---|---|
| Genus | Stylus Common UI | `-legacy_ui` ou `set_db common_ui false` | `is_common_ui_mode` |
| Innovus | Legacy | `-stylus` | `get_common_ui_map`, `convert_legacy_to_common_ui`, `eval_legacy` |
| Tempus / Voltus | Legacy | `-stylus` | idem |

## Vérification (Xcelium)
`irun`/Incisive → `xrun`/Xcelium ; binaires `nc*` → `xm*` (xmvlog, xmelab, xmsim).

## PCB (Allegro X / OrCAD X)
| Version | Scripting | Batch |
|---|---|---|
| 16.6 / 17.2 / 17.4 | SKILL axl* (Allegro), Tcl (Capture) | `allegro -s script.scr -nograph carte.brd` |
| 22.1 / 23.1 / 24.1 / 25.1 (Allegro X / OrCAD X) | idem + fenêtre de commandes Tcl (OrCAD X Capture, Presto) | idem ; Capture : `capture.exe script.tcl` |
"""
