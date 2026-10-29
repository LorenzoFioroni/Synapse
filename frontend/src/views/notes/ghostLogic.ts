// Autocompletion's ghost text, the parts that need no editor: where a suggestion may appear, what
// is sent around the cursor, what is left of a suggestion as you type it, and the next word of it.
// The CodeMirror extension is ghost.ts.

/** A suggestion shown at ``pos`` (it is not in the document until accepted). */
export interface Ghost { pos: number; text: string }

/** Characters sent before and after the cursor (the server uses the lines nearest to it). */
export const BEFORE = 6000;
export const AFTER = 2000;

/** What may follow the cursor on its line for a suggestion to appear: nothing, or closing marks
 *  ("$" of inline math, a bracket, the end of emphasis …), followed by punctuation. */
const REST_OK = /^[\s)\]}$*_`'"+~.,;:!?…]*$/;

/** May a suggestion appear with ``after`` following the cursor on its line? ``prefix`` is
 *  everything before the cursor: unless asked for (``explicit``), it needs some text to go on. */
export function canSuggestAt(after: string, prefix: string, explicit = false): boolean {
  if (!REST_OK.test(after)) return false;
  return explicit || prefix.replace(/\s+/g, '').length >= 3;
}

/** The text sent for the cursor at ``pos`` of ``doc``. */
export function contextAround(doc: string, pos: number): { prefix: string; suffix: string } {
  return { prefix: doc.slice(Math.max(0, pos - BEFORE), pos), suffix: doc.slice(pos, pos + AFTER) };
}

/** After the change ``from``–``to`` → ``inserted``: what is left of ``ghost`` when the change
 *  typed its beginning, else null (the suggestion goes away). */
export function consumeTyped(ghost: Ghost, from: number, to: number, inserted: string): Ghost | null {
  if (from !== ghost.pos || to !== ghost.pos || !inserted) return null;
  if (!ghost.text.startsWith(inserted) || inserted.length >= ghost.text.length) return null;
  return { pos: ghost.pos + inserted.length, text: ghost.text.slice(inserted.length) };
}

/** The next word of a suggestion (for accepting it word by word): spaces before it, then a
 *  word, a LaTeX command, or one other character. */
export function nextWord(text: string): string {
  const m = /^\s*(?:\\?[\p{L}\p{N}]+|\S)/u.exec(text);
  return m ? m[0] : text;
}
