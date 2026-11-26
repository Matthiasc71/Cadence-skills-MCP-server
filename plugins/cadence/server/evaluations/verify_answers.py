#!/usr/bin/env python3
"""Solve every question of cadence_eval.xml with read-only cadence_mcp tool calls and compare.

    python evaluations/build_demo_env.py /tmp/cadence-demo
    python evaluations/verify_answers.py /tmp/cadence-demo/config.json

This is the "answer verification" step of the mcp-builder evaluation guide, scripted so it can run
in CI; it is not a substitute for running the LLM evaluation harness (scripts/evaluation.py).
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).parent))
from mcp_call import call_many  # noqa: E402

READ_ONLY = {"cadence_list_profiles", "cadence_list_runs", "cadence_get_run", "cadence_psf_read",
             "cadence_spectre_get_info", "cadence_virtuoso_list_libraries", "cadence_virtuoso_list_cells",
             "cadence_virtuoso_get_cellview", "cadence_digital_summarize_log", "cadence_read_text_file"}


class Client:
    def __init__(self, config: str):
        self.config = config
        self.calls = 0

    def __call__(self, tool: str, **args):
        assert tool in READ_ONLY, f"{tool} is not a read-only tool"
        self.calls += 1
        args.setdefault("response_format", "json")
        (err, text), = anyio.run(call_many, self.config, [(tool, args)])
        if err:
            raise RuntimeError(f"{tool} failed: {text}")
        return json.loads(text)


def all_runs(c: Client, tool: str) -> list[dict]:
    items, offset = [], 0
    while offset is not None:
        page = c("cadence_list_runs", tool_name=tool, limit=20, offset=offset)
        items += page["items"]
        offset = page["next_offset"]
    return sorted(items, key=lambda r: r["started"])


def q1(c):
    runs = all_runs(c, "innovus")
    viol = [c("cadence_get_run", run_id=r["run_id"], tail_lines=0)["summary"]["timing"]["violating_paths_last"] for r in runs]
    met = viol.index(0)
    return str(viol[met - 1])


def q2(c):
    for r in all_runs(c, "spectre"):
        run = c("cadence_get_run", run_id=r["run_id"], tail_lines=0)
        if run["state"] == "done" and "ac.ac" in run["summary"]["results"]:
            data = c("cadence_psf_read", path=f"{run['run_dir']}/psf/ac.ac", signals=["*"], max_points=2)["data"]
            return str(round(next(iter(data.values()))["x_at_unity"] / 1e6))


def q3(c):
    import re
    for r in all_runs(c, "spectre"):
        run = c("cadence_get_run", run_id=r["run_id"], tail_lines=0)
        for e in run["summary"]["errors"]:
            m = re.search(r"undefined model or subcircuit, `([^']+)'", e)
            if m:
                return m.group(1)


def q4(c):
    for p in c("cadence_list_profiles"):
        info = c("cadence_spectre_get_info", profile=p["name"])
        if (info["major"], info["minor"]) < (19, 1):
            return p["name"]


def q5(c):
    cells = c("cadence_virtuoso_list_cells", library="amp_lib", limit=500)["items"]
    return next(x["name"] for x in cells if "maestro" in x["views"] and "layout" not in x["views"])


def q6(c):
    cv = c("cadence_virtuoso_get_cellview", library="amp_lib", cell="ota_2stage", view="schematic", limit=500)
    widths = [float(dict(i["params"])["w"].rstrip("u")) for i in cv["instances"] if i["cell"].startswith("pmos")]
    return f"{sum(widths):g}"


def q7(c):
    run = all_runs(c, "genus")[0]
    ids = c("cadence_get_run", run_id=run["run_id"], tail_lines=0)["summary"]["warning_ids"]
    return max(ids, key=ids.get)


def q8(c):
    import re
    for r in all_runs(c, "xrun"):
        run = c("cadence_get_run", run_id=r["run_id"], tail_lines=0)
        if run["summary"]["errors"] or (run["summary"]["uvm_summary"] or {}).get("UVM_ERROR"):
            log = c("cadence_read_text_file", path=f"{run['run_dir']}/xrun.log", head_lines=5)["text"]
            return re.search(r"Running test (\w+)", log).group(1)


def q9(c):
    for r in all_runs(c, "spectre"):
        run = c("cadence_get_run", run_id=r["run_id"], tail_lines=0)
        if "tran.tran" in run["summary"].get("results", []):
            data = c("cadence_psf_read", path=f"{run['run_dir']}/psf/tran.tran", signals=["vout"], max_points=2)["data"]
            return f"{data['vout']['max']:.2f}"


def q10(c):
    count, offset = 0, 0
    while offset is not None:
        page = c("cadence_virtuoso_list_cells", library="analogLib", limit=50, offset=offset)
        count += sum("veriloga" in x["views"] for x in page["items"])
        offset = page["next_offset"]
    return str(count)


SOLVERS = [q1, q2, q3, q4, q5, q6, q7, q8, q9, q10]


def verify(config: str, xml_path: Path) -> list[tuple[str, str, str, bool]]:
    pairs = ET.parse(xml_path).getroot().findall("qa_pair")
    assert len(pairs) == len(SOLVERS) == 10
    results = []
    for pair, solver in zip(pairs, SOLVERS):
        c = Client(config)
        got = solver(c)
        expected = pair.findtext("answer").strip()
        results.append((pair.findtext("question").strip(), expected, got, got == expected, c.calls))
    return results


if __name__ == "__main__":
    cfg = sys.argv[1]
    res = verify(cfg, Path(__file__).with_name("cadence_eval.xml"))
    for i, (q, exp, got, ok, calls) in enumerate(res, 1):
        print(f"Q{i:02d} {'OK ' if ok else 'BAD'} expected={exp!r} got={got!r} ({calls} tool calls)")
    sys.exit(0 if all(r[3] for r in res) else 1)
