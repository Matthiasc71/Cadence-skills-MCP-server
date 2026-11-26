"""Backend tests against fake Cadence executables and a fake skillbridge workspace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import DATA

from cadence_mcp.digital import DigitalBackend
from cadence_mcp.env import discover
from cadence_mcp.jobs import JobManager
from cadence_mcp.pcb import PcbBackend
from cadence_mcp.profiles import ProfileManager
from cadence_mcp.safety import AccessDenied
from cadence_mcp.spectre import SpectreBackend
from cadence_mcp.virtuoso import VirtuosoBackend


def test_discover(managers):
    s, jobs, pm = managers
    d = discover(s, pm)
    assert d["tools"]["virtuoso"]["version"] == "IC23.1 ISR9"
    assert d["tools"]["spectre"]["version"] == "SPECTRE23.1.0 ISR8"
    assert d["tools"]["innovus"]["version"] == "INNOVUS21.1 ISR8"
    assert d["tools"]["xrun"]["version"] == "XCELIUM 23.09-s001"
    assert "tempus" in d["missing"]


def test_spectre_run_engine_x(managers, tmp_path):
    s, jobs, pm = managers
    sp = SpectreBackend(s, jobs, pm)
    net = tmp_path / "tb.scs"
    net.write_text("simulator lang=spectre\n")
    out = sp.run(netlist_path=str(net), engine="x", preset="ax", threads=4)
    assert out["state"] == "done" and out["summary"]["completed"]
    args = json.loads((Path(out["run_dir"]) / "psf" / "args.json").read_text())
    assert "+preset=ax" in args and "+mt=4" in args and args[args.index("-format") + 1] == "psfascii"
    assert out["cwd"] == str(tmp_path)  # runs next to the netlist so includes resolve
    assert "tran.tran" in out["results"]


def test_spectre_old_version_falls_back_to_aps(settings, monkeypatch):
    s = settings("run", FAKE_SPECTRE_W="sub-version 18.1.0.421.isr12", FAKE_SPECTRE_V="18.1.0")
    sp = SpectreBackend(s, JobManager(), ProfileManager(s))
    out = sp.run(netlist_text="simulator lang=spectre\n", engine="x")
    args = json.loads((Path(out["run_dir"]) / "psf" / "args.json").read_text())
    assert "+aps" in args and not any(a.startswith("+preset") for a in args)
    assert out["warnings"]


def test_spectre_background_job(managers):
    s, jobs, pm = managers
    out = SpectreBackend(s, jobs, pm).run(netlist_text="x", wait=False)
    assert out["state"] in ("running", "done")
    job = jobs.get(out["run_id"])
    assert jobs.wait(job, 30)


def test_read_level_blocks_runs(settings):
    s = settings("read")
    sp = SpectreBackend(s, JobManager(), ProfileManager(s))
    with pytest.raises(AccessDenied):
        sp.run(netlist_text="x")


def test_innovus_stylus_flag_and_cpus(managers):
    s, jobs, pm = managers
    dig = DigitalBackend(s, jobs, pm)
    out = dig.run_tcl("innovus", script="set_db design_process_node 7\nplace_opt_design\n", cpus=8)
    assert out["ui"] == "stylus"
    log = Path(out["tool_logs"][0]).read_text()
    assert "-stylus" in log.splitlines()[0]
    assert "set_multi_cpu_usage -local_cpu 8" in log
    assert out["summary"]["timing"]["wns_ns_table_last"] == -0.123


def test_genus_legacy_flag(managers):
    s, jobs, pm = managers
    out = DigitalBackend(s, jobs, pm).run_tcl("genus", script="set_attribute lib_search_path . /\n", ui="auto")
    assert out["ui"] == "legacy"
    first = Path(out["tool_logs"][0]).read_text().splitlines()[0]
    assert "-legacy_ui" in first
    assert out["summary"]["error_ids"] == {"TUI-61": 1}


def test_xrun(managers):
    s, jobs, pm = managers
    src = DATA / "xrun.log"  # any existing file inside the allowed roots
    out = DigitalBackend(s, jobs, pm).xrun([str(src)], top="tb", options=["-sv"], uvm=True)
    assert out["state"] == "failed" and out["summary"]["errors"] == 2
    assert "-uvm" in out["command"] and "-xmlibdirpath" in out["command"]


def test_virtuoso_batch(managers, tmp_path):
    s, jobs, pm = managers
    v = VirtuosoBackend(s, jobs, pm)
    v._open_workspace = lambda *a: (_ for _ in ()).throw(RuntimeError("No server found. Is it running?"))
    info = v.info()
    assert info["mode"] == "batch"
    assert info["parsed_version"]["label"] == "IC23.1 ISR9"
    assert info["features"] == {"maeOpenSetup": True, "axlGetWindowSession": True, "sevSession": False}
    assert any("mae* API is callable" in h for h in info["hints"])
    assert any("isCallable on an axl* function cannot tell" in n for n in info["parsed_version"]["notes"])
    r = v.eval("1+2", mode="batch")
    assert (r.status, r.value, r.output) == ("ok", "3", "hello from batch")
    err = v.eval("boom()", mode="batch")
    assert err.status == "error" and "undefined function" in err.value
    with pytest.raises(AccessDenied):
        v.eval("dbSave(cv)", mode="batch")
    libs, mode = v.query_json("CdsMcpListLibs()", mode="batch")
    assert libs[0]["name"] == "analogLib" and mode == "batch"


class FakeWS:
    def __init__(self):
        self.calls = []

    def __getitem__(self, name):
        def call(*args):
            self.calls.append((name, args))
            if name == "isCallable":
                return True
            if name == "CdsMcpEval":
                code = args[0]
                if "CdsMcpVersionInfo" in code:
                    return ["ok", "", json.dumps({"subVersion": "sub-version  IC6.1.8-64b.500.31 ",
                                                  "features": [{"name": "axlGetWindowSession", "callable": True}]}),
                            "string"]
                return ["ok", "printed\n", "t", "symbol"]
            if name == "CdsMcpEvalFile":
                return ["ok", "", Path(args[0]).read_text(), "string"]
            raise AssertionError(name)
        return call

    def close(self):
        pass


def test_virtuoso_live(managers):
    s, jobs, pm = managers
    v = VirtuosoBackend(s, jobs, pm)
    ws = FakeWS()
    v._open_workspace = lambda *a: ws
    assert v.connect()["connected"]
    r = v.eval('printf("hi\\n")')
    assert r.mode == "live" and r.output == "printed\n"
    assert v.info()["parsed_version"]["label"] == "IC6.1.8"
    # non-ASCII code goes through a file (skillbridge escapes would corrupt it)
    r2 = v.eval('printf("tension d\'entrée\\n")')
    assert "entrée" in r2.value
    with pytest.raises(AccessDenied):
        v.eval("exit()")  # would kill the user's session


def test_live_timeout_marks_desync(managers):
    import time
    s, jobs, pm = managers
    v = VirtuosoBackend(s, jobs, pm)

    class Slow(FakeWS):
        def __getitem__(self, name):
            if name == "CdsMcpEval":
                return lambda *a: time.sleep(2)
            return super().__getitem__(name)
    v._open_workspace = lambda *a: Slow()
    v.connect()
    with pytest.raises(TimeoutError):
        v.eval("1", timeout_s=0.3)
    with pytest.raises(RuntimeError, match="out of sync"):
        v.eval("1", mode="live")


def test_allegro_board_summary(managers, tmp_path):
    s, jobs, pm = managers
    brd = tmp_path / "demo.brd"
    brd.write_bytes(b"fake")
    out = PcbBackend(s, jobs, pm).board_summary(str(brd))
    assert out["status"] == "ok" and out["board"]["nets"] == 34
    scr = (Path(out["run_dir"]) / "run.scr").read_text()
    assert scr.startswith("skill load(") and scr.rstrip().endswith("exit")


def test_profiles_setup_script(tmp_path, settings, fakebin):
    script = tmp_path / "setup_ic251.sh"
    script.write_text(f'export CDSHOME=/eda/IC251\nexport PATH="{fakebin}:$PATH"\n')
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"profiles": {"ic251": {"setup": str(script), "env": {"MY_VAR": "${CDSHOME}/x"}}},
                               "default_profile": "ic251"}))
    s = settings("run", CADENCE_MCP_CONFIG=str(cfg))
    pm = ProfileManager(s)
    env = pm.env(None)
    assert env["CDSHOME"] == "/eda/IC251" and env["MY_VAR"] == "/eda/IC251/x"
    assert pm.which("virtuoso", "ic251").startswith(str(fakebin))
    with pytest.raises(ValueError):
        pm.env("nope")


def _call(server, name, args):
    """Call a tool in-process and decode its JSON text answer (SDK 1.x and 2.x)."""
    import asyncio
    res = asyncio.run(server.call_tool(name, {**args, "response_format": "json"}))
    if isinstance(res, tuple):  # SDK 1.x: (content, structured)
        res = res[0]
    content = res if isinstance(res, list) else res.content
    return json.loads(content[0].text)


def test_netlist_and_ocean_through_server(settings, tmp_path):
    from cadence_mcp.server import build_server
    s = settings("run", CADENCE_MCP_PROJECT_DIR=str(tmp_path))
    server = build_server(s)
    out = _call(server, "cadence_virtuoso_create_netlist", {"library": "amp", "cell": "ota", "view": "schematic"})
    assert out["netlists"] and out["netlists"][0].endswith("ota/spectre/schematic/netlist/input.scs")
    assert "simulator lang=spectre" in out["netlist_head"]
    assert out["cwd"] == str(tmp_path)
    assert "-restore" in out["command"]
    oc = _call(server, "cadence_ocean_run_script", {"script": 'simulator(\'spectre)\nocnPrint(1)'})
    assert oc["state"] == "done"
    assert (Path(oc["run_dir"]) / "script.ocn").read_text().rstrip().endswith("exit()")


def test_runs_listing_and_pagination(settings, tmp_path):
    from cadence_mcp.server import build_server
    s = settings("run")
    server = build_server(s)
    for _ in range(3):
        _call(server, "cadence_spectre_run", {"netlist_text": "simulator lang=spectre\n"})
    page = _call(server, "cadence_list_runs", {"tool_name": "spectre", "limit": 2})
    assert page["total"] == 3 and page["count"] == 2 and page["has_more"] and page["next_offset"] == 2
    run = _call(server, "cadence_get_run", {"run_id": page["items"][0]["run_id"]})
    assert run["state"] == "done" and run["summary"]["completed"] and "tran.tran" in run["summary"]["results"]
    files = _call(server, "cadence_find_files", {"pattern": "**/tran.tran", "limit": 1})
    assert files["total"] == 3 and files["count"] == 1
    # a run directory left by a previous server process is still described from job.json
    server2 = build_server(s)
    again = _call(server2, "cadence_get_run", {"run_id": page["items"][1]["run_id"]})
    assert again["state"] == "done" and again["summary"]["counts"]["errors"] == 0


def test_virtuoso_db_paging(settings, tmp_path):
    from cadence_mcp.server import build_server
    db = {"subVersion": "sub-version  IC6.1.7-64b.500.23 ", "version": "v", "dbType": "OpenAccess",
          "features": {"axlGetWindowSession": True, "maeOpenSetup": True},
          "libraries": [{"name": "amp", "path": "/p/amp", "cells": {
              f"c{i:02d}": {"views": {"schematic": {"cellViewType": "schematic",
                                                    "instances": [{"name": f"M{j}"} for j in range(i)],
                                                    "nets": [], "terminals": []}}} for i in range(7)}}]}
    dbf = tmp_path / "db.json"
    dbf.write_text(json.dumps(db))
    s = settings("run", FAKE_VIRTUOSO_DB=str(dbf))
    server = build_server(s)
    server._cadence["virtuoso"]._open_workspace = lambda *a: (_ for _ in ()).throw(RuntimeError("no server"))
    cells = _call(server, "cadence_virtuoso_list_cells", {"library": "amp", "limit": 3, "offset": 3})
    assert cells["total"] == 7 and [c["name"] for c in cells["items"]] == ["c03", "c04", "c05"]
    assert cells["next_offset"] == 6
    cv = _call(server, "cadence_virtuoso_get_cellview", {"library": "amp", "cell": "c06", "limit": 4})
    assert cv["instanceCount"] == 6 and len(cv["instances"]) == 4 and cv["next_offset"] == 4
    info = _call(server, "cadence_virtuoso_get_info", {})
    assert info["parsed_version"]["label"] == "IC6.1.7" and info["features"]["axlGetWindowSession"] is True


def test_markdown_default_and_errors(settings):
    import asyncio
    from cadence_mcp.server import build_server
    server = build_server(settings("run"))
    res = asyncio.run(server.call_tool("cadence_list_profiles", {}))
    content = res[0] if isinstance(res, tuple) else (res if isinstance(res, list) else res.content)
    assert content[0].text.startswith("# Profiles")
    with pytest.raises(Exception) as exc:
        asyncio.run(server.call_tool("cadence_get_run", {"run_id": "nope"}))
    assert "cadence_list_runs" in str(exc.value)
