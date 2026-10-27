// Server declarations name plugin providers; legacy labels also work with an older server.
const LEGACY: Record<string, string> = {
  semanticscholar: 'Semantic Scholar', openalex: 'OpenAlex', crossref: 'Crossref',
  'arxiv-html': 'arXiv (HTML version)', none: 'no service',
};

export function referenceSourceTitle(fetch: { source: string; source_title?: string }): string {
  return fetch.source_title || LEGACY[fetch.source] || fetch.source;
}
