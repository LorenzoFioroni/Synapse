// The phone layout's entry point (main.ts hands over to it on a phone, see layout.ts): loads the
// settings, starts the plugins and the change feed like the desktop does, and builds the phone
// shell (shell.ts) with its tabs. The desktop's own views are never built here.
import * as A from '../actions.js';
import { api } from '../api.js';
import { initChanges } from '../changes.js';
import { captures } from '../extend.js';
import { icon } from '../icons.js';
import { initNotifications, openCenter } from '../notifications/center.js';
import { initPluginHost } from '../plugins/host.js';
import { activeTaskProvider } from '../plugins/taskHost.js';
import { on, state } from '../store.js';
import { installLongPress } from '../ui/longpress.js';
import { menusAsSheets, openMenu } from '../ui/menu.js';
import { toast, toastError } from '../ui/toast.js';
import { escapeHtml as esc } from '../util.js';
import { openIdentifierDialog } from '../views/identifierDialog.js';
import { pickFiles } from '../views/itemlist/menus.js';
import { applyAppearance, openSettings } from '../views/settingsView.js';
import { switchLayout } from './layout.js';
import { libraryScreen, itemScreen } from './library.js';
import { newNote, noteScreen, notesScreen } from './notes.js';
import { destinationScreen, pluginsScreen } from './plugins.js';
import { currentTab, destinationList, initShell, openDestination, pinnedDestination, push, selectTab } from './shell.js';
import { openSheet } from './ui.js';

function finishStartup(): void {
  const el = document.getElementById('startup-screen');
  if (!el) return;
  el.classList.add('ready');
  setTimeout(() => el.remove(), 180);
}

export async function bootPhone(): Promise<void> {
  document.body.classList.add('phone');
  try {
    const [schema, settings] = await Promise.all([api.schema(), api.getSettings()]);
    state.schema = schema;
    state.settings = settings.settings;
    state.settingsMeta = settings;
    state.mode = settings.settings.default_search_mode;
  } catch (e) {
    document.body.insertAdjacentHTML('beforeend', `<div class="ph-empty ph-fatal">${icon('alert')}<h3>Cannot reach the server</h3><p>${esc((e as Error).message)}</p></div>`);
    finishStartup();
    return;
  }
  applyAppearance();
  menusAsSheets(true);
  installLongPress();
  // the notification center belongs to a bell; the phone opens it from the settings button
  const bell = document.createElement('button');
  bell.className = 'ph-bell';
  bell.hidden = true;
  bell.innerHTML = '<span class="notification-badge" hidden></span>';
  document.body.appendChild(bell);
  initNotifications(bell);

  initShell({
    library: () => libraryScreen(),
    notes: () => (state.settings.notes_enabled !== false ? notesScreen() : null),
    pin: () => { const d = pinnedDestination(); return d ? destinationScreen(d, { root: true }) : null; },
    plugins: () => pluginsScreen(),
  }, openCapture);
  wireEvents();
  try { await A.loadItems(); } finally { finishStartup(); }
  initChanges();
  void initPluginHost();
  window.zcPhone = { tab: currentTab, select: selectTab };   // for tests
}

/** What other parts of the app ask for, done the phone's way: on top of the tab shown. */
function wireEvents(): void {
  on('show-item', (id) => push(itemScreen(id)));
  on('open-note', ({ path, line }) => push(noteScreen(path, { line: line ?? null })));
  on('open-notes', () => selectTab('notes'));
  on('close-notes', () => selectTab('library'));
  on('open-view', (view) => {
    if (view.startsWith('source:')) openDestination(view, (d) => destinationScreen(d));
    else { A.setView(view); void A.loadItems({ keepSelection: false }); selectTab('library'); }
  });
  on('open-page', (id) => openDestination(`page:${id}`, (d) => destinationScreen(d)));
  on('open-tasks', () => {
    // the Tasks plugin's page for phones, whichever plugin provides Tasks
    const owner = activeTaskProvider();
    const d = owner ? destinationList().find((x) => x.kind === 'page' && x.plugin === owner && x.phone) : null;
    if (d) openDestination(d.id, (x) => destinationScreen(x));
  });
  on('open-settings', (section) => openSettings(section || null));
  on('open-identifier', () => { void openIdentifierDialog(); });
}

/** The settings button of every tab: Settings, notifications, and the desktop layout. */
export function settingsMenu(): void {
  const unread = Number(document.querySelector('.ph-bell .notification-badge')?.textContent || 0);
  openMenu([
    { label: 'Settings', icon: 'gear', run: () => openSettings() },
    { label: unread ? `Notifications (${unread})` : 'Notifications', icon: 'bell', run: () => openCenter() },
    'sep',
    { label: 'Use the Desktop Layout', icon: 'columns', run: () => switchLayout('desktop') },
  ], { x: 0, y: innerHeight });
}

/** The "+" sheet: a paper by identifier, a note, a PDF, and what plugins add. */
function openCapture(): void {
  const sheet = openSheet('Add');
  const extra = captures.all();
  sheet.body.innerHTML = `
    <form class="ph-capture-id">
      <label class="ph-field">${icon('link')}<input name="id" placeholder="DOI, arXiv ID, ISBN or link" aria-label="DOI, arXiv ID, ISBN or link" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
      <div class="ph-capture-id-actions">
        <button type="button" class="ph-pill" data-cap="paste">${icon('copy')}Paste</button>
        <button type="submit" class="ph-pill primary">Add to Library</button>
      </div>
    </form>
    <div class="ph-tiles">
      ${state.settings.notes_enabled !== false ? `<button class="ph-tile" data-cap="note">${icon('edit')}<span>Note</span></button>` : ''}
      <button class="ph-tile" data-cap="pdf">${icon('pdf')}<span>PDF file</span></button>
    </div>
    ${extra.length ? `<h3 class="ph-sheet-section">From plugins</h3><div class="ph-tiles">${extra.map((c) =>
      `<button class="ph-tile" data-capture="${esc(c.id)}">${icon(c.icon || 'plugin')}<span>${esc(c.title)}</span><small>${esc(c.source || '')}</small></button>`).join('')}</div>` : ''}`;
  const form = sheet.body.querySelector<HTMLFormElement>('form')!;
  const input = form.querySelector<HTMLInputElement>('input')!;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    const btn = form.querySelector<HTMLButtonElement>('[type=submit]')!;
    btn.disabled = true; btn.textContent = 'Adding…';
    try {
      const item = await api.fromIdentifier(text);
      sheet.close();
      await A.loadItems();
      toast(`Added “${(item.title || 'Untitled').slice(0, 60)}”`, { type: 'success' });
      push(itemScreen(item.id), 'library');
    } catch (err) {
      toastError(err);
      btn.disabled = false; btn.textContent = 'Add to Library';
    }
  });
  sheet.body.addEventListener('click', async (e) => {
    const b = (e.target as Element).closest<HTMLElement>('[data-cap], [data-capture]');
    if (!b) return;
    if (b.dataset.capture) { sheet.close(); captures.get(b.dataset.capture)?.run(); return; }
    const what = b.dataset.cap;
    if (what === 'paste') {
      try { input.value = (await navigator.clipboard.readText()).trim(); } catch { input.focus(); toast('Paste into the field to add it.'); }
      return;
    }
    sheet.close();
    if (what === 'note') await newNote();
    if (what === 'pdf') pickFiles((files) => { void A.addPdfsAsItems(files).then(() => selectTab('library')); });
  });
}
