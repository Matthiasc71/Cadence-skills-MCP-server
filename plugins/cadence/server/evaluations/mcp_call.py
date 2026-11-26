#!/usr/bin/env python3
"""Call cadence_mcp tools over stdio, as an MCP client would (used to explore the demo and verify answers).

    python evaluations/mcp_call.py <config.json> <tool> '<json args>' [<tool> '<json args>' ...]
"""
from __future__ import annotations

import json
import os
import sys

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def call_many(config: str, calls: list[tuple[str, dict]]) -> list[tuple[bool, str]]:
    env = dict(os.environ, CADENCE_MCP_CONFIG=config)
    params = StdioServerParameters(command=sys.executable, args=["-m", "cadence_mcp"], env=env)
    results = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for name, args in calls:
                res = await s.call_tool(name, args)
                err = bool(getattr(res, "is_error", None) or getattr(res, "isError", False))
                results.append((err, "\n".join(getattr(c, "text", "") for c in res.content)))
    return results


def main() -> None:
    config, rest = sys.argv[1], sys.argv[2:]
    calls = [(rest[i], json.loads(rest[i + 1])) for i in range(0, len(rest), 2)]
    for (name, _), (err, text) in zip(calls, anyio.run(call_many, config, calls)):
        print(f"===== {name}{' [ERROR]' if err else ''}\n{text}")


if __name__ == "__main__":
    main()
