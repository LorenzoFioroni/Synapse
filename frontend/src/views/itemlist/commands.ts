// The library's commands for the command palette: what the item menu does to the selected items
// (attach a PDF, find one, tags, folders, citation keys …) and actions on the library itself (add
// PDFs as items, new folder, trash). A command on the selection is offered only while the library
// is on screen and it fits what is selected, so the palette never lists one that would do nothing.
// Plugins' item actions reach the palette through plugins/host.ts.
import * as A from '../../actions.js';
import { commands } from '../../extend.js';
import { openAttachment, openPdfInNewWindow } from '../../api.js';
import { currentFolderId, selectedItems, state } from '../../store.js';
import type { Item } from '../../types.js';
import { openNewWindow } from '../../windows.js';
import { openMetadataReview } from '../metadataReview.js';
import { addSelectionToFolder, editTagsDialog, moveSelectionToFolder, pickFolder, smartFolderDialog } from '../organizeDialogs.js';
import { fetchReferencesBulk } from '../referencesSection.js';
import { newFolder } from '../sidebar.js';
import { copyBibtex, copyKeys, newPaperNote, openItemNote, pickFiles } from './menus.js';

export interface LibraryCommandsHost {
  /** Is the library list on screen (not Notes, Tasks, a page, Settings or a PDF tab)? */
  shown: () => boolean;
  /** Bring the library back on screen. */
  show: () => Promise<void>;
}

type Selected = { items: Item[]; one: Item | null; ids: string[] };

export function registerLibraryCommands(host: LibraryCommandsHost): void {
  const sel = (): Selected => {
    const items = selectedItems();
    return { items, one: items.length === 1 ? items[0] : null, ids: items.map((i) => i.id) };
  };
  const inLibrary = (items: Item[]) => items.length > 0 && state.view !== 'trash' && !items.some((i) => i._outside);
  const notesOn = () => state.settings.notes_enabled !== false;

  /** A command on the selected library items; ``ok`` narrows when it is offered. */
  const onItems = (id: string, title: string, run: (s: Selected) => unknown, ok: (s: Selected) => boolean = () => true, key?: string) => {
    commands.register({
      id: `core.items.${id}`, title, key,
      when: () => { const s = sel(); return host.shown() && inLibrary(s.items) && ok(s); },
      run: () => run(sel()),
    });
  };
  const one = (s: Selected) => !!s.one;
  const attachments = (s: Selected) => s.one?.attachments || [];

  onItems('open-pdf', 'Open PDF', (s) => A.openPdf(s.one!), (s) => attachments(s).length > 0);
  onItems('open-pdf-window', 'Open PDF in New Window', (s) => openPdfInNewWindow(s.one!, attachments(s)[0]), (s) => attachments(s).length > 0);
  onItems('open-attachment', 'Open Attachment…', (s) => pickAttachment(s.one!), (s) => attachments(s).length > 1);
  onItems('add-attachment', 'Add PDF Attachment…', (s) => pickFiles((files) => A.uploadAttachments(s.one!.id, files)), one);
  onItems('find-pdf', 'Find Available PDF', (s) => (s.one ? A.findPdf(s.one.id) : A.findPdfs(s.ids)));
  onItems('metadata', 'Retrieve Metadata…', (s) => openMetadataReview(s.ids));
  onItems('references', 'Fetch References', (s) => fetchReferencesBulk(s.ids));
  onItems('view-online', 'View Online', (s) => window.open(onlineUrl(s.one!), '_blank'), (s) => !!s.one && !!onlineUrl(s.one));
  onItems('new-window', 'Show Item in New Window', (s) => openNewWindow({ item: s.one!.id, view: currentFolderId() ? state.view : undefined }), one);
  onItems('paper-note', 'Open Paper Note', (s) => openItemNote(s.one!), (s) => notesOn() && !!s.one && !!state.noteKeys[s.one.citation_key || '']);
  onItems('new-paper-note', 'New Paper Note', (s) => newPaperNote(s.one!),
    (s) => notesOn() && !!s.one?.citation_key && !state.noteKeys[s.one.citation_key]);
  onItems('add-to-folder', 'Add to Folder…', (s) => addSelectionToFolder(s.ids));
  onItems('move-to-folder', 'Move to Another Folder…', (s) => moveSelectionToFolder(s.ids), () => !!currentFolderId());
  onItems('remove-from-folder', 'Remove from This Folder', (s) => A.removeFromFolder(s.ids, currentFolderId()!), () => !!currentFolderId());
  onItems('tags', 'Edit Tags…', (s) => editTagsDialog(s.ids));
  onItems('copy-key', 'Copy Citation Key', (s) => copyKeys(s.items));
  onItems('copy-bibtex', 'Copy as BibTeX', (s) => copyBibtex(s.ids));
  onItems('regenerate-key', 'Regenerate Citation Key', (s) => A.regenerateCitekeys(s.ids));
  onItems('trash', 'Move to Trash', () => A.trashSelected());

  // the Trash and plugins' lists
  const trashSel = () => host.shown() && state.view === 'trash' && sel().items.length > 0;
  commands.register({ id: 'core.items.restore', title: 'Restore to Library', when: trashSel, run: A.restoreSelected });
  commands.register({ id: 'core.items.delete', title: 'Delete Permanently…', when: trashSel, run: () => A.deleteForever(sel().ids) });
  const outside = () => { const items = selectedItems(); return host.shown() && items.length > 0 && items.every((i) => i._outside); };
  commands.register({ id: 'core.items.add-outside', title: 'Add to My Library', when: outside, run: () => A.addOutside(selectedItems()) });
  commands.register({
    id: 'core.items.add-outside-folder', title: 'Add to My Library in Folder…', when: outside,
    run: async () => { const items = selectedItems(); const fid = await pickFolder({ title: 'Add to Folder' }); if (fid !== undefined) await A.addOutside(items, fid); },
  });

  // the library itself: these bring it on screen first
  const library = (id: string, title: string, run: () => unknown, when?: () => boolean) => commands.register({
    id: `core.library.${id}`, title, when, run: async () => { if (!host.shown()) await host.show(); return run(); },
  });
  library('add-pdfs', 'Add PDFs as New Items…', () => pickFiles((files) => A.addPdfsAsItems(files)));
  library('new-folder', 'New Folder', () => newFolder(null));
  library('new-subfolder', 'New Subfolder', () => newFolder(currentFolderId()), () => !!currentFolderId() && host.shown());
  library('new-smart', 'New Smart Folder…', () => smartFolderDialog(null, state.query ? { query: state.query, mode: state.mode } : {}));
  library('my-library', 'Show My Library', () => A.setView('library'));
  library('duplicates', 'Show Duplicate Items', () => A.setView('duplicates'));
  library('show-trash', 'Show Trash', () => A.setView('trash'));
  library('empty-trash', 'Empty Trash…', () => A.emptyTrash(), () => !!state.counts.trash);
  library('clear-tags', 'Clear Tag Filter', () => A.clearTagFilter(), () => state.tagFilter.size > 0);
}

function onlineUrl(it: Item): string {
  return it.url || (it.doi ? `https://doi.org/${it.doi}` : '');
}

/** An item with several attachments: choose which one to open. */
async function pickAttachment(it: Item): Promise<void> {
  const { openMenu } = await import('../../ui/menu.js');
  const row = document.querySelector<HTMLElement>(`.row[data-id="${CSS.escape(it.id)}"]`);
  openMenu((it.attachments || []).map((a) => ({ label: a.name || 'PDF', icon: 'pdf', run: () => openAttachment(it, a) })),
    row || { x: innerWidth / 2, y: innerHeight / 3 });
}
