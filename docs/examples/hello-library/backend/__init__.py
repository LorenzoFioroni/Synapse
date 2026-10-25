"""Hello Library: the minimal example used by docs/plugin-authoring.md."""

from backend.plugins import Plugin


def activate(plugin: Plugin) -> None:
    @plugin.route("/message")
    def message():
        return {"message": plugin.settings["message"]}
