"""Command line: cadence-mcp [serve|cdsinit|doctor]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cdsinit(args: argparse.Namespace) -> int:
    from .virtuoso import helper_path, skill_str
    try:
        import skillbridge
        server_il = Path(skillbridge.__file__).parent / "server" / "python_server.il"
    except ImportError:
        print("skillbridge is not installed in this Python: pip install skillbridge", file=sys.stderr)
        return 1
    python = args.python or sys.executable
    id_part = f" ?id {skill_str(args.id)}" if args.id else ""
    tcp_part = " ?forceTcp t" if args.tcp else ""
    print(";; ---- cadence-mcp: add to ~/.cdsinit (or paste into the CIW) ----")
    print(f"load({skill_str(str(server_il))})")
    print(f"pyStartServer({id_part.strip()}{tcp_part} ?python {skill_str(python)})".replace("( ", "("))
    print(f"load({skill_str(str(helper_path()))})")
    print(";; -----------------------------------------------------------------")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    from .config import load_settings
    from .env import discover
    from .profiles import ProfileManager
    from .server import dumps
    settings = load_settings(args.config)
    pm = ProfileManager(settings)
    report = {
        "access": settings.access,
        "allow_shell": settings.allow_shell,
        "workdir": str(settings.workdir),
        "allowed_roots": [str(r) for r in settings.all_roots()],
        "profiles": pm.describe(),
    }
    try:
        from importlib.metadata import version
        report["mcp_sdk"] = version("mcp")
    except Exception as exc:  # noqa: BLE001
        report["mcp_sdk"] = f"missing ({exc})"
    try:
        from importlib.metadata import version
        report["skillbridge"] = version("skillbridge")
    except Exception:  # noqa: BLE001
        report["skillbridge"] = "not installed (live Virtuoso disabled, batch still works)"
    names = [args.profile] if args.profile else (pm.names() or [None])
    report["discovery"] = {str(n): discover(settings, pm, n, check_versions=not args.fast) for n in names}
    print(dumps(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("cadence-mcp", description="MCP server for Cadence tools")
    parser.add_argument("--config", help="JSON config file (default: $CADENCE_MCP_CONFIG)")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the MCP server (default)")
    serve.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    ci = sub.add_parser("cdsinit", help="print the .cdsinit lines that start skillbridge + helpers")
    ci.add_argument("--id", default=None, help="skillbridge workspace id (numeric when using TCP)")
    ci.add_argument("--tcp", action="store_true", help="force TCP sockets")
    ci.add_argument("--python", default=None, help="python used by Virtuoso to run the bridge (>= 3.6)")
    doc = sub.add_parser("doctor", help="check configuration and discover tools/versions")
    doc.add_argument("--profile", default=None)
    doc.add_argument("--fast", action="store_true", help="skip version queries")
    args = parser.parse_args(argv)

    if args.command == "cdsinit":
        return _cdsinit(args)
    if args.command == "doctor":
        return _doctor(args)

    from .config import load_settings
    from .server import build_server
    server = build_server(load_settings(args.config))
    transport = getattr(args, "transport", "stdio")
    if transport == "stdio":
        server.run()
        return 0
    try:
        server.run(transport=transport, host=args.host, port=args.port)
    except TypeError:  # SDK 1.x: host/port live in settings
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport=transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
