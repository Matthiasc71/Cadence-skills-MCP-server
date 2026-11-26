"""Fake Cadence executables used by the tests (they mimic CLI contracts, not the tools)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

DATA = Path(__file__).parent / "data"

COMMON = r'''
import json, re, sys, os, shutil
from pathlib import Path
args = sys.argv[1:]
DATA = Path(%(data)r)
def after(flag):
    return args[args.index(flag) + 1] if flag in args else None
'''

FAKES = {
    "spectre": COMMON + r'''
if args == ["-W"]:
    print(os.environ.get("FAKE_SPECTRE_W", "sub-version 23.1.0.363.isr8")); sys.exit(0)
if args == ["-V"]:
    print("@(#)$CDS: spectre version " + os.environ.get("FAKE_SPECTRE_V", "23.1.0") + " 64bit"); sys.exit(0)
log = Path(after("=log")); raw = Path(after("-raw")); raw.mkdir(parents=True, exist_ok=True)
(raw / "args.json").write_text(json.dumps(args))
shutil.copy(DATA / "tran.tran", raw / "tran.tran")
log.write_text("Total time required for tran analysis `tran': CPU = 1 ms, elapsed = 2 ms.\n"
               "spectre completes with 0 errors, 0 warnings, and 1 notice.\n")
print("netlist", args[0], "cwd", os.getcwd())
''',
    "virtuoso": COMMON + r'''
DEFAULT_DB = {"subVersion": "sub-version  IC23.1-64b.ISR9.24 ", "version": "@(#)$CDS: virtuoso version 23.1-64b",
              "dbType": "OpenAccess", "features": {"maeOpenSetup": True, "axlGetWindowSession": True, "sevSession": False},
              "libraries": [{"name": "analogLib", "path": "/eda/analogLib", "cells": {}}]}
db = DEFAULT_DB
if os.environ.get("FAKE_VIRTUOSO_DB"):
    db = json.loads(Path(os.environ["FAKE_VIRTUOSO_DB"]).read_text())
if args == ["-W"]:
    print(db["subVersion"]); sys.exit(0)
script = Path(after("-replay") or after("-restore"))
text = script.read_text()
logf = after("-log")
if logf: Path(logf).write_text("fake CDS.log\n")
m = re.search(r'CdsMcpWriteResult\("([^"]+)" CdsMcpEvalFile\("([^"]+)"\)\)', text)
if not m:  # OCEAN-like script
    proj = re.search(r'projectDir" \'string "([^"]+)"', text)
    design = re.search(r'design\("([^"]+)" "([^"]+)" "([^"]+)"\)', text)
    if proj and design:
        d = Path(proj.group(1)) / design.group(2) / "spectre" / design.group(3) / "netlist"
        d.mkdir(parents=True, exist_ok=True)
        (d / "input.scs").write_text("// fake netlist\nsimulator lang=spectre\n")
    print("ocean script ran:", script)
    sys.exit(0)

def lib(name):
    for l in db["libraries"]:
        if l["name"] == name:
            return l
    return None

result, user = m.group(1), Path(m.group(2)).read_text()
status, kind, val = "ok", "string", None
if "CdsMcpVersionInfo" in user:
    val = json.dumps({"version": db["version"], "subVersion": db["subVersion"], "dbType": db["dbType"],
                      "graphic": False, "features": [{"name": k, "callable": v} for k, v in db["features"].items()]})
elif "CdsMcpListLibs" in user:
    val = json.dumps([{"name": l["name"], "path": l["path"]} for l in db["libraries"]])
elif "CdsMcpListCells" in user:
    a = re.search(r'CdsMcpListCells\("([^"]*)" (?:nil|"([^"]*)") (\d+) (\d+)\)', user)
    l = lib(a.group(1))
    if l is None:
        status, kind, val = "error", "error", '("CdsMcp: library %s is not defined in cds.lib" "%s")' % (a.group(1), a.group(1))
    else:
        names = sorted(n for n in l["cells"] if not a.group(2) or re.search(a.group(2), n))
        off, lim = int(a.group(3)), int(a.group(4))
        val = json.dumps({"library": l["name"], "path": l["path"], "total": len(names),
                          "cells": [{"name": n, "views": sorted(l["cells"][n]["views"])} for n in names[off:off + lim]]})
elif "CdsMcpCellViewInfo" in user:
    a = re.search(r'CdsMcpCellViewInfo\("([^"]*)" "([^"]*)" "([^"]*)" (\d+) (\d+)\)', user)
    l = lib(a.group(1))
    cv = l and l["cells"].get(a.group(2), {}).get("views", {}).get(a.group(3))
    if not cv:
        status, kind, val = "error", "error", '("CdsMcp: cannot open %s/%s/%s")' % a.groups()[:3]
    else:
        off, lim = int(a.group(4)), int(a.group(5))
        out = {"lib": a.group(1), "cell": a.group(2), "view": a.group(3)}
        out.update({k: v for k, v in cv.items() if k not in ("terminals", "instances", "nets", "layers")})
        for key in ("terminals", "instances", "nets", "layers"):
            out[key] = cv.get(key, [])[off:off + lim]
        out["instanceCount"] = len(cv.get("instances", []))
        out["netCount"] = len(cv.get("nets", []))
        out["terminalCount"] = len(cv.get("terminals", []))
        val = json.dumps(out)
elif "boom" in user:
    val, status, kind = '("*Error* eval: undefined function" boom)', "error", "error"
else:
    val, kind = "3", "fixnum"
Path(result).write_text(f"CDSMCP_STATUS {status}\nCDSMCP_KIND {kind}\nCDSMCP_OUTPUT_BEGIN\nhello from batch\n"
                        f"CDSMCP_OUTPUT_END\nCDSMCP_VALUE_BEGIN\n{val}\nCDSMCP_VALUE_END\n")
''',
    "genus": COMMON + r'''
if args == ["-version"]:
    print("Genus(TM) Synthesis Solution, Version 21.14-s082_1"); sys.exit(0)
prefix = after("-log")
Path(prefix + ".log").write_text("ARGS " + " ".join(args) + "\n" + Path(after("-files")).read_text()
                                 + "\nError   : (TUI-61) A required object parameter could not be found.\n")
''',
    "innovus": COMMON + r'''
if args == ["-version"]:
    print("@(#)CDS: Innovus v21.18-s099_1 (64bit)"); sys.exit(0)
prefix = after("-log")
Path(prefix + ".log").write_text("ARGS " + " ".join(args) + "\n" + Path(after("-files")).read_text() + "\n"
                                 + (DATA / "innovus.log").read_text())
''',
    "xrun": COMMON + r'''
if args == ["-version"]:
    print("TOOL:\txrun(64)\t23.09-s001"); sys.exit(0)
Path(after("-l")).write_text((DATA / "xrun.log").read_text())
sys.exit(1)
''',
    "allegro": COMMON + r'''
scr = Path(after("-s")).read_text()
m = re.search(r'CdsMcpWriteResult\("([^"]+)" CdsMcpEvalFile\("([^"]+)"\)\)', scr)
user = Path(m.group(2)).read_text()
val = json.dumps({"design": "demo", "components": 12, "nets": 34}) if "axlDBGetDesign" in user else "t"
Path(m.group(1)).write_text("CDSMCP_STATUS ok\nCDSMCP_KIND string\nCDSMCP_OUTPUT_BEGIN\n\nCDSMCP_OUTPUT_END\n"
                            f"CDSMCP_VALUE_BEGIN\n{val}\nCDSMCP_VALUE_END\n")
''',
}


def install(bindir: Path) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    for name, body in FAKES.items():
        p = bindir / name
        p.write_text(f"#!{sys.executable}\n" + body.replace("%(data)r", repr(str(DATA))))
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    # ocean behaves like virtuoso for our purposes
    (bindir / "ocean").write_text((bindir / "virtuoso").read_text())
    (bindir / "ocean").chmod(0o755)
    return bindir


def fake_env(bindir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    return env
