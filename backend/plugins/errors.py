"""The error type plugins raise for problems to show as they are."""

from __future__ import annotations


class PluginError(Exception):
    """A problem to show as it is: to the user (as the error of a route) or to Claude (as the
    result of a tool). It does not count as the plugin failing."""

    readable = True  # shown as it is (the MCP layer checks this, without importing plugins)
