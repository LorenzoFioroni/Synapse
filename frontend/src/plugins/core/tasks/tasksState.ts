// What the Tasks page shows (view, search, sort, group, toggles, folded tasks, selection),
// remembered in this browser between visits (the selection and search are not).
import { storage, store } from '../../../util.js';
import type { GroupId, SortId, ViewId } from './taskQuery.js';

export interface ListState {
  view: ViewId;
  query: string;
  sort: SortId;
  group: GroupId;
  showDone: boolean;
  flat: boolean;
  folded: Set<string>;
  note: string | null;      // only the tasks of this note
  keep: Set<string>;        // ticked here: still shown (struck through) until the view changes
  sel: Set<string>;
  anchor: string | null;    // where Shift-click extends from
  cursor: string | null;    // the row the keyboard is on
  save(): void;
}

const KEY = 'zc.tasks.view';

export function loadState(): ListState {
  const s = storage<Partial<{ view: ViewId; sort: SortId; group: GroupId; showDone: boolean; flat: boolean; folded: string[]; note: string | null }>>(KEY, {});
  const st: ListState = {
    view: s.view || 'open',
    query: '',
    sort: s.sort || 'position',
    group: s.group || 'note',
    showDone: !!s.showDone,
    flat: !!s.flat,
    folded: new Set(s.folded || []),
    note: s.note || null,
    keep: new Set(),
    sel: new Set(),
    anchor: null,
    cursor: null,
    save() {
      store(KEY, { view: st.view, sort: st.sort, group: st.group, showDone: st.showDone, flat: st.flat, folded: [...st.folded].slice(-500), note: st.note });
    },
  };
  return st;
}
