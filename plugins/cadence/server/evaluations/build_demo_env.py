#!/usr/bin/env python3
"""Build the frozen demo environment used by evaluations/cadence_eval.xml.

    python evaluations/build_demo_env.py /tmp/cadence-demo

It creates, without any Cadence license:
- three profiles (ic617, ic231, ic251) whose setup scripts point at fake Cadence executables
  (tests/fakes.py) reporting different Virtuoso/Spectre releases;
- a Virtuoso library database (amp_lib, digital_io, analogLib) served by the fake Virtuoso;
- archived runs (Spectre AC/tran/failed, Genus, three Innovus stages, two xrun regressions)
  with logs, PSF ASCII results and job.json metadata;
- config.json for the server (CADENCE_MCP_CONFIG).
Every answer in the evaluation file is fixed by this script, so it never changes.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))
from fakes import install  # noqa: E402

PROFILES = {
    "ic617": {"virtuoso": "sub-version  IC6.1.7-64b.500.23 ", "spectre_w": "sub-version 18.1.0.421.isr12",
              "spectre_v": "18.1.0", "features": {"maeOpenSetup": True, "axlGetWindowSession": True,
                                                  "sevSession": True, "schCheck": True}},
    "ic231": {"virtuoso": "sub-version  IC23.1-64b.ISR9.24 ", "spectre_w": "sub-version 23.1.0.363.isr8",
              "spectre_v": "23.1.0", "features": {"maeOpenSetup": True, "axlGetWindowSession": True,
                                                  "sevSession": False, "schCheck": True}},
    "ic251": {"virtuoso": "sub-version  IC25.1-64b.ISR3.10 ", "spectre_w": "sub-version 25.1.0.142.isr3",
              "spectre_v": "25.1.0", "features": {"maeOpenSetup": True, "axlGetWindowSession": True,
                                                  "sevSession": False, "schCheck": True}},
}


def inst(name, cell, **params):
    return {"name": name, "lib": "analogLib", "cell": cell, "view": "symbol",
            "params": [[k, v] for k, v in params.items()]}


def library_db() -> list[dict]:
    ota_sch = {
        "cellViewType": "schematic", "mode": "r", "bBox": [[-2.5, -1.0], [6.0, 4.5]],
        "modifiedButNotSaved": False, "shapeCount": 212,
        "terminals": [{"name": n, "direction": d} for n, d in
                      [("VDD", "inputOutput"), ("VSS", "inputOutput"), ("INP", "input"), ("INN", "input"),
                       ("IBIAS", "input"), ("OUT", "output")]],
        "instances": [
            inst("M1", "nmos4", w="2u", l="500n", m="1"), inst("M2", "nmos4", w="2u", l="500n", m="1"),
            inst("M3", "pmos4", w="4u", l="1u", m="1"), inst("M4", "pmos4", w="4u", l="1u", m="1"),
            inst("M5", "nmos4", w="4u", l="1u", m="1"), inst("M6", "pmos4", w="16u", l="500n", m="1"),
            inst("M7", "nmos4", w="8u", l="1u", m="1"), inst("C0", "cap", c="1p"), inst("R0", "res", r="5k"),
        ],
        "nets": ["VDD", "VSS", "INP", "INN", "IBIAS", "OUT", "net1", "net2", "tail", "nz"],
    }
    ota_lay = {"cellViewType": "maskLayout", "mode": "r", "bBox": [[0.0, 0.0], [42.5, 31.2]],
               "modifiedButNotSaved": False, "shapeCount": 1873, "terminals": ota_sch["terminals"],
               "instances": [], "nets": ota_sch["nets"],
               "layers": ["Nwell", "Oxide", "Poly", "Nimp", "Pimp", "Cont", "Metal1", "Via1", "Metal2", "Via2", "Metal3"]}
    bg_sch = {"cellViewType": "schematic", "mode": "r", "bBox": [[0, 0], [8, 6]], "modifiedButNotSaved": False,
              "shapeCount": 140,
              "terminals": [{"name": "VDD", "direction": "inputOutput"}, {"name": "VSS", "direction": "inputOutput"},
                            {"name": "VREF", "direction": "output"}, {"name": "EN", "direction": "input"}],
              "instances": [inst("Q1", "pnp", area="1"), inst("Q2", "pnp", area="8"), inst("R1", "res", r="10k"),
                            inst("R2", "res", r="80k"), inst("M1", "pmos4", w="10u", l="2u", m="2")],
              "nets": ["VDD", "VSS", "VREF", "EN", "va", "vb"]}

    def cell(*views, details=None):
        return {"views": {v: (details or {}).get(v, {"cellViewType": v}) for v in views}}

    amp = {"name": "amp_lib", "path": "/proj/asic01/libs/amp_lib", "cells": {
        "ota_2stage": cell("schematic", "symbol", "layout", "maestro",
                           details={"schematic": ota_sch, "layout": ota_lay}),
        "bandgap_ref": cell("schematic", "symbol", details={"schematic": bg_sch}),
        "ldo_1v8": cell("schematic", "symbol", "layout"),
        "tb_ota": cell("schematic", "maestro", "config"),
        "bias_gen": cell("schematic", "symbol", "layout"),
    }}
    dio = {"name": "digital_io", "path": "/proj/asic01/libs/digital_io", "cells": {
        "pad_in": cell("layout", "abstract", "symbol"), "pad_out": cell("layout", "abstract", "symbol"),
        "pad_vdd": cell("layout", "abstract")}}
    base = ["nmos4", "pmos4", "nmos3", "pmos3", "res", "cap", "ind", "diode", "npn", "pnp", "vdc", "idc",
            "vsin", "isin", "vpulse", "ipulse", "vpwl", "ipwl", "vcvs", "vccs", "ccvs", "cccs", "switch",
            "iprobe", "gnd", "vdd", "cdsParam", "port", "mind", "xfmr", "tline", "zero"]
    va = ["comparator_va", "opamp_va", "adc_va", "dac_va", "zcd_va"]  # zcd_va sorts onto page 2
    extra = [f"aux{i:02d}" for i in range(64 - len(base) - len(va))]
    cells = {}
    for n in base + extra:
        cells[n] = cell("symbol", "spectre", "auCdl")
    for n in va:
        cells[n] = cell("symbol", "veriloga")
    alib = {"name": "analogLib", "path": "/eda/cadence/IC/tools/dfII/etc/cdslib/artist/analogLib", "cells": cells}
    return [alib, amp, dio]


# ------------------------------------------------------------------------- PSF writers

def psf_header(analysis: str, name: str, version: str) -> list[str]:
    return ["HEADER", '"PSFversion" "1.00"', '"simulator" "spectre"', f'"version" "{version}"',
            f'"analysis type" "{analysis}"', f'"analysis name" "{name}"']


def write_ac(path: Path) -> None:
    a0, p1, p2 = 2000.0, 50e3, 200e6
    lines = psf_header("ac", "ac", "23.1.0.363.isr8") + [
        "TYPE", '"freq" FLOAT DOUBLE PROP(', '"key" "sweep"', ")", '"V" COMPLEX DOUBLE PROP(', '"key" "node"', ")",
        "SWEEP", '"freq" "freq" PROP(', '"key" "sweep"', ")", "TRACE", '"OUT" "V"', "VALUE"]
    for k in range(0, 9 * 100 + 1):  # 1 Hz .. 1 GHz, 100 points/decade
        f = 10 ** (k / 100)
        h = a0 / ((1 + 1j * f / p1) * (1 + 1j * f / p2))
        lines += [f'"freq" {f:.12e}', f'"OUT" ({h.real:.12e} {h.imag:.12e})']
    path.write_text("\n".join(lines + ["END", ""]))


def write_tran(path: Path) -> None:
    lines = psf_header("tran", "tran", "23.1.0.363.isr8") + [
        "TYPE", '"time" FLOAT DOUBLE PROP(', '"key" "sweep"', ")", '"V" FLOAT DOUBLE PROP(', '"key" "node"', ")",
        "SWEEP", '"time" "time" PROP(', '"key" "sweep"', ")", "TRACE", '"group" GROUP 2', '"vout" "V"',
        '"vin" "V"', "VALUE"]
    zeta, wn = 0.35, 2 * math.pi * 20e6
    wd = wn * math.sqrt(1 - zeta ** 2)
    for k in range(0, 2001):
        t = k * 0.1e-9  # 0 .. 200 ns
        step = 1.0 if t >= 10e-9 else 0.0
        tt = t - 10e-9
        v = 0.0 if tt < 0 else 1.5 * (1 - math.exp(-zeta * wn * tt) * (math.cos(wd * tt) + zeta / math.sqrt(1 - zeta ** 2) * math.sin(wd * tt)))
        lines += [f'"time" {t:.12e}', f'"group" {0.3 + v:.12e}', f"{0.3 + 1.5 * step:.12e}"]
    path.write_text("\n".join(lines + ["END", ""]))


# ------------------------------------------------------------------------- runs

def job(run: Path, tool: str, state: str, rc: int, started: str, ended: str, elapsed: float, command: list[str],
        log: str, **meta) -> None:
    ended = (datetime.fromisoformat(started) + timedelta(seconds=elapsed)).isoformat(timespec="seconds")
    d = {"run_id": run.name, "tool": tool, "state": state, "returncode": rc, "command": command,
         "cwd": str(run), "run_dir": str(run), "log": str(run / log), "started": started, "ended": ended,
         "elapsed_s": elapsed, **meta}
    (run / "job.json").write_text(json.dumps(d, indent=2))


def innovus_log(stage: str, wns: float, tns: float, viol: int) -> str:
    return (f"@(#)CDS: Innovus v21.18-s099_1 (64bit) 07/18/2023 13:03 (Linux 3.10.0)\n"
            f"**WARN: (IMPLF-200):\tPin 'A' in macro 'INVX1' has no ANTENNAGATEAREA defined.\n"
            f"<CMD> timeDesign -{stage}\n"
            "------------------------------------------------------------\n"
            f"     timeDesign Summary ({stage})\n"
            "------------------------------------------------------------\n"
            "+--------------------+---------+---------+\n"
            "|     Setup mode     |   all   | reg2reg |\n"
            "+--------------------+---------+---------+\n"
            f"|           WNS (ns):| {wns:.3f}  | {wns:.3f}  |\n"
            f"|           TNS (ns):| {tns:.3f}  | {tns:.3f}  |\n"
            f"|    Violating Paths:|   {viol}    |   {viol}    |\n"
            "+--------------------+---------+---------+\n")


def build(dest: Path) -> dict:
    dest = dest.resolve()
    bindir = install(dest / "bin")
    libs = library_db()
    cfg_profiles = {}
    for name, p in PROFILES.items():
        db = {"subVersion": p["virtuoso"], "version": "@(#)$CDS: virtuoso version " + p["virtuoso"].split()[-1],
              "dbType": "OpenAccess", "features": p["features"], "libraries": libs}
        dbf = dest / f"virtuoso_db_{name}.json"
        dbf.write_text(json.dumps(db, indent=1))
        setup = dest / f"setup_{name}.sh"
        setup.write_text(f'export PATH="{bindir}:$PATH"\nexport FAKE_VIRTUOSO_DB="{dbf}"\n'
                         f'export FAKE_SPECTRE_W="{p["spectre_w"]}"\nexport FAKE_SPECTRE_V="{p["spectre_v"]}"\n'
                         f'export CDSHOME="/eda/cadence/{name.upper()}"\n')
        cfg_profiles[name] = {"setup": str(setup), "description": f"Demo installation {name}"}

    work = dest / "work"
    runs = work / "runs"
    runs.mkdir(parents=True, exist_ok=True)

    r = runs / "20261103_091512_spectre_1a2b3c"
    (r / "psf").mkdir(parents=True, exist_ok=True)
    write_ac(r / "psf" / "ac.ac")
    (r / "spectre.out").write_text(
        "Cadence (R) Virtuoso (R) Spectre (R) Circuit Simulation\nVersion 23.1.0.363.isr8 64bit -- 5 Mar 2024\n"
        "Circuit: amp_lib ota_2stage schematic (tb_ota)\n"
        "Total time required for ac analysis `ac': CPU = 412.1 ms, elapsed = 430.6 ms.\n"
        "spectre completes with 0 errors, 0 warnings, and 2 notices.\n")
    job(r, "spectre", "done", 0, "2026-11-03T09:15:12", "2026-11-03T09:15:14", 2.1,
        ["spectre", "input.scs", "+aps", "-format", "psfascii"], "spectre.out", raw_dir=str(r / "psf"))

    r = runs / "20261103_101044_spectre_4d5e6f"
    (r / "psf").mkdir(parents=True, exist_ok=True)
    (r / "spectre.out").write_text(
        "Cadence (R) Virtuoso (R) Spectre (R) Circuit Simulation\nVersion 23.1.0.363.isr8 64bit -- 5 Mar 2024\n"
        "ERROR (SFE-23): \"input.scs\" 41: The instance `Rload' is referencing an undefined model or subcircuit, `resx'.\n"
        "    Either include the file containing the definition of `resx', or define `resx' before running the simulation.\n"
        "spectre completes with 1 error, 0 warnings, and 0 notices.\n")
    job(r, "spectre", "failed", 1, "2026-11-03T10:10:44", "2026-11-03T10:10:45", 0.8,
        ["spectre", "input.scs", "+aps"], "spectre.out")

    r = runs / "20261104_143000_spectre_7a8b9c"
    (r / "psf").mkdir(parents=True, exist_ok=True)
    write_tran(r / "psf" / "tran.tran")
    (r / "spectre.out").write_text(
        "Version 23.1.0.363.isr8 64bit -- 5 Mar 2024\n"
        "Total time required for tran analysis `tran': CPU = 2.315 s, elapsed = 2.401 s.\n"
        "spectre completes with 0 errors, 1 warning, and 0 notices.\n")
    job(r, "spectre", "done", 0, "2026-11-04T14:30:00", "2026-11-04T14:30:03", 2.6,
        ["spectre", "input.scs", "+preset=cx"], "spectre.out", raw_dir=str(r / "psf"))

    r = runs / "20261105_120000_genus_d1e2f3"
    r.mkdir(parents=True, exist_ok=True)
    warn = (["Warning : Libraries have inconsistent nominal operating conditions. [LBR-38]"] * 2
            + ["Warning : Unusable library cells found. [LBR-415]"] * 1
            + ["Warning : Found a hierarchical port with no driver. [PHYS-12]"] * 5
            + ["Warning : Could not find an attribute in the library. [LBR-436]"] * 3)
    (r / "genus.log").write_text("Genus(TM) Synthesis Solution, Version 21.14-s082_1\n" + "\n".join(warn)
                                 + "\nTotal area : 18234.552\n")
    job(r, "genus", "done", 0, "2026-11-05T12:00:00", "2026-11-05T12:41:10", 2470.0,
        ["genus", "-no_gui", "-files", "run.tcl"], "genus.log", ui="stylus")

    for rid, stage, wns, tns, viol, start in [
        ("20261105_180000_innovus_a1a1a1", "preCTS", -0.231, -12.4, 87, "2026-11-05T18:00:00"),
        ("20261106_080000_innovus_b2b2b2", "postCTS", -0.045, -0.9, 12, "2026-11-06T08:00:00"),
        ("20261107_080000_innovus_c3c3c3", "postRoute", 0.012, 0.0, 0, "2026-11-07T08:00:00"),
    ]:
        r = runs / rid
        r.mkdir(parents=True, exist_ok=True)
        (r / "innovus.log").write_text(innovus_log(stage, wns, tns, viol))
        job(r, "innovus", "done", 0, start, start[:11] + "11:30:00", 12600.0,
            ["innovus", "-no_gui", "-files", "run.tcl"], "innovus.log", ui="legacy")

    def xlog(test: str, errors: int) -> str:
        lines = ["TOOL:\txrun(64)\t23.09-s001: Started on Nov 08, 2026 at 09:00:00 CET",
                 f"UVM_INFO @ 0: reporter [RNTST] Running test {test}..."]
        lines += [f"UVM_ERROR /proj/tb/scoreboard.sv(88) @ {1200 + 40 * i}ns: uvm_test_top.env.sb [SBMISMATCH] data mismatch"
                  for i in range(errors)]
        lines += ["--- UVM Report Summary ---", "UVM_INFO :   42", "UVM_WARNING :    1",
                  f"UVM_ERROR :    {errors}", "UVM_FATAL :    0",
                  "Simulation complete via $finish(1) at time 25 US + 0",
                  "TOOL:\txrun(64)\t23.09-s001: Exiting on Nov 08, 2026 at 09:03:10 CET  (total: 00:03:10)"]
        return "\n".join(lines) + "\n"

    for rid, test, errors, rc, start in [("20261108_090000_xrun_e4e4e4", "smoke_test", 0, 0, "2026-11-08T09:00:00"),
                                          ("20261108_093000_xrun_f5f5f5", "dma_burst_test", 3, 1, "2026-11-08T09:30:00")]:
        r = runs / rid
        r.mkdir(parents=True, exist_ok=True)
        (r / "xrun.log").write_text(xlog(test, errors))
        job(r, "xrun", "done" if rc == 0 else "failed", rc, start, start[:11] + "09:59:00", 190.0,
            ["xrun", "-uvm", f"+UVM_TESTNAME={test}"], "xrun.log")

    config = {"access": "read", "workdir": str(work), "allowed_roots": [str(dest)],
              "default_profile": "ic231", "profiles": cfg_profiles,
              "virtuoso": {"project_dir": str(dest)}}
    (dest / "config.json").write_text(json.dumps(config, indent=2))
    (dest / "cds.lib").write_text("".join(f"DEFINE {lib['name']} {lib['path']}\n" for lib in libs))
    return config


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cadence-demo")
    build(target)
    print(f"demo environment ready: export CADENCE_MCP_CONFIG={target.resolve() / 'config.json'}")
