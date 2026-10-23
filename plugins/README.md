# Plugins that come with Synapse

Each folder here is a plugin (see [docs/plugins.md](../docs/plugins.md)). They are listed in
Settings → Plugins and stay off until turned on. A plugin with the same id in `data/plugins/`
replaces the one here.

- [`citation-counts`](citation-counts): how often each paper is cited (OpenAlex, Semantic
  Scholar), as a column, a search filter and a row in the item pane.
- [`arxiv-today`](arxiv-today): each day's arXiv papers in your categories, ranked by how they
  connect to your library, with a + to add one.

Examples to copy are in [docs/examples](../docs/examples).

`tasks/` and `metadata-sources/` are core plugins (on by default): the optional Tasks interface
and the online metadata providers. Their records and data integrity remain in the core.
