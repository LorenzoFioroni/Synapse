"""Export Formats: the library exports Synapse always had (BibTeX, RIS, EndNote tagged and XML,
CSL JSON), registered with Synapse's own writers from the public API (``backend.plugins.formats``),
so their output does not change. Their keys are ``export-formats.<format>``; requests naming a
format by its old key (``bibtex``) still reach these writers."""

from backend.plugins import Plugin, formats


def activate(plugin: Plugin) -> None:
    for writer in formats.writers():
        plugin.export_format(writer.key, title=writer.title, extension=writer.extension, mimetype=writer.mimetype)(
            lambda items, options, writer=writer: writer.dump(items, **options)
        )
