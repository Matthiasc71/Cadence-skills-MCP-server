"""End-to-end: launch the server over stdio with the MCP client and call tools."""

from __future__ import annotations

import json
import sys

import anyio
import pytest
from fakes import DATA, fake_env

mcp = pytest.importorskip("mcp")
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402


def _text(result) -> str:
    content = getattr(result, "content", None) or []
    return "\n".join(getattr(c, "text", "") for c in content)


def _payload(result):
    return json.loads(_text(result))


def test_stdio_roundtrip(tmp_path, fakebin):
    env = fake_env(fakebin)
    env.update({"CADENCE_MCP_WORKDIR": str(tmp_path / "work"), "CADENCE_MCP_ALLOWED_ROOTS": f"{tmp_path}:{DATA}"})
    params = StdioServerParameters(command=sys.executable, args=["-m", "cadence_mcp"], env=env)

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                instructions = getattr(init, "instructions", "") or ""
                assert "feature probes" in instructions
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert all(n.startswith("cadence_") for n in names)
                for expected in ("cadence_get_environment", "cadence_skill_eval", "cadence_spectre_run",
                                 "cadence_psf_read", "cadence_digital_run_tcl", "cadence_xcelium_run",
                                 "cadence_allegro_run_skill", "cadence_get_run", "cadence_list_runs"):
                    assert expected in names
                ro = [t for t in tools.tools if t.name == "cadence_psf_read"][0].annotations
                dump = ro.model_dump(by_alias=True)  # camelCase on the wire in both SDKs
                assert dump["readOnlyHint"] is True and dump["idempotentHint"] is True
                assert dump["openWorldHint"] is False
                envr = _payload(await session.call_tool("cadence_get_environment", {"response_format": "json"}))
                assert envr["tools"]["virtuoso"]["version"] == "IC23.1 ISR9"
                psf = _payload(await session.call_tool("cadence_psf_read", {"path": str(DATA / "ac.ac"), "signals": ["out"], "response_format": "json"}))
                assert round(psf["data"]["out"]["mag_first_db"]) == 40
                refused = await session.call_tool("cadence_skill_eval", {"code": "dbSave(cv)", "mode": "batch"})
                is_error = getattr(refused, "is_error", None)
                if is_error is None:
                    is_error = getattr(refused, "isError", False)
                assert is_error and "write" in _text(refused)
                ok = _payload(await session.call_tool("cadence_skill_eval", {"code": "1+2", "mode": "batch", "response_format": "json"}))
                md = _text(await session.call_tool("cadence_skill_eval", {"code": "1+2", "mode": "batch"}))
                assert md.startswith("# SKILL") and "**value**: 3" in md
                assert ok["status"] == "ok" and ok["value"] == "3"
                res = await session.read_resource("cadence://compatibility")
                assert "IC25.1" in res.contents[0].text
                return len(names)

    n = anyio.run(scenario)
    assert n == 36
