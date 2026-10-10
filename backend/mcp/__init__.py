"""MCP (Model Context Protocol) server: lets Claude and other AI apps read the library.

``protocol.py`` is the message layer (transport-independent), ``tools.py`` the
library tools. ``mcp_server.py`` at the repository root runs it over stdio.
"""
from .protocol import Server, Tool, ToolError, serve_stdio
from .tools import build_server

__all__ = ["Server", "Tool", "ToolError", "serve_stdio", "build_server"]
