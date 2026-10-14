"""MCP (Model Context Protocol) server: lets Claude and other AI apps read the library.

``protocol.py`` is the message layer (transport-independent), ``tools/`` the library tools,
``server.py`` registers them with their schemas and ``instructions.py`` holds what the AI app is
told about using them. ``mcp_server.py`` at the repository root runs it over stdio.
"""

from .protocol import Server, Tool, ToolError, serve_stdio
from .server import build_server

__all__ = ["Server", "Tool", "ToolError", "serve_stdio", "build_server"]
