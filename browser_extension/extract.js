// Runs inside the web page (injected by the popup with chrome.scripting).
// Each function must be fully self-contained: it is serialised and executed in the page.
//
// Reads, in order of preference: Highwire/Google Scholar <meta name="citation_*">
// tags (used by almost every publisher, arXiv, bioRxiv, …), Dublin Core / PRISM
// tags, JSON-LD ScholarlyArticle data, and finally the URL and Open Graph tags.

/**
 * What the page says about the work, in the fields the library's connector reads
 * (backend/api/connector.py).
 * @typedef {object} Citation
 * @property {string} page_url
 * @property {string} site
 * @property {string} [title]
 * @property {string[]} [authors]
 * @property {string} [year]
 * @property {string} [journal]
 * @property {string} [volume]
 * @property {string} [issue]
 * @property {string} [pages]
 * @property {string} [publisher]
 * @property {string} [abstract]
 * @property {string} [issn]
 * @property {string} [isbn]
 * @property {string} [language]
 * @property {string} [pdf_url]
 * @property {string} [doi]
 * @property {string} [arxiv]
 * @property {string} [item_type]
 * @property {boolean} [is_pdf]            a PDF opened directly in the browser
 * @property {boolean} [has_citation_meta]  the page has citation_* tags
 */

/** @returns {Citation} */
export function extractCitation() {
  /** @param {string} name */
  const metas = (name) => [...document.querySelectorAll(`meta[name="${name}" i], meta[property="${name}" i]`)]
    .map((m) => (m.getAttribute('content') || '').trim()).filter(Boolean);
  /** @param {...string} names */
  const meta = (...names) => { for (const n of names) { const v = metas(n)[0]; if (v) return v; } return ''; };
  /** @param {unknown} s */
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const DOI_RE = /\b(10\.\d{4,9}\/[^\s"'<>?#&]+)/i;
  /** @param {unknown} s */
  const cleanDoi = (s) => {
    const m = DOI_RE.exec(decodeURIComponent(String(s || '')));
    return m ? m[1].replace(/[.,;)\]}]+$/, '') : '';
  };
  const url = location.href;
  /** @type {Citation} */
  const out = { page_url: url, site: location.hostname.replace(/^www\./, '') };

  // A PDF opened directly in the browser
  if (document.contentType === 'application/pdf' || /\.pdf($|\?)/i.test(location.pathname) && document.querySelector('embed[type="application/pdf"]')) {
    out.is_pdf = true;
    out.pdf_url = url;
    out.title = clean(document.title) || decodeURIComponent(location.pathname.split('/').pop() || '');
    const arx = /arxiv\.org\/pdf\/([^/?#]+?)(v\d+)?(\.pdf)?$/i.exec(url);
    if (arx) { out.arxiv = arx[1]; out.item_type = 'preprint'; }
    out.doi = cleanDoi(url);
    return out;
  }

  // ---- citation_* (Highwire / Google Scholar) and friends
  out.title = clean(meta('citation_title', 'dc.title', 'DC.title', 'prism.title', 'og:title', 'twitter:title')) || clean(document.title);
  let authors = metas('citation_author');
  if (!authors.length) authors = metas('dc.creator').concat(metas('DC.creator'));
  if (!authors.length) authors = metas('citation_authors').flatMap((a) => a.split(';'));
  out.authors = authors.map(clean).filter(Boolean);
  out.year = meta('citation_publication_date', 'citation_date', 'citation_online_date', 'citation_year', 'dc.date', 'DC.date',
    'prism.publicationDate', 'article:published_time');
  out.journal = clean(meta('citation_journal_title', 'citation_conference_title', 'citation_book_title', 'prism.publicationName',
    'citation_inbook_title', 'og:site_name'));
  out.volume = meta('citation_volume', 'prism.volume');
  out.issue = meta('citation_issue', 'prism.number');
  const fp = meta('citation_firstpage', 'prism.startingPage'), lp = meta('citation_lastpage', 'prism.endingPage');
  out.pages = fp && lp && fp !== lp ? `${fp}-${lp}` : fp;
  out.publisher = clean(meta('citation_publisher', 'dc.publisher', 'DC.publisher', 'citation_technical_report_institution'));
  out.abstract = clean(meta('citation_abstract', 'dc.description', 'DC.description', 'og:description', 'description'));
  out.issn = meta('citation_issn', 'prism.issn');
  out.isbn = meta('citation_isbn');
  out.language = meta('citation_language', 'dc.language', 'DC.language');
  out.pdf_url = meta('citation_pdf_url');
  out.doi = cleanDoi(meta('citation_doi', 'dc.identifier', 'DC.identifier', 'prism.doi', 'bepress_citation_doi'))
    || [.../** @type {NodeListOf<HTMLMetaElement>} */ (document.querySelectorAll('meta[scheme="doi" i], meta[name="dc.identifier" i]'))].map((m) => cleanDoi(m.content)).find(Boolean)
    || cleanDoi(/** @type {HTMLLinkElement | null} */ (document.querySelector('link[rel="canonical"]'))?.href)
    || cleanDoi(url);
  out.arxiv = (meta('citation_arxiv_id') || '').replace(/^arxiv:/i, '');

  // ---- JSON-LD (ScholarlyArticle, Article, Book…)
  try {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      /** @type {any} */
      let data = JSON.parse(s.textContent || '');
      if (data['@graph']) data = data['@graph'];
      for (const d of /** @type {any[]} */ ([]).concat(data)) {
        const t = /** @type {string[]} */ ([]).concat(d['@type'] || []).join(' ');
        if (!/Article|Book|Thesis|Report|Chapter|Dataset/i.test(t)) continue;
        out.title ||= clean(d.headline || d.name);
        if (!out.authors.length && d.author) out.authors = /** @type {any[]} */ ([]).concat(d.author).map((a) => clean(typeof a === 'string' ? a : a.name)).filter(Boolean);
        out.year ||= d.datePublished || '';
        out.journal ||= clean(d.isPartOf?.name || d.publisher?.name || '');
        out.doi ||= cleanDoi(d.identifier?.value || d.identifier || d.sameAs || '');
        out.abstract ||= clean(d.description || '');
        if (/Book/i.test(t)) out.item_type ||= 'book';
      }
    }
  } catch { /* malformed JSON-LD is common; ignore */ }

  // ---- site specifics
  const arx = /arxiv\.org\/(?:abs|pdf|html)\/([^/?#]+?)(v\d+)?(\.pdf)?(?:[?#]|$)/i.exec(url);
  if (arx) {
    out.arxiv = arx[1];
    out.pdf_url ||= `https://arxiv.org/pdf/${arx[1]}`;
    out.title = out.title.replace(/^\[[^\]]+\]\s*/, '');   // "[2205.03415] Title"
  }

  // ---- item type
  if (!out.item_type) {
    if (out.arxiv || /biorxiv|medrxiv|ssrn|preprint/i.test(out.site + ' ' + meta('citation_journal_title'))) out.item_type = 'preprint';
    else if (meta('citation_conference_title')) out.item_type = 'conferencePaper';
    else if (meta('citation_dissertation_institution')) out.item_type = 'thesis';
    else if (meta('citation_technical_report_institution')) out.item_type = 'report';
    else if (meta('citation_inbook_title', 'citation_book_title')) out.item_type = 'bookSection';
    else if (out.isbn && !meta('citation_journal_title')) out.item_type = 'book';
    else if (meta('citation_journal_title') || out.doi) out.item_type = 'journalArticle';
    else out.item_type = 'webpage';
  }
  if (out.pdf_url) { try { out.pdf_url = new URL(out.pdf_url, url).href; } catch { /* keep */ } }
  out.has_citation_meta = metas('citation_title').length > 0;
  return out;
}

/**
 * Fetch a PDF from inside the page (so the user's cookies / institutional login apply).
 * @param {string} pdfUrl
 * @param {number} maxBytes
 * @returns {Promise<{ b64?: string; size?: number; error?: string }>}
 */
export async function fetchPdfInPage(pdfUrl, maxBytes) {
  try {
    const r = await fetch(pdfUrl, { credentials: 'include' });
    if (!r.ok) return { error: `HTTP ${r.status}` };
    const buf = new Uint8Array(await r.arrayBuffer());
    if (buf.length > maxBytes) return { error: 'too large' };
    if (!(buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46)) return { error: 'not a PDF (probably a login page)' };
    let bin = '';
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    return { b64: btoa(bin), size: buf.length };
  } catch (e) {
    return { error: String((e instanceof Error && e.message) || e) };
  }
}
