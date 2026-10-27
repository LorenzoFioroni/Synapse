# Plugins that come with Synapse

Each folder here is a plugin (see [docs/plugins.md](../docs/plugins.md)). They are listed in
Settings → Plugins. Core plugins use their declared defaults; other plugins stay off until turned on. A plugin with the same id in `data/plugins/`
replaces the one here.

- [`citation-counts`](citation-counts): how often each paper is cited (OpenAlex, Semantic
  Scholar), as a column, a search filter and a row in the item pane.
- [`arxiv-today`](arxiv-today): each day's arXiv papers in your categories, ranked by how they
  connect to your library, with a + to add one.

Examples to copy are in [docs/examples](../docs/examples).

None of the plugins here is a core plugin any more: they all use only the public plugin API, so
they double as worked examples. These start on by themselves the first time (`enabled_by_default`)
and can be turned off without rewriting user data:

- `tasks/`: the Tasks interface (task records and note integrity stay in Synapse's core).
- `metadata-sources/` and `reference-sources/`: the online metadata and reference providers.
- `extended-markdown/`: galleries and numbered figures, inline and collapsible spoilers.
- `graph/`: the notes and citation graph.
- [`open-access-pdfs`](open-access-pdfs): where Find Available PDF looks (a PDF source).
- [`export-formats`](export-formats): the library exports (export writers).

`metadata-check/` and `autocompletion/` start off (Autocompletion follows the old
`notes_ai_complete` setting when it was set).
