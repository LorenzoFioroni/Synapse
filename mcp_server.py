#!/usr/bin/env python3
"""Local MCP server: gives Claude Desktop / Claude Code access to the library.

The AI app starts this script itself and talks to it over stdin/stdout; you do
not run it by hand. It reads the same ``data/`` folder as the web app (which
does not need to be running). New notes go into ``notes/ai/``; edits of existing notes are
hash-checked and recorded in ``notes/.history/`` so they can be undone. See docs/mcp.md.

    python mcp_server.py --config     # print the setup for Claude Desktop and Claude Code
    python mcp_server.py --data DIR   # use another data folder (default: ./data or $SYNAPSE_DATA_DIR)

See the README section "AI access (MCP)".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

VERSION = "0.1.0"


def _config(data_dir: Path, explicit: bool) -> str:
    args = [str(ROOT / "mcp_server.py")] + (["--data", str(data_dir)] if explicit else [])
    desktop = {"mcpServers": {"synapse": {"command": sys.executable, "args": args}}}
    code = " ".join(json.dumps(a) if " " in a else a for a in [sys.executable] + args)
    return (
        "Claude Desktop: Settings → Developer → Edit Config, and merge this into claude_desktop_config.json\n"
        "(on macOS ~/Library/Application Support/Claude/claude_desktop_config.json), then restart Claude:\n\n"
        + json.dumps(desktop, indent=2)
        + "\n\nClaude Code (available in every project):\n\n"
        + f"claude mcp add --scope user synapse -- {code}\n"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MCP server for the library (stdio).")
    ap.add_argument("--data", help="data folder (default: ./data or $SYNAPSE_DATA_DIR)")
    ap.add_argument("--config", action="store_true", help="print the setup for Claude Desktop / Claude Code and exit")
    ap.add_argument("--verbose", action="store_true", help="log more to stderr")
    ap.add_argument("--safe-mode", action="store_true", help="start no plugins")
    a = ap.parse_args(argv)

    from backend.config import Config

    config = Config.from_env(a.data)
    config.safe_mode = a.safe_mode

    if a.config:
        print(
            _config(
                config.data_dir, bool(a.data or os.environ.get("SYNAPSE_DATA_DIR") or os.environ.get("ZCLONE_DATA_DIR"))
            )
        )
        return 0

    # stdout carries the protocol: all logging goes to stderr (Claude shows it in its MCP logs).
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO if a.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.db_path.exists():
        print(
            f"No library found at {config.data_dir} (missing library.db). Start the app once, "
            f"or pass --data with the right folder.",
            file=sys.stderr,
        )
        return 1

    from backend.mcp import build_server, serve_stdio
    from backend.services import Services

    services = Services(config)
    # This process never runs the app's background jobs (attachment sweeps etc.).
    services.note_service.on_change = None
    services.plugins.start("mcp")  # their tools are offered to Claude too
    serve_stdio(build_server(services, VERSION))
    return 0


if __name__ == "__main__":
    sys.exit(main())
