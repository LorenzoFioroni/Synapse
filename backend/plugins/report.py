"""What Settings → Plugins shows about a plugin: who it is, whether it runs and why not, what it
may do, its settings, and what it offers while running."""

from __future__ import annotations

from . import build
from .loading import Active, Entry


def describe(entry: Entry, st: dict, act: Active | None, values: dict, safe_mode: bool, core: bool = False) -> dict:
    """``st``: its entry in plugins.json; ``act``: it running; ``values``: its settings."""
    pid = entry.id
    man = entry.manifest
    enabled = bool(st.get("enabled"))
    if entry.problem or not man:
        status, problem = "invalid", entry.problem
    elif man.compatible():
        status, problem = "incompatible", man.compatible()
    elif act:
        status, problem = "on", None
    elif st.get("failed"):
        status, problem = "failed", st["failed"]
    elif enabled and safe_mode and not core:
        status, problem = "safe-mode", "Synapse was started in safe mode"
    else:
        status, problem = ("on" if enabled else "off"), None
    out: dict = {
        "id": pid,
        "name": man.name if man else pid,
        "version": man.version if man else "",
        "description": man.description if man else "",
        "author": man.author if man else "",
        "source": entry.source,
        "folder": str(entry.folder),
        "replaces_bundled": entry.replaces_bundled,
        "core": core,
        "enabled": enabled,
        "running": bool(act),
        "status": status,
        "problem": problem,
        "failed_at": st.get("failed_at") if status == "failed" else None,
        "remote_mcp": bool(st.get("remote_mcp")),
    }
    if man:
        out.update(
            api=f"{man.api[0]}.{man.api[1]}",
            permissions=man.permissions(),
            parts=[p for p in ("backend", "frontend", "styles") if getattr(man, p)],
            frontend=build.BUNDLED if act and act.bundle else man.frontend,
            styles=man.styles,
            settings=[s.describe() for s in man.settings],
            values=values,
        )
    if act:
        out["started"] = act.started
        out["schedules"] = [
            {"name": sc.name, "rule": sc.cron.text if sc.cron else f"every {int(sc.every or 0)} s"}
            for sc in act.plugin.schedules
        ]
        out["jobs"] = len(act.runner.queue.pending()) if act.runner else 0
        out["tools"] = [t.name for t in act.plugin.tools]
        out["routes"] = [{"rule": r.rule, "methods": list(r.methods)} for r in act.plugin.routes]
        out["events"] = sorted(e for e, fns in act.plugin.listeners.items() if fns)
        out["fields"] = [d.describe() for d in act.plugin.fields.defs.values()]
        out["sources"] = [{"id": f"{pid}.{s.id}", "title": s.title} for s in act.plugin.sources.values()]
        out["export_formats"] = [{"id": f.id, "title": f.title} for f in act.plugin.export_formats.values()]
        out["reference_sources"] = [{"id": s.id, "title": s.title} for s in act.plugin.reference_sources.values()]
        out["metadata_sources"] = [
            {"id": s.id, "title": s.title, "kinds": sorted(s.kinds)} for s in act.plugin.metadata_sources.values()
        ]
        out["pdf_sources"] = [
            {"id": s.id, "title": s.title, "by_title": s.by_title} for s in act.plugin.pdf_sources.values()
        ]
    return out
