// Markdown → HTML for notes (read mode, tables and math in the live preview, later export).
// No DOM access here, so it is unit-tested with Node (tests/js/render.test.mjs).
//
// Rules follow the syntax spec in notes_plan.md §3 and the backend parser:
//  * raw HTML is NOT rendered (shown as text): notes may later be written by an AI, and the
//    app must never run a script that came from a note;
//  * indented code blocks are off; fenced code only;
//  * $…$ closes on the same line (Pandoc rules), $$…$$ inline or as a block;
//  * [[Note|alias]], ![[Note]], [@key, p. 4], [see @a; @b], > [!note] callouts, - [ ] tasks.

const CALLOUTS = {
  note: 'Note', tip: 'Tip', warning: 'Warning', question: 'Question', quote: 'Quote',
  info: 'Info', important: 'Important', caution: 'Caution', example: 'Example', todo: 'To do',
};
const KEY = '[A-Za-z0-9_][A-Za-z0-9_:.+\\-/]*';
const CITE_PART = new RegExp(`^((?:[\\s\\S]*?[\\s(])?)(-?)@(${KEY})(?:\\s*,\\s*([\\s\\S]*?))?\\s*$`, 'u');
const PAGE = /^(?:p|pp|page|pages|pg)\.?\s*(\d+)/i;
const strip = (s) => s.replace(/^\s+|\s+$/gu, '');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function parseCites(inner) {
  const out = [];
  for (const part of inner.split(';')) {
    const m = CITE_PART.exec(strip(part));
    if (!m) return null;
    const key = m[3].replace(/[.:/-]+$/, '');
    if (!key) return null;
    const loc = strip(m[4] || '');
    const pm = PAGE.exec(loc);
    out.push({ key, prefix: strip(m[1]), suppress: m[2] === '-', locator: loc, page: pm ? Number(pm[1]) : null });
  }
  return out;
}

export function splitWiki(inner) {
  inner = inner.replaceAll('\\|', '|');
  const bar = inner.indexOf('|');
  let target = bar >= 0 ? inner.slice(0, bar) : inner;
  const alias = bar >= 0 ? inner.slice(bar + 1) : '';
  const hash = target.indexOf('#');
  const heading = hash >= 0 ? target.slice(hash + 1) : '';
  if (hash >= 0) target = target.slice(0, hash);
  return { target: strip(target), heading: strip(heading), alias: strip(alias) };
}

export function slugHeading(text) {
  return String(text).toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-');
}

/** Image source for a path written in a note (vault-relative), or the URL as given. */
export function imageSrc(target) {
  if (/^(https?:)?\/\//i.test(target) || target.startsWith('data:image/')) return target;
  const clean = target.replace(/^\.?\//, '');
  if (clean.startsWith('attachments/')) return `/api/notes/${clean.split('/').map(encodeURIComponent).join('/')}`;
  return `/api/notes/attachments/${clean.split('/').map(encodeURIComponent).join('/')}`;
}

/** A short label for a citation: "Smith et al. 2021, p. 4" when the item is known. */
export function citeLabel(c, info) {
  let base = `@${c.key}`;
  if (info && (info.authors || info.year)) {
    const fams = String(info.authors || '').split(';').map((a) => a.split(',')[0].trim()).filter(Boolean);
    const who = fams.length === 0 ? '' : fams.length === 1 ? fams[0] : fams.length === 2 ? `${fams[0]} & ${fams[1]}` : `${fams[0]} et al.`;
    base = c.suppress ? String(info.year || '') : [who, info.year].filter(Boolean).join(' ');
    base ||= `@${c.key}`;
  }
  return [c.prefix, base].filter(Boolean).join(' ') + (c.locator ? `, ${c.locator}` : '');
}

/* ---------------------------------------------------------------- markdown-it rules */
function mathInline() {
  return (state, silent) => {
    const src = state.src, pos = state.pos, max = state.posMax;
    if (src[pos] !== '$') return false;
    if (src.startsWith('$$', pos)) {
      const close = src.indexOf('$$', pos + 2);
      if (close < 0 || close + 2 > max) return false;
      if (!silent) { const t = state.push('math_inline', 'math', 0); t.content = src.slice(pos + 2, close); t.markup = '$$'; t.meta = { display: true }; }
      state.pos = close + 2;
      return true;
    }
    if (pos + 1 >= max || ' \t\n$'.includes(src[pos + 1])) return false;
    const eol = src.indexOf('\n', pos);
    const lineEnd = eol < 0 || eol > max ? max : eol;
    let k = pos + 1;
    while (k < lineEnd) {
      if (src[k] === '\\') { k += 2; continue; }
      if (src[k] === '$' && !' \t\n'.includes(src[k - 1]) && !(k + 1 < src.length && '0123456789'.includes(src[k + 1]))) break;
      k++;
    }
    if (k >= lineEnd) return false;
    if (!silent) { const t = state.push('math_inline', 'math', 0); t.content = src.slice(pos + 1, k); t.markup = '$'; t.meta = { display: false }; }
    state.pos = k + 1;
    return true;
  };
}

function mathBlock(state, startLine, endLine, silent) {
  const start = state.bMarks[startLine] + state.tShift[startLine];
  const max = state.eMarks[startLine];
  const first = state.src.slice(start, max).trim();
  if (!first.startsWith('$$')) return false;
  const rest = first.slice(2).replace(/\s+$/, '');
  if (first !== '$$' && rest.endsWith('$$')) return false;   // one-line $$x$$: inline rule
  if (silent) return true;
  let line = startLine, content = [rest];
  let closed = false;
  while (++line < endLine) {
    const l = state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
    const t = l.trim();
    if (t.endsWith('$$')) { content.push(t.slice(0, -2)); closed = true; break; }
    content.push(l);
  }
  state.line = closed ? line + 1 : endLine;
  const tok = state.push('math_block', 'math', 0);
  tok.block = true;
  tok.content = content.join('\n').trim();
  tok.map = [startLine, state.line];
  tok.markup = '$$';
  return true;
}

function wikiRule(state, silent) {
  const src = state.src;
  let pos = state.pos;
  const embed = src[pos] === '!';
  if (embed) pos++;
  if (!src.startsWith('[[', pos)) return false;
  const close = src.indexOf(']]', pos + 2);
  if (close < 0 || close > state.posMax) return false;
  const inner = src.slice(pos + 2, close);
  if (!inner || /[[\]\n]/.test(inner)) return false;
  const w = splitWiki(inner);
  if (!w.target && !w.heading) return false;
  if (!silent) {
    const t = state.push(embed ? 'note_embed' : 'note_link', '', 0);
    t.meta = w;
    t.content = inner;
  }
  state.pos = close + 2;
  return true;
}

function citeRule(state, silent) {
  const src = state.src, pos = state.pos;
  if (src[pos] !== '[' || src[pos + 1] === '[') return false;
  if (pos > 0 && '![]\\'.includes(src[pos - 1])) return false;
  let end = pos + 1;
  while (end < state.posMax && !'[]\n'.includes(src[end])) end++;
  if (src[end] !== ']') return false;
  const inner = src.slice(pos + 1, end);
  if (!inner.includes('@')) return false;
  const next = src[end + 1];
  if (next && '([]:'.includes(next)) return false;
  const cites = parseCites(inner);
  if (!cites) return false;
  if (!silent) {
    const t = state.push('cite_group', '', 0);
    t.meta = cites;
    t.content = inner;
  }
  state.pos = end + 1;
  return true;
}

const TAG_START = /[A-Za-z]/;
const TAG_BODY = /^[\p{L}\p{N}_\-/]*/u;
const NOT_BEFORE_TAG = /[\p{L}\p{N}_/&#\])([]/u;

/** #tag (same rule as the backend: not after a word character, a slash, a bracket…). */
function tagRule(state, silent) {
  const src = state.src, pos = state.pos;
  if (src[pos] !== '#' || !TAG_START.test(src[pos + 1] || '')) return false;
  if (pos > 0 && NOT_BEFORE_TAG.test(src[pos - 1])) return false;
  const m = TAG_BODY.exec(src.slice(pos + 2, state.posMax));
  const tag = (src[pos + 1] + m[0]).replace(/[/-]+$/, '');
  if (!silent) { const t = state.push('tag', '', 0); t.content = tag; }
  state.pos = pos + 1 + tag.length;
  return true;
}

/** > [!type] Title  →  a callout. Runs after block parsing. */
function calloutRule(state) {
  const toks = state.tokens;
  for (let i = 0; i < toks.length; i++) {
    if (toks[i].type !== 'blockquote_open') continue;
    const p = toks[i + 1], inl = toks[i + 2];
    if (!p || p.type !== 'paragraph_open' || !inl || inl.type !== 'inline') continue;
    const m = /^\[!([A-Za-z]+)\]([+-]?)[ \t]*(.*)$/m.exec(inl.content.split('\n')[0]);
    if (!m) continue;
    const type = m[1].toLowerCase();
    toks[i].attrJoin('class', `callout callout-${CALLOUTS[type] ? type : 'note'}`);
    toks[i].meta = { callout: type, title: m[3] || CALLOUTS[type] || m[1] };
    const lines = inl.content.split('\n');
    lines.shift();
    inl.content = lines.join('\n');   // the inline rule parses it later
    if (!inl.content.trim()) { p.hidden = true; toks[i + 3].hidden = true; inl.hidden = true; }
  }
}

/** - [ ] / - [x] at the start of a list item → checkbox. */
function taskRule(state) {
  const toks = state.tokens;
  for (let i = 2; i < toks.length; i++) {
    const inl = toks[i];
    if (inl.type !== 'inline' || toks[i - 1].type !== 'paragraph_open' || toks[i - 2].type !== 'list_item_open') continue;
    const m = /^\[([ xX])\][ \t]/.exec(inl.content);
    if (!m) continue;
    const first = inl.children[0];
    if (!first || first.type !== 'text' || !/^\[[ xX]\][ \t]/.test(first.content)) continue;
    first.content = first.content.slice(4);
    const box = new state.Token('task_box', '', 0);
    box.meta = { checked: m[1] !== ' ', line: toks[i - 2].map ? toks[i - 2].map[0] : null };
    inl.children.unshift(box);
    toks[i - 2].attrJoin('class', 'task');
  }
}

/** data-line on block tokens, so a click in read mode can find the source line. */
function lineAttrs(state) {
  for (const t of state.tokens) {
    if (t.map && t.nesting >= 0 && t.block && t.type !== 'inline') t.attrSet('data-line', String(t.map[0]));
  }
}

/**
 * @param {{MarkdownIt, footnote, katex}} lib       from vendor/md/md.js
 * @param {object} [o]
 * @param {(key) => object|null} [o.citeInfo]        library info for a key (or null if unknown)
 * @param {(target) => string|null} [o.resolveNote]  path a [[target]] resolves to, or null
 * @param {number} [o.lineOffset=0]                  added to data-line (front-matter lines)
 */
export function createRenderer(lib, o = {}) {
  const md = new lib.MarkdownIt({ html: false, linkify: true, typographer: false });
  md.disable('code');
  md.use(lib.footnote);
  md.block.ruler.before('fence', 'math_block', mathBlock, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
  md.inline.ruler.before('escape', 'math_inline', mathInline());
  md.inline.ruler.before('link', 'wiki', wikiRule);
  md.inline.ruler.before('image', 'wiki_embed', wikiRule);
  md.inline.ruler.before('link', 'cite', citeRule);
  md.inline.ruler.push('tag', tagRule);
  md.core.ruler.after('block', 'callout', calloutRule);
  md.core.ruler.after('inline', 'task', taskRule);
  md.core.ruler.push('line_attrs', lineAttrs);

  const katex = (tex, display) => {
    try {
      return lib.katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: 'ignore', trust: false, output: 'htmlAndMathml' });
    } catch (e) {
      return `<span class="math-error" title="${esc(e.message)}">${esc(tex)}</span>`;
    }
  };
  const R = md.renderer.rules;
  R.math_inline = (t, i) => (t[i].meta.display ? `<span class="math display">${katex(t[i].content, true)}</span>` : `<span class="math">${katex(t[i].content, false)}</span>`);
  R.math_block = (t, i) => `<div class="math-block" data-line="${(t[i].map?.[0] ?? 0) + (o.lineOffset || 0)}">${katex(t[i].content, true)}</div>\n`;
  R.note_link = (t, i) => {
    const w = t[i].meta;
    const path = o.resolveNote ? o.resolveNote(w.target) : undefined;   // undefined = unknown, null = does not exist
    const label = w.alias || (w.target ? w.target + (w.heading ? ` › ${w.heading}` : '') : w.heading);
    return `<a class="wikilink${path === null && w.target ? ' unresolved' : ''}" data-note="${esc(w.target)}" data-heading="${esc(w.heading)}"${path ? ` data-path="${esc(path)}"` : ''} href="#">${esc(label)}</a>`;
  };
  R.note_embed = (t, i) => {
    const w = t[i].meta;
    const path = o.resolveNote ? o.resolveNote(w.target) : undefined;
    return `<div class="note-embed${path === null ? ' unresolved' : ''}" data-note="${esc(w.target)}" data-heading="${esc(w.heading)}"${path ? ` data-path="${esc(path)}"` : ''}><div class="embed-title">${esc(w.alias || w.target)}</div><div class="embed-body"></div></div>`;
  };
  R.cite_group = (t, i) => {
    const parts = t[i].meta.map((c) => {
      const info = o.citeInfo ? o.citeInfo(c.key) : null;
      const cls = ['cite', info ? '' : 'missing', info?.trashed ? 'trashed' : ''].filter(Boolean).join(' ');
      return `<a class="${cls}" href="#" data-key="${esc(c.key)}"${c.page ? ` data-page="${c.page}"` : ''} title="${esc(info ? `${info.title || ''}` : `@${c.key} is not in your library`)}">${esc(citeLabel(c, info))}</a>`;
    });
    return `<span class="cite-group">(${parts.join('; ')})</span>`;
  };
  R.tag = (t, i) => `<span class="tag">#${esc(t[i].content)}</span>`;
  R.task_box = (t, i) => `<input type="checkbox" class="task-box"${t[i].meta.checked ? ' checked' : ''}${t[i].meta.line != null ? ` data-task-line="${t[i].meta.line + (o.lineOffset || 0)}"` : ''}>`;
  const defaultImage = R.image;
  R.image = (t, i, opts, env, self) => {
    const tok = t[i];
    const alt = tok.children ? self.renderInlineAsText(tok.children, opts, env) : tok.content;
    const [label, width] = alt.split('|');
    tok.attrSet('src', imageSrc(tok.attrGet('src') || ''));
    tok.attrSet('loading', 'lazy');
    if (width && /^\d+$/.test(width.trim())) tok.attrSet('width', width.trim());
    if (tok.children) tok.children = [{ type: 'text', content: label.trim() }];
    tok.content = label.trim();
    return defaultImage(t, i, opts, env, self);
  };
  const defaultFence = R.fence;
  R.fence = (t, i, opts, env, self) => {
    const tok = t[i];
    const lang = (tok.info || '').trim().split(/\s+/)[0].toLowerCase();
    const line = (tok.map?.[0] ?? 0) + (o.lineOffset || 0);
    if (lang === 'mermaid') return `<div class="mermaid-block" data-line="${line}"><pre class="mermaid-src">${esc(tok.content)}</pre></div>\n`;
    const html = defaultFence(t, i, opts, env, self);
    return `<div class="code-block" data-line="${line}"${lang ? ` data-lang="${esc(lang)}"` : ''}><button class="copy-code" title="Copy" type="button">Copy</button>${html}</div>\n`;
  };
  const defaultBQ = R.blockquote_open || ((t, i, opts, env, self) => self.renderToken(t, i, opts));
  R.blockquote_open = (t, i, opts, env, self) => {
    const m = t[i].meta;
    const open = defaultBQ(t, i, opts, env, self);
    return m?.callout ? `${open}<div class="callout-title">${esc(m.title)}</div>\n` : open;
  };
  R.heading_open = (t, i, opts, env, self) => {
    const text = t[i + 1]?.content || '';
    t[i].attrSet('id', `h-${slugHeading(text)}`);
    return self.renderToken(t, i, opts);
  };
  // data-line values include the front-matter offset
  if (o.lineOffset) {
    md.core.ruler.push('line_offset', (state) => {
      for (const t of state.tokens) { const v = t.attrGet && t.attrGet('data-line'); if (v != null) t.attrSet('data-line', String(Number(v) + o.lineOffset)); }
    });
  }
  return {
    md,
    render: (src) => md.render(src),
    renderInline: (src) => md.renderInline(src),
    katex,
  };
}
