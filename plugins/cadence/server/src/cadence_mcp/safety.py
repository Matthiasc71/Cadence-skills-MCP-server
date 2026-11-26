"""Guardrails: access levels, path confinement, SKILL / Tcl screening.

This is a guardrail against accidental damage (an LLM saving a cellview it was only meant
to read, deleting a library, killing the user's Virtuoso). It is NOT a sandbox: anyone who
can write SKILL can, with enough effort, do anything Virtuoso can. Keep the MCP server
running under the designer's own account and keep the default access level ("run").
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ACCESS_LEVELS, Settings


class AccessDenied(PermissionError):
    pass


def require(settings: Settings, level: str, action: str) -> None:
    if settings.level < ACCESS_LEVELS[level]:
        raise AccessDenied(
            f"'{action}' needs access level '{level}' but the server runs with '{settings.access}'. "
            f"Restart it with CADENCE_MCP_ACCESS={level} (or \"access\": \"{level}\" in the config) "
            "if the user agrees."
        )


def confine(path: str | Path, settings: Settings, must_exist: bool = True) -> Path:
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    roots = settings.all_roots()
    if not any(p == r or r in p.parents for r in roots):
        raise AccessDenied(
            f"{p} is outside the allowed roots {[str(r) for r in roots]}. "
            "Add its parent to CADENCE_MCP_ALLOWED_ROOTS / allowed_roots."
        )
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p


# --------------------------------------------------------------------------- SKILL

SKILL_SHELL = {
    "system", "sh", "shell", "csh", "ipcBeginProcess", "ipcSkillProcess", "ipcBatchProcess",
    "ipcWriteProcess", "ipcKillProcess", "hiStartProcess", "axlShell", "axlRunBatchDBProgram",
}
# Never allowed through the live bridge: they would end or wedge the user's session.
SKILL_LIVE_FORBIDDEN = {"exit", "hiExit", "hiQuit", "pyKillServer", "pyReloadScript", "axlExit"}
SKILL_DYNAMIC = {
    "evalstring", "eval", "funcall", "apply", "errsetstring", "load", "loadi", "loadstring",
    "loadContext", "putd", "linereadstring", "ilEval", "evalstringc",
}
# Harmless in a throw-away batch session, but they change the user's live session or files.
SKILL_LIVE_WRITE = {"outfile", "createDir", "createDirHier", "envSetVal"}
SKILL_WRITE_EXACT = {
    "deleteFile", "deleteDir", "renameFile", "copyFile", "putprop", "setShellEnvVar", "unsetShellEnvVar", "schCheck", "schCheckHier",
    "maeRunSimulation", "maeSaveSetup", "axlDBCloak", "axlDBDelete", "axlDeleteObject",
    "axlDBCreatePin", "axlDBCreateLine", "axlDBCreatePath", "axlDBCreateShape", "axlDBCreateText",
    "axlTransformObject", "axlChangeNet", "axlDBChangeDesign", "axlRunCommand",
}
SKILL_WRITE_PREFIXES = (
    "dbCreate", "dbDelete", "dbSave", "dbCopy", "dbMove", "dbReplace", "dbPurge", "dbSet",
    "dbFlatten", "dbTransform", "dbReopen", "dbAdd", "dbRemove", "dbMerge", "dbConvert",
    "ddDelete", "ddCreate", "ddCopy", "ddRename", "ddMove", "ddsCopy", "ddsDelete",
    "schCreate", "schDelete", "schSave", "schHi", "schSet", "schPlace", "schSchemToPinList",
    "leCreate", "leDelete", "leHi", "leSet", "leMerge", "leChop", "leConvert",
    "geDelete", "geSave", "ccpCopy", "ccpRename", "ccpDelete", "gdmCreate",
    "axlDBCreate", "axlDBDelete", "axlDel", "axlRename",
)
_ASSIGN_ATTR = re.compile(r"(?:~>|->)\s*[A-Za-z_][\w?]*\s*=(?!=)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_?]*")


def _strip_skill(code: str) -> str:
    """Remove comments and string literals so identifiers inside them are not flagged."""
    out: list[str] = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == ";":
            j = code.find("\n", i)
            i = n if j < 0 else j
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif c == '"':
            i += 1
            while i < n and code[i] != '"':
                i += 2 if code[i] == "\\" else 1
            i += 1
            out.append('""')
        else:
            out.append(c)
            i += 1
    return "".join(out)


def skill_problems(code: str, settings: Settings, live: bool) -> list[str]:
    stripped = _strip_skill(code)
    idents = set(_IDENT.findall(stripped))
    problems: list[str] = []
    if live:
        bad = sorted(idents & SKILL_LIVE_FORBIDDEN)
        if bad:
            problems.append(f"{bad} would end or disconnect the user's live Virtuoso session")
    if not settings.allow_shell:
        bad = sorted(idents & SKILL_SHELL)
        if bad:
            problems.append(f"shell/process functions {bad} need allow_shell=true")
    if settings.level < ACCESS_LEVELS["write"]:
        writes = sorted(i for i in idents if i in SKILL_WRITE_EXACT or i.startswith(SKILL_WRITE_PREFIXES)
                        or (live and i in SKILL_LIVE_WRITE))
        if writes:
            problems.append(f"design/file-modifying functions {writes} need access level 'write'")
        if _ASSIGN_ATTR.search(stripped):
            problems.append("attribute assignment (obj~>attr = value) modifies the database and needs access level 'write'")
        dyn = sorted(idents & SKILL_DYNAMIC)
        if dyn:
            problems.append(f"dynamic evaluation {dyn} cannot be screened and needs access level 'write'")
    return problems


def check_skill(code: str, settings: Settings, live: bool) -> None:
    problems = skill_problems(code, settings, live)
    if problems:
        raise AccessDenied("SKILL refused by the cadence-mcp guardrail: " + "; ".join(problems))


# --------------------------------------------------------------------------- Tcl

_TCL_SHELL = [
    (re.compile(r"(^|[\s\[;{])exec\s"), "exec"),
    (re.compile(r"\bopen\s+[\"{]?\|"), "open |pipe"),
    (re.compile(r"(^|[\s\[;{])(sh|unix|shell)\s"), "sh/unix/shell"),
]
_TCL_FILE_WRITE = [(re.compile(r"\bfile\s+(delete|rename|link)\b"), "file delete/rename/link")]


def tcl_problems(code: str, settings: Settings) -> list[str]:
    lines = [ln for ln in code.splitlines() if not ln.lstrip().startswith("#")]
    text = "\n".join(lines)
    problems: list[str] = []
    if not settings.allow_shell:
        found = [name for rx, name in _TCL_SHELL if rx.search(text)]
        if found:
            problems.append(f"shell escapes {found} need allow_shell=true")
    if settings.level < ACCESS_LEVELS["write"]:
        found = [name for rx, name in _TCL_FILE_WRITE if rx.search(text)]
        if found:
            problems.append(f"{found} need access level 'write'")
    return problems


def check_tcl(code: str, settings: Settings) -> None:
    problems = tcl_problems(code, settings)
    if problems:
        raise AccessDenied("Tcl refused by the cadence-mcp guardrail: " + "; ".join(problems))
