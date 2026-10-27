"""Discover plugin manifests and initialize the defaults of the plugins that come with Synapse,
including legacy preferences.

Discovery never imports plugin code. Only manifests bundled with Synapse may claim core status or
start on by themselves.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .loading import Entry
from .manifest import ManifestError, read
from .state import StateFile

log = logging.getLogger(__name__)


def discover(config) -> dict[str, Entry]:
    roots = [(config.bundled_plugins_dir, "bundled"), (config.plugins_dir, "user")]
    dev = getattr(config, "dev_plugins_dir", None)
    if dev:
        roots.append((Path(dev), "dev"))
    found: dict[str, Entry] = {}
    for root, source in roots:
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
            entry = Entry(folder.name, folder, source)
            try:
                entry.manifest = read(folder)
            except ManifestError as e:
                entry.problem = str(e)
            if source in ("user", "dev") and folder.name in found:
                entry.replaces_bundled = True
            found[folder.name] = entry
    return found


def starts_by_itself(entry: Entry) -> bool:
    """A plugin that comes with Synapse and decides on its own whether it starts the first time:
    a core plugin, or a contributed one whose manifest says it starts on (Open-Access PDFs,
    Export Formats: features Synapse always had). An installed plugin always waits for the user."""
    man = entry.manifest
    return bool(
        entry.source == "bundled"
        and man
        and (man.core or man.enabled_by_default or man.enabled_from_setting)
    )


def initialize_defaults(entries: dict[str, Entry], state: StateFile, settings) -> None:
    """Turn on (or not) the plugins that come with Synapse the first time they are seen, carrying
    legacy feature settings over once; preserve choices already made in Plugins."""
    legacy = getattr(settings, "legacy", lambda key: None)
    # Initialization and a settings request in the other process must not overwrite each other.
    from .files import transaction

    with transaction(state.path):
        states = state.read()
        for pid, entry in entries.items():
            man = entry.manifest
            if not starts_by_itself(entry) or not man or "enabled" in states.get(pid, {}):
                continue
            on = man.enabled_by_default
            if man.enabled_from_setting and legacy(man.enabled_from_setting) is not None:
                on = bool(legacy(man.enabled_from_setting))
            values: dict = {}
            for setting in man.settings:
                old = legacy(setting.from_setting) if setting.from_setting else None
                if old is not None:
                    try:
                        values[setting.key] = setting.clean(old)
                    except ManifestError:
                        pass
            changes: dict = {"enabled": on}
            if man.mcp and man.core:
                changes["remote_mcp"] = True
            if values:
                changes["settings"] = {**(states.get(pid, {}).get("settings") or {}), **values}
            state.update(pid, **changes)
            log.info("Plugin %s: %s", pid, "on" if on else "off")
