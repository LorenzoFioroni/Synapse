// Public Notes visualization registration, bridged to the workspace's layout and history.
// The workspace owns navigation; plugins receive only their main host and lifecycle callbacks.
import { navigation } from '../extend.js';
import { navUpdate } from '../nav.js';
import { state } from '../store.js';
import { noteHooks, setNoteHook, type GraphHook } from '../views/notes/hooks.js';
import { ws } from '../views/notes/workspace.js';
import type { Dispose, NotesVisualizationSpec, PluginContext } from './api.js';

let active: { owner: string; id: string; hook: GraphHook } | null = null;
export const notesEnabled = () => state.settings.notes_enabled !== false;

export function notesContext(owner: string, stopped: () => boolean, keep: (off: Dispose) => Dispose, report: (e: unknown) => void): PluginContext['notes'] {
  const live = () => { if (stopped()) throw new DOMException('Plugin stopped', 'AbortError'); };
  const selected = (id: string) => {
    live();
    if (!active || active.owner !== owner || active.id !== id) throw new Error(`No Notes visualization ${id}`);
    return active.hook;
  };
  async function leaveOthers() {
    live();
    const [settings, { tasksPage }, pages] = await Promise.all([
      import('../views/settingsView.js'), import('../tasksPage.js'), import('../views/pages.js'),
    ]);
    live();
    if (settings.settingsOpen()) settings.closeSettings();
    tasksPage.close();
    pages.closePage({ push: false });
  }
  async function open(id: string, focus: string | null = null) {
    const hook = selected(id);
    await leaveOthers();
    if (selected(id) !== hook) return;
    const notes = await import('../views/notes/notesView.js');
    if (selected(id) === hook) await notes.openNotesGraph(focus);
  }
  return {
    enabled: notesEnabled,
    current: () => stopped() ? null : ws.session?.path || null,
    async open(path) {
      await leaveOthers();
      const notes = await import('../views/notes/notesView.js');
      live();
      await notes.openNotes(path);
    },
    visualizations: {
      register(spec: NotesVisualizationSpec) {
        live();
        if (active || noteHooks.graph) throw new Error('A Notes visualization is already registered');
        const run = <T>(fn: () => T, fallback: T): T => { try { return fn(); } catch (e) { report(e); return fallback; } };
        const hook: GraphHook = {
          title: spec.title, icon: spec.icon,
          attach: ctx => run(() => spec.attach({
            main: ctx.root.querySelector<HTMLElement>('.notes-main')!,
            currentPath: ctx.currentPath,
            openNote: path => { live(); ctx.openNote(path); },
            open: focus => { void open(spec.id, focus).catch(report); },
            setOpen: on => { if (stopped()) return; ctx.root.classList.toggle('graph-on', on); navigation.changed(); },
            closed: () => ctx.onGlobalClosed?.(),
          }), undefined),
          localSection: () => run(() => spec.section(), document.createElement('div')),
          showLocal: path => run(() => spec.showNote(path), undefined),
          changed: () => run(() => spec.changed(), undefined),
          openGlobal: async focus => { try { await spec.open(focus); } catch (e) { report(e); } finally { navigation.changed(); } },
          closeGlobal: options => run(() => spec.close({ notify: options?.push }), undefined),
          globalOpen: () => run(() => spec.isOpen(), false),
        };
        active = { owner, id: spec.id, hook };
        const offNav = navigation.register({
          id: `${owner}.${spec.id}`, title: spec.title, icon: spec.icon || 'link',
          when: notesEnabled,
          active: () => document.body.dataset.workspace === 'notes' && hook.globalOpen(),
          open: () => open(spec.id),
        });
        setNoteHook('graph', hook);
        let removed = false;
        return keep(() => {
          if (removed) return;
          removed = true;
          offNav();
          if (active?.hook === hook) {
            if (document.body.dataset.workspace === 'notes' && hook.globalOpen()) navUpdate('notes', { graph: false, focus: null });
            hook.closeGlobal();
            ws.root?.classList.remove('graph-on');
            active = null;
            if (noteHooks.graph === hook) setNoteHook('graph', null);
          }
          run(() => spec.destroy(), undefined);
        });
      },
      open,
      async toggle(id) {
        const hook = selected(id);
        if (document.body.dataset.workspace === 'notes' && hook.globalOpen()) {
          hook.closeGlobal({ push: true }); ws.editor?.focus();
        } else await open(id, ws.session ? `note:${ws.session.path}` : null);
      },
    },
  };
}
