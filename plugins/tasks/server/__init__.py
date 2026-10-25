"""Tasks: the optional interface over Synapse's task records and note links."""

from backend.plugins import Plugin

from . import registration, routes


def activate(plugin: Plugin) -> None:
    routes.register(plugin)
    registration.register(plugin)
