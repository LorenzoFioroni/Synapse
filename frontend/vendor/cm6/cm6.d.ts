// Types of cm6.js: the same re-exports as tools/cm6/entry.js (copied by tools/cm6/build.sh). The types
// come from the @codemirror packages installed in frontend/ for type checking, at the same versions.
export { EditorState, StateEffect, StateField, Compartment, RangeSetBuilder, Transaction, Annotation, EditorSelection } from '@codemirror/state';
export { EditorView, keymap, drawSelection, highlightActiveLine, dropCursor, rectangularSelection, crosshairCursor,
  highlightSpecialChars, placeholder, Decoration, ViewPlugin, WidgetType, hoverTooltip, showTooltip } from '@codemirror/view';
export { defaultKeymap, history, historyKeymap, indentWithTab, undo, redo } from '@codemirror/commands';
export { syntaxHighlighting, HighlightStyle, defaultHighlightStyle, indentOnInput, bracketMatching, foldGutter,
  foldKeymap, syntaxTree, LanguageDescription } from '@codemirror/language';
export { markdown, markdownLanguage, markdownKeymap } from '@codemirror/lang-markdown';
export { languages } from '@codemirror/language-data';
export { search, searchKeymap, highlightSelectionMatches, openSearchPanel } from '@codemirror/search';
export { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap, startCompletion, acceptCompletion } from '@codemirror/autocomplete';
export { tags, styleTags, highlightCode } from '@lezer/highlight';
