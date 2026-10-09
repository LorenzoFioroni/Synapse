// CodeMirror 6 set-up for notes (source mode). The library is loaded on first use.
// Colours come from the app's CSS variables, so light and dark themes just work.

let CM = null;
export async function loadCM() {
  CM ||= await import('../../../vendor/cm6/cm6.js');
  return CM;
}

/**
 * Front-matter block for the Markdown parser: a `---` line at the very start of the note up to
 * the next `---` (or `...`) line. Same rule as the backend (frontmatter.split): if it is never
 * closed, it is not front-matter.
 */
function frontmatter(cm) {
  return {
    defineNodes: [{ name: 'Frontmatter', block: true }],
    parseBlock: [{
      name: 'Frontmatter',
      before: 'HorizontalRule',
      parse(cx, line) {
        if (cx.lineStart !== 0 || line.text !== '---') return false;
        const doc = cx.input.read(0, cx.input.length);
        const m = /\n(---|\.\.\.)(\n|$)/.exec(doc.slice(3));
        if (!m) return false;
        const end = 3 + m.index + 1 + m[1].length;
        while (cx.lineStart + line.text.length < end && cx.nextLine()) { /* consume */ }
        cx.addElement(cx.elt('Frontmatter', 0, end));
        cx.nextLine();
        return true;
      },
    }],
    props: [cm.styleTags({ Frontmatter: cm.tags.meta })],
  };
}

function highlightStyle(cm) {
  const t = cm.tags;
  return cm.HighlightStyle.define([
    { tag: t.heading1, class: 'cmt-h cmt-h1' },
    { tag: t.heading2, class: 'cmt-h cmt-h2' },
    { tag: t.heading3, class: 'cmt-h cmt-h3' },
    { tag: [t.heading4, t.heading5, t.heading6], class: 'cmt-h cmt-h4' },
    { tag: t.strong, class: 'cmt-strong' },
    { tag: t.emphasis, class: 'cmt-em' },
    { tag: t.strikethrough, class: 'cmt-strike' },
    { tag: [t.link, t.url], class: 'cmt-link' },
    { tag: t.monospace, class: 'cmt-code' },
    { tag: t.quote, class: 'cmt-quote' },
    { tag: t.meta, class: 'cmt-meta' },
    { tag: [t.processingInstruction, t.contentSeparator], class: 'cmt-mark' },
    { tag: t.list, class: 'cmt-list' },
    // inside fenced code blocks
    { tag: [t.keyword, t.operatorKeyword, t.modifier], class: 'cmt-kw' },
    { tag: [t.string, t.special(t.string), t.regexp], class: 'cmt-str' },
    { tag: [t.number, t.bool, t.null, t.atom], class: 'cmt-num' },
    { tag: [t.comment, t.lineComment, t.blockComment], class: 'cmt-comment' },
    { tag: [t.function(t.variableName), t.function(t.propertyName)], class: 'cmt-fn' },
    { tag: [t.typeName, t.className, t.namespace], class: 'cmt-type' },
    { tag: [t.propertyName, t.attributeName], class: 'cmt-prop' },
  ]);
}

/**
 * @param {HTMLElement} parent
 * @param {{onChange(text), onSave(), onBlur(), onImage(file): Promise<string|null>}} handlers
 */
export async function createEditor(parent, handlers) {
  const cm = await loadCM();
  const saveKey = { key: 'Mod-s', preventDefault: true, run: () => { handlers.onSave?.(); return true; } };

  const insertImages = async (view, files, pos) => {
    for (const f of files) {
      const md = await handlers.onImage?.(f);
      if (!md) continue;
      const at = pos ?? view.state.selection.main.head;
      const before = at > 0 && view.state.sliceDoc(at - 1, at) !== '\n' ? '\n' : '';
      const text = `${before}${md}\n`;
      view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length } });
      pos = at + text.length;
    }
  };
  const imagesOf = (dt) => [...(dt?.files || [])].filter((f) => f.type.startsWith('image/'));

  const extensions = () => [
    cm.history(),
    cm.drawSelection(),
    cm.dropCursor(),
    cm.highlightSpecialChars(),
    cm.EditorView.lineWrapping,
    cm.indentOnInput(),
    cm.bracketMatching(),
    cm.closeBrackets(),
    cm.highlightSelectionMatches(),
    cm.search({ top: true }),
    cm.markdown({ base: cm.markdownLanguage, codeLanguages: cm.languages, extensions: [frontmatter(cm)] }),
    cm.syntaxHighlighting(highlightStyle(cm)),
    cm.EditorView.contentAttributes.of({ spellcheck: 'true', autocorrect: 'on', autocapitalize: 'sentences' }),
    cm.keymap.of([saveKey, ...cm.closeBracketsKeymap, ...cm.markdownKeymap, ...cm.defaultKeymap,
      ...cm.searchKeymap, ...cm.historyKeymap, cm.indentWithTab]),
    cm.EditorView.updateListener.of((u) => {
      if (u.docChanged) handlers.onChange?.(u.state.doc.toString());
      if (u.focusChanged && !u.view.hasFocus) handlers.onBlur?.();
    }),
    cm.EditorView.domEventHandlers({
      paste(e, view) {
        const files = imagesOf(e.clipboardData);
        if (!files.length) return false;
        e.preventDefault();
        insertImages(view, files);
        return true;
      },
      drop(e, view) {
        const files = imagesOf(e.dataTransfer);
        if (!files.length) return false;
        e.preventDefault();
        const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
        insertImages(view, files, pos ?? undefined);
        return true;
      },
    }),
  ];

  const view = new cm.EditorView({ parent, state: cm.EditorState.create({ doc: '', extensions: extensions() }) });

  return {
    view,
    /** Load a different note: fresh state, so undo history never crosses notes. */
    load(text) {
      view.setState(cm.EditorState.create({ doc: text, extensions: extensions() }));
    },
    /** Replace the content of the current note (e.g. taken from another tab), keeping the cursor nearby. */
    replace(text) {
      const cur = view.state.doc.toString();
      if (cur === text) return;
      const head = Math.min(view.state.selection.main.head, text.length);
      view.dispatch({ changes: { from: 0, to: cur.length, insert: text }, selection: { anchor: head } });
    },
    text: () => view.state.doc.toString(),
    focus: () => view.focus(),
    gotoLine(n) {
      const line = view.state.doc.line(Math.max(1, Math.min(n, view.state.doc.lines)));
      view.dispatch({ selection: { anchor: line.from }, effects: cm.EditorView.scrollIntoView(line.from, { y: 'start', yMargin: 40 }) });
      view.focus();
    },
    destroy: () => view.destroy(),
  };
}
