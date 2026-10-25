"""``python server.py --new-plugin <id>``: a working plugin to start from, with a Python backend
(a route, a tool for Claude, an event listener, a schedule) and a TypeScript frontend (a page and a
command), plus the frontend's type declarations and a README."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import APP_ROOT
from .errors import PluginError
from .manifest import API_VERSION, ID_RE

BACKEND = '''"""{name}: its Python part (docs/plugins.md)."""

from __future__ import annotations

from backend.plugins import Plugin


def activate(plugin: Plugin) -> None:
    seen = plugin.storage.json("state")

    @plugin.on("item.added")
    def count(event: dict) -> None:
        seen.set("added", seen.get("added", 0) + len(event["ids"]))

    @plugin.route("/hello")
    def hello():
        return {{"message": f"Hello from {{plugin.manifest.name}}", "added": seen.get("added", 0)}}

    @plugin.mcp_tool("Say how many items were added since {name} was turned on.")
    def added_count() -> dict:
        return {{"added": seen.get("added", 0)}}

    @plugin.schedule("daily", cron="0 9 * * *")
    def daily() -> None:
        plugin.log.info("Good morning: %s items added so far", seen.get("added", 0))
'''

FRONTEND = """// {name}: its part in the app (docs/plugins.md). Written in TypeScript: Synapse bundles it with
// esbuild when the plugin starts. The types come from ./api.ts (a copy of Synapse's plugin API).
import type {{ PluginContext }} from './api';

export function activate(p: PluginContext): void {{
  p.pages.register({{
    id: 'main',
    title: '{name}',
    icon: 'plugin',
    mount(el) {{
      el.innerHTML = '<p class="hello">Loading…</p>';
      p.backend.get<{{ message: string; added: number }}>('/hello').then((r) => {{
        el.innerHTML = `<p class="hello">${{p.ui.escapeHtml(r.message)}}: ${{r.added}} items added.</p>`;
      }});
    }},
  }});
  p.commands.register({{ id: 'open', title: 'Open {name}', run: () => p.pages.open('main') }});
}}
"""

STYLE = """/* {name}: every rule starts with the plugin's scope and uses the app's colour tokens. */
[data-plugin="{id}"] .hello {{ color: var(--text-2); }}
"""

README = """# {name}

A Synapse plugin. While writing it, run Synapse with this folder's parent as the dev plugins folder,
so each saved change restarts it:

    python server.py --dev-plugins {parent}

Then turn it on in Settings → Plugins. `backend/` is its Python part, `frontend/main.ts` its part in
the app (bundled with esbuild when it starts), `frontend/api.ts` the types of Synapse's plugin API.
The guide is docs/plugins.md in Synapse's repository.
"""


def create(pid: str, parent: Path, name: str | None = None) -> Path:
    """A new plugin ``pid`` in ``parent``; refuses to overwrite anything."""
    if not ID_RE.match(pid):
        raise PluginError("The id: 2–64 lowercase letters, digits and -, e.g. my-plugin")
    folder = parent / pid
    if folder.exists():
        raise PluginError(f"{folder} exists already")
    name = name or pid.replace("-", " ").title()
    (folder / "backend").mkdir(parents=True)
    (folder / "frontend").mkdir()
    manifest = {
        "id": pid,
        "name": name,
        "version": "0.1.0",
        "api": f"^{API_VERSION[0]}.{API_VERSION[1]}",
        "author": "",
        "description": "",
        "backend": "backend",
        "frontend": "frontend/main.ts",
        "styles": "frontend/style.css",
        "permissions": {"library": ["read"], "mcp": True},
        "settings": [],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    (folder / "backend" / "__init__.py").write_text(BACKEND.format(name=name), "utf-8")
    (folder / "frontend" / "main.ts").write_text(FRONTEND.format(name=name), "utf-8")
    (folder / "frontend" / "style.css").write_text(STYLE.format(name=name, id=pid), "utf-8")
    shutil.copyfile(APP_ROOT / "frontend" / "src" / "plugins" / "api.ts", folder / "frontend" / "api.ts")
    (folder / "README.md").write_text(README.format(name=name, parent=parent), "utf-8")
    return folder
