"""Unit tests: versions, guardrails, PSF reader, log parsers, SKILL helper syntax."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fakes import DATA

from cadence_mcp import digital, safety, versions
from cadence_mcp.psf import read_psf_ascii
from cadence_mcp.spectre import summarize_log
from cadence_mcp.virtuoso import helper_path, parse_result_file, skill_str


# ------------------------------------------------------------------ versions

@pytest.mark.parametrize("raw,label", [
    ("sub-version  IC6.1.7-64b.500.23 ", "IC6.1.7"),
    ("sub-version  IC6.1.8-64b.500.31 ", "IC6.1.8"),
    ("sub-version  ICADVM20.1-64b.500.11 ", "ICADVM20.1"),
    ("sub-version  IC23.1-64b.ISR9.24 ", "IC23.1 ISR9"),
    ("sub-version  IC25.1-64b.ISR8.13 ", "IC25.1 ISR8"),
    ("/eda/cadence/IC231/tools/bin/virtuoso", "IC23.1"),
    ("/eda/cadence/IC617/tools/bin/virtuoso", "IC6.1.7"),
])
def test_virtuoso_versions(raw, label):
    assert versions.parse_virtuoso(raw).label == label


def test_virtuoso_notes_track_ade_removal():
    v617 = versions.parse_virtuoso("IC6.1.7-64b.500.1")
    v231 = versions.parse_virtuoso("IC23.1-64b.ISR1.2")
    v251 = versions.parse_virtuoso("IC25.1-64b.ISR1.2")
    assert any("deprecated" in n for n in v617.notes)
    assert any("ADE L and ADE XL environments are removed" in n for n in v231.notes)
    assert any("axl* session API still exists" in n for n in v231.notes)
    assert any(".simrc" in n for n in v251.notes)


def test_spectre_engine_args_by_version():
    old = versions.parse_spectre("sub-version 18.1.0.421.isr12")
    new = versions.parse_spectre("@(#)$CDS: spectre version 21.1.0 64bit")
    assert versions.spectre_engine_args("x", "mx", new) == (["+preset=mx"], [])
    args, warn = versions.spectre_engine_args("x", None, old)
    assert args == ["+aps"] and warn
    with pytest.raises(ValueError):
        versions.spectre_engine_args("x", "zz", new)


def test_digital_and_xcelium_versions():
    assert versions.parse_digital("innovus", "Version: v21.18-s099_1").label == "INNOVUS21.1 ISR8"
    assert versions.parse_digital("genus", "Version 17.11-s014_1").label == "GENUS17.1 ISR1"
    assert versions.parse_xcelium("TOOL:\txrun(64)\t23.09-s001").label == "XCELIUM 23.09-s001"
    assert versions.parse_spb("C:/Cadence/SPB_24.1/tools/bin").label == "SPB24.1"


# ------------------------------------------------------------------ guardrails

def test_skill_guard_levels(settings):
    run = settings("run")
    write = settings("write")
    assert safety.skill_problems("x = 1 + 2 printf(\"%d\" x)", run, live=True) == []
    assert safety.skill_problems('dbSave(cv)', run, live=False)
    assert safety.skill_problems('cv~>name = "x"', run, live=False)
    assert safety.skill_problems('cv~>name == "x"', run, live=False) == []
    assert safety.skill_problems('dbSave(cv)', write, live=False) == []
    assert safety.skill_problems('system("rm -rf /")', write, live=False)  # shell needs allow_shell
    assert safety.skill_problems('exit()', write, live=True)  # never in live mode
    assert safety.skill_problems('exit()', write, live=False) == []
    # identifiers inside strings and comments are ignored
    assert safety.skill_problems('printf("dbSave is fine here") ; dbDeleteObject(x)\n', run, live=True) == []
    # envSetVal / outfile are acceptable in a throw-away batch session, not live
    assert safety.skill_problems('envSetVal("spectre.envOpts" "x" \'string "y")', run, live=False) == []
    assert safety.skill_problems('envSetVal("spectre.envOpts" "x" \'string "y")', run, live=True)
    # funcall/evalstring cannot be screened
    assert safety.skill_problems('funcall(stringToSymbol("dbSave") cv)', run, live=False)


def test_tcl_guard(settings):
    run = settings("run")
    assert safety.tcl_problems("set_db max_cpus_per_server 8\nsyn_generic\n", run) == []
    assert safety.tcl_problems("exec rm -rf /tmp/x\n", run)
    assert safety.tcl_problems("set f [open \"|ls\" r]\n", run)
    assert safety.tcl_problems("# exec in a comment is fine\nreport_timing\n", run) == []
    assert safety.tcl_problems("file delete foo.v\n", run)
    assert safety.tcl_problems("file delete foo.v\n", settings("write")) == []


def test_path_confinement(settings, tmp_path):
    s = settings("run")
    (tmp_path / "a.txt").write_text("x")
    assert safety.confine(tmp_path / "a.txt", s).name == "a.txt"
    with pytest.raises(safety.AccessDenied):
        safety.confine("/etc/passwd", s)


# ------------------------------------------------------------------ PSF

def test_psf_tran_group():
    r = read_psf_ascii(DATA / "tran.tran", ["out", "V0:p", "nope"])
    assert r["sweep"] == "time" and r["points"] == 3
    assert r["signals"] == ["out", "in", "V0:p"]
    assert r["data"]["out"]["max"] == 1.2 and r["data"]["out"]["last"] == 1.2
    assert r["data"]["V0:p"]["min"] == -3e-6
    assert r["not_found"] == ["nope"]


def test_psf_ac_metrics():
    r = read_psf_ascii(DATA / "ac.ac", ["out"])
    d = r["data"]["out"]
    assert round(d["mag_first_db"], 3) == 40.0
    assert 10 < d["x_at_minus_3db"] <= 100.5
    assert 1000 < d["x_at_unity"] < 10000
    assert d["columns"] == ["x", "mag", "mag_db", "phase_deg"]


def test_psf_dcop_struct():
    r = read_psf_ascii(DATA / "dcOp.dc")
    assert r["values"]["out"] == 0.9
    assert r["values"]["M0"] == [0.42, 1.5e-05]


def test_psf_rejects_binary(tmp_path):
    p = tmp_path / "tran.tran"
    p.write_bytes(b"\x00\x00\x04\x00binary")
    with pytest.raises(ValueError, match="psfascii"):
        read_psf_ascii(p)


# ------------------------------------------------------------------ logs

def test_spectre_log_summary():
    s = summarize_log(DATA / "spectre.out")
    assert s["completed"] and s["counts"] == {"errors": 1, "warnings": 2, "notices": 0}
    assert s["error_ids"] == {"SFE-23": 1}
    assert s["analyses"][0]["name"] == "tran"
    assert "convergence failure" in s["convergence_issues"]


def test_innovus_log_summary():
    s = digital.summarize_tool_log(DATA / "innovus.log")
    assert s["errors"] == 1 and s["warnings"] == 2
    assert s["warning_ids"] == {"IMPLF-200": 2}
    assert s["timing"]["wns_ns_table_last"] == -0.123
    assert s["timing"]["violating_paths_last"] == 42


def test_xrun_log_summary():
    s = digital.summarize_xrun_log(DATA / "xrun.log")
    assert s["errors"] == 2 and s["warnings"] == 1
    assert s["uvm_summary"]["UVM_ERROR"] == 2
    assert s["tool_version"] == "xrun 23.09-s001"


def test_ui_detection():
    assert digital.detect_ui("innovus", "set_db design_process_node 7\nplace_opt_design") == "stylus"
    assert digital.detect_ui("innovus", "setDesignMode -process 28\nplaceDesign") == "legacy"
    assert digital.detect_ui("innovus", "puts hello") == "legacy"
    assert digital.detect_ui("genus", "puts hello") == "stylus"
    assert digital.cpu_preamble("innovus", "legacy", 8) == "setMultiCpuUsage -localCpu 8\n"
    assert digital.cpu_preamble("innovus", "stylus", 8) == "set_multi_cpu_usage -local_cpu 8\n"


# ------------------------------------------------------------------ SKILL helper file

def _strip(code: str) -> str:
    out, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if c == ";":
            j = code.find("\n", i)
            i = n if j < 0 else j
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            assert j >= 0, "unterminated comment"
            i = j + 2
        elif c == '"':
            j = i + 1
            while code[j] != '"':
                j += 2 if code[j] == "\\" else 1
                assert j < n, "unterminated string"
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def test_skill_helper_is_balanced():
    src = helper_path().read_text()
    code = _strip(src)
    depth = 0
    for ch in code:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        assert depth >= 0
    assert depth == 0
    # every procedure is CdsMcp-prefixed
    names = re.findall(r"procedure\(\s*(\w+)", src)
    assert names and all(n.startswith("CdsMcp") for n in names)
    # no release-specific functions without an isCallable guard
    for fn in ("hiGraphicMode", "geGetEditCellView", "dbGetDatabaseType", "getWarn"):
        assert f"isCallable('{fn})" in src


def test_skill_str_and_result_parsing():
    assert skill_str('a"b\\c\nd') == '"a\\"b\\\\c\\nd"'
    r = parse_result_file("CDSMCP_STATUS ok\nCDSMCP_KIND fixnum\nCDSMCP_OUTPUT_BEGIN\nx\ny\nCDSMCP_OUTPUT_END\n"
                          "CDSMCP_VALUE_BEGIN\n42\nCDSMCP_VALUE_END\n")
    assert (r.status, r.kind, r.output, r.value) == ("ok", "fixnum", "x\ny", "42")


def test_compat_matrix_mentions_all_versions():
    md = versions.COMPAT_MATRIX_MD
    for v in ("IC6.1.7", "IC6.1.8", "IC23.1", "IC25.1", "Spectre X", "-stylus", "-legacy_ui", "xrun", "allegro -s"):
        assert v in md, v


def test_readme_exists():
    assert (Path(__file__).parents[1] / "README.md").exists()
