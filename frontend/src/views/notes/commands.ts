// The notes' commands for the command palette, for the note being edited: inserting images and
// figures (several at once make a gallery with Extended Markdown), citations, links, tasks and other blocks; Live/Source/Read; renaming,
// moving, deleting, exporting and linking the note; its graph, history and paper; the history of all notes. Each is offered
// only while a note is open on screen and it can do something there (a figure needs Extended
// Markdown's figures, a task the Tasks plugin), so the palette never lists one that would do nothing.
import { api } from '../../api.js';
import { commands } from '../../extend.js';
import { emit } from '../../store.js';
import { toast, toastError } from '../../ui/toast.js';
import { pickFiles } from '../itemlist/menus.js';
import { markMilestone, openNoteHistory, openVaultHistory } from './historyView.js';
import { pdfsOf } from './citeCard.js';
import { loadCM } from './editor.js';
import { exportNote } from './exportNote.js';
import { copyLinks, deleteSelection, pickFolder, renameNote } from './fileOps.js';
import { noteHooks } from './hooks.js';
import { imagesMarkdown, insertIntoNote, openNotesGraph, openPaperPdf, setNoteMode, showAttachments } from './notesView.js';
import { toggleDetails, toggleList } from './panels.js';
import { nKey, selectOnly } from './tree.js';
import { itemInfo, taskHooks, ws } from './workspace.js';

export interface NoteCommandsHost {
  /** Is the notes workspace on screen (not under Settings or a PDF tab)? */
  shown: () => boolean;
}

const IMAGES = 'image/png,image/jpeg,image/gif,image/webp';

export function registerNoteCommands(host: NoteCommandsHost): void {
  /** A note is open and on screen. */
  const open = () => host.shown() && !!ws.session && !!ws.editor;
  /** … and can be changed (not waiting for a conflict to be resolved). */
  const editable = () => open() && ws.session!.status !== 'conflict';
  const add = (id: string, title: string, run: () => unknown, when: () => boolean = open) =>
    commands.register({ id: `core.note.${id}`, title, when, run });

  // ---- inserting
  add('insert-image', 'Insert Image…', () => pickImages(false), editable);
  add('insert-figure', 'Insert Figure with Caption…', () => pickImages(true), () => editable() && !!noteHooks.figures);
  add('insert-attachment', 'Insert Image from Attachments…', () => showAttachments(), editable);
  add('insert-citation', 'Insert Citation…', () => typeAndComplete('[@', ']'), editable);
  add('insert-link', 'Insert Link to Note…', () => typeAndComplete('[[', ']]'), editable);
  add('new-task', 'Add Task to This Note…', () => newTask(), () => editable() && !!taskHooks.add);
  add('insert-task', 'Insert Existing Task…', () => typeAndComplete('{{task ', '}}'), () => editable() && !!taskHooks.choices);
  add('insert-equation', 'Insert Numbered Equation', () => insertBlock('$$\n\\label{eq:name}\n$$', '\\label{eq:name}', -1), editable);
  add('insert-table', 'Insert Table', () => insertBlock('| Column | Column |\n| --- | --- |\n|  |  |', 'Column'), editable);
  add('insert-callout', 'Insert Callout', () => insertBlock('> [!note] Title\n> Text', 'Title'), editable);
  add('insert-diagram', 'Insert Mermaid Diagram', () => insertBlock('```mermaid\nflowchart LR\n  A --> B\n```', 'A --> B'), editable);

  // ---- viewing
  add('live', 'Live Preview', () => setNoteMode('live'), () => open() && ws.mode !== 'live');
  add('source', 'Source Mode', () => setNoteMode('source'), () => open() && ws.mode !== 'source');
  add('read', 'Read Mode', () => setNoteMode('read'), () => open() && ws.mode !== 'read');
  add('graph', 'Show This Note in the Graph', () => openNotesGraph(`note:${ws.session!.path}`), () => open() && !!noteHooks.graph);
  add('history', 'History of This Note…', () => openNoteHistory());
  add('notes-history', 'Notes History…', () => openVaultHistory(), () => host.shown());
  add('milestone', 'Mark Notes Milestone…', () => markMilestone(), () => host.shown());
  add('paper', 'Show This Note’s Paper in the Library', () => { const it = itemInfo(ws.info!.paper!); if (it) emit('show-item', it.id); },
    () => open() && !!ws.info?.paper && !!itemInfo(ws.info.paper));
  add('paper-pdf', 'Open This Note’s Paper PDF', () => openPaperPdf(centre()),
    () => open() && !!ws.info?.paper && pdfsOf(itemInfo(ws.info.paper)).length > 0);
  add('toggle-list', 'Show or Hide the Notes List', () => toggleList(), () => host.shown());
  add('toggle-details', 'Show or Hide the Note Details', () => toggleDetails(), () => host.shown());

  // ---- the note itself
  add('rename', 'Rename This Note…', () => renameNote(ws.session!.path), editable);
  add('move', 'Move This Note to a Folder…', () => pickFolder(centre(), { notes: [ws.session!.path], folders: [] }), editable);
  add('copy-link', 'Copy Link to This Note', () => copyLinks([ws.session!.path]));
  add('delete', 'Delete This Note…', () => { selectOnly(nKey(ws.session!.path)); return deleteSelection(); }, editable);
  add('export-pdf', 'Export This Note as PDF', () => exportNote(ws.session!.path, 'pdf'));
  add('export-html', 'Export This Note as HTML', () => exportNote(ws.session!.path, 'html'));
  add('export-md', 'Export This Note as Markdown', () => exportNote(ws.session!.path, 'md'));
  add('attachments', 'Notes Attachments…', () => showAttachments(), () => host.shown());
}

/** Where a menu opened from the palette goes: the middle of the window, a third of the way down. */
const centre = () => ({ x: innerWidth / 2, y: innerHeight / 3 });

/** Come back to editing (from Read) so what is inserted is seen where the cursor is. */
function editing(): void {
  if (ws.mode === 'read') setNoteMode(ws.editMode);
}

/** Insert ``md`` after the cursor's line, then select ``select`` (the part to type over) in it;
 *  ``nth`` -1 takes its last occurrence in the inserted block. */
function insertBlock(md: string, select?: string, nth = 0): void {
  editing();
  if (!insertIntoNote(md)) return;
  const view = ws.editor!.view;
  if (select) {
    const end = view.state.selection.main.head;
    const start = view.state.doc.sliceString(0, end).lastIndexOf(md.replace(/\n+$/, ''));
    if (start >= 0) {
      const at = nth < 0 ? md.lastIndexOf(select) : md.indexOf(select);
      if (at >= 0) view.dispatch({ selection: { anchor: start + at, head: start + at + select.length } });
    }
  }
  ws.editor!.focus();
}

/** Type ``text`` at the cursor (replacing the selection), and ``close`` after it as typing the
 *  bracket would, then ask for the completions it starts: citations after "[@", notes after "[[",
 *  tasks after "{{task ". */
async function typeAndComplete(text: string, close: string): Promise<void> {
  editing();
  const view = ws.editor!.view;
  const { from, to } = view.state.selection.main;
  view.dispatch({ changes: { from, to, insert: text + close }, selection: { anchor: from + text.length }, userEvent: 'input.type' });
  view.focus();
  const cm = await loadCM();
  cm.startCompletion(view);
}

async function upload(files: FileList | File[]): Promise<string[]> {
  const out: string[] = [];
  for (const f of [...files]) {
    try { out.push((await api.uploadNoteImage(f)).markdown); } catch (e) { toastError(e); }
  }
  return out;
}

/** "fig:speckle-pattern" from "attachments/20261026-0517-speckle-pattern.png". */
function labelOf(markdown: string): string {
  const name = /\(([^)]+)\)/.exec(markdown)?.[1] || '';
  const stem = name.replace(/^.*\//, '').replace(/\.[^.]+$/, '').replace(/^\d{8}-\d{6}-/, '');
  return `fig:${stem.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'name'}`;
}

/** Images from this computer. Several become one gallery with Extended Markdown's figures (as when
 *  pasted or dropped together). With ``figure`` the image, or the gallery, gets a caption to type
 *  and a label to refer to it, so it is numbered. */
function pickImages(figure: boolean): void {
  pickFiles(async (files) => {
    const images = await upload(files);
    if (!images.length) return;
    const label = `\\label{${labelOf(images[0])}}`;
    if (!figure) { insertBlock(imagesMarkdown(images)); return; }
    if (images.length === 1) {
      insertBlock(`${images[0]}\n*Caption. ${label}*`, 'Caption');
    } else {
      insertBlock(imagesMarkdown(images, { caption: `Caption. ${label}`, panels: true }), 'Caption');
    }
    toast('Type the caption; \\cref{…} with its label refers to the figure.', { timeout: 5000 });
  }, IMAGES, true);
}

/** The new-task popup at the cursor, as the Task button in the note's head opens it. */
function newTask(): void {
  editing();
  const anchor = ws.root.querySelector<HTMLElement>('.note-add-task') || document.body;
  taskHooks.add?.(anchor);
}
