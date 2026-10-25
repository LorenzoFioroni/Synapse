"""Register the existing writers without changing their output or import parsers."""

from backend import formats
from backend.plugins import Plugin


def activate(plugin: Plugin) -> None:
    for key, fmt in formats.FORMATS.items():
        plugin.export_format(key, title=fmt.label, extension=fmt.extension, mimetype=fmt.mimetype)(
            lambda items, options, writer=fmt.dump: writer(items, **options)
        )
