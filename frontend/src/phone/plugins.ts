// What plugins add to the phone: the Plugins tab (every list and page of the running plugins),
// a plugin's list of papers (cards and rows, grouped as the plugin says, swiped to add or dismiss)
// and a plugin's page drawn by its phone layout.
import * as A from '../actions.js';
import { api } from '../api.js';
import { pages, type Cleanup } from '../extend.js';
import { icon } from '../icons.js';
import { off, on, state } from '../store.js';
import type { Item } from '../types.js';
import { toastError } from '../ui/toast.js';
import { escapeHtml as esc } from '../util.js';
import { openSettings } from '../views/settingsView.js';
import { dismissAction, itemScreen, readPdf } from './library.js';
import { type Destination, groupRuns, itemMeta } from './model.js';
import { noteScreen } from './notes.js';
import { type Screen, back, choosePin, destinationList, paintTabs, pinnedDestination, push } from './shell.js';
import { cardHtml, chevron, navBar, rootHeader, roundButton, rowHtml, swipeRows } from './ui.js';

export function pluginsScreen(): Screen {
  return {
    title: 'Plugins',
    key: 'plugins',
    mount(el) {
      const paint = () => {
        const dests = destinationList();
        const pin = pinnedDestination();
        const usable = dests.filter((d) => d.phone), other = dests.filter((d) => !d.phone);
        const row = (d: Destination) => `<button class="ph-dest${d.phone ? '' : ' off'}" data-dest="${esc(d.id)}"${d.phone ? '' : ' aria-disabled="true"'}>
          <span class="ph-dest-icon">${icon(d.icon)}</span>
          <span class="grow"><span class="ph-row-title">${esc(d.title)}</span><span class="ph-row-meta">${esc(d.phone ? d.pluginName : `${d.pluginName} · needs a larger screen`)}</span></span>
          ${d.id === pin?.id ? '<span class="ph-tag small">In tab bar</span>' : ''}
          ${d.badge ? `<span class="ph-count badge">${esc(d.badge)}</span>` : ''}${d.phone ? chevron() : ''}</button>`;
        el.innerHTML = `<div class="ph-scroll">
          ${rootHeader('Plugins', { actions: roundButton('gear', 'Settings', 'data-act="settings"') })}
          ${usable.length ? `<h2 class="ph-section-label">Lists and pages</h2><div class="ph-group">${usable.map(row).join('')}</div>`
            : `<div class="ph-empty">${icon('plugin')}<h3>No plugin lists here</h3><p>Plugins with a list of papers or a page of their own show up here.</p></div>`}
          ${other.length ? `<div class="ph-group">${other.map(row).join('')}</div>` : ''}
          <div class="ph-group">
            ${usable.length ? `<button class="ph-link-row" data-act="pin"><span>Tab next to Notes</span><small>${esc(pin?.title || 'None')}</small>${chevron()}</button>` : ''}
            <button class="ph-link-row" data-act="manage"><span>Turn plugins on or off</span>${chevron()}</button>
          </div>
          <p class="ph-muted pad">Plugins without a list or page of their own show up inside papers, notes and the + button.</p></div>`;
      };
      paint();
      el.addEventListener('click', (e) => {
        const t = e.target as Element;
        const d = t.closest<HTMLElement>('[data-dest]');
        if (d) {
          const dest = destinationList().find((x) => x.id === d.dataset.dest);
          if (dest?.phone) push(destinationScreen(dest));
          return;
        }
        const act = t.closest<HTMLElement>('[data-act]')?.dataset.act;
        if (act === 'pin') choosePin();
        if (act === 'manage') openSettings('plugins');
        if (act === 'settings') void import('./main.js').then((m) => m.settingsMenu());
      });
      const repaint = () => { paintTabs(); paint(); };
      on('sources', repaint);
      const offPages = pages.subscribe(repaint);
      return () => { off('sources', repaint); offPages(); };
    },
  };
}

/** A plugin's list or page; ``root``: it is the first screen of the pinned tab. */
export function destinationScreen(d: Destination, { root = false } = {}): Screen {
  return d.kind === 'source' ? sourceScreen(d.id.slice(7), root) : pageScreen(d.id.slice(5), root);
}

function pageScreen(id: string, root: boolean): Screen {
  const page = pages.get(id);
  return {
    title: page?.title || 'Page',
    key: `page:${id}`,
    mount(el, ctx) {
      const p = pages.get(id);
      el.innerHTML = `${root ? `<div class="ph-scroll ph-page-scroll">${rootHeader(p?.title || 'Page', { actions: roundButton('gear', 'Settings', 'data-act="settings"') })}` : `${navBar(ctx.backLabel, p?.title || '')}<div class="ph-scroll ph-page-scroll">`}
        <div class="ph-page-body"${p?.owner ? ` data-plugin="${esc(p.owner)}"` : ''}></div></div>`;
      el.querySelector('[data-act="settings"]')?.addEventListener('click', () => { void import('./main.js').then((m) => m.settingsMenu()); });
      const body = el.querySelector<HTMLElement>('.ph-page-body')!;
      if (!p?.phone) { body.innerHTML = `<div class="ph-empty">${icon('columns')}<h3>Needs a larger screen</h3><p>Open this page on a computer or tablet.</p></div>`; return; }
      let r: Cleanup;
      try {
        r = p.phone(body, {
          push: (s) => push({ title: s.title, mount: (box, c) => {
            box.innerHTML = `${navBar(c.backLabel, s.title)}<div class="ph-scroll"><div class="ph-page-body"${p.owner ? ` data-plugin="${esc(p.owner)}"` : ''}></div></div>`;
            return s.mount(box.querySelector<HTMLElement>('.ph-page-body')!);
          } }),
          back,
          openNote: (path) => push(noteScreen(path)),
          openItem: (itemId) => push(itemScreen(itemId)),
        });
      } catch (e) { body.innerHTML = `<div class="ph-empty">${icon('alert')}<h3>This page could not be shown</h3><p>${esc((e as Error).message)}</p></div>`; return; }
      return typeof r === 'function' ? r : undefined;
    },
  };
}

function sourceScreen(sourceId: string, root: boolean): Screen {
  const src = () => state.sources.find((s) => s.id === sourceId);
  return {
    title: src()?.title || 'List',
    key: `source:${sourceId}`,
    mount(el, ctx) {
      let variant: string | null = null;
      let items: Item[] = [];
      let loading = true;
      const s0 = src();
      el.innerHTML = `${root ? '' : navBar(ctx.backLabel, '')}<div class="ph-scroll">
        <div class="ph-head-slot"></div><div class="ph-chips ph-variants" role="list"></div>
        <div class="ph-source-list"></div></div>`;
      const list = el.querySelector<HTMLElement>('.ph-source-list')!;
      const paintHead = () => {
        const s = src() || s0;
        el.querySelector('.ph-head-slot')!.innerHTML = rootHeader(s?.title || 'List', {
          kicker: s?.plugin_name || '',
          actions: root ? roundButton('gear', 'Settings', 'data-act="settings"') : '',
        });
        const variants = s?.variants || [];
        const chosen = variant ?? variants[0]?.id ?? null;
        el.querySelector('.ph-variants')!.innerHTML = variants.map((v) => `<button class="ph-chip${v.id === chosen ? ' on' : ''}" data-variant="${esc(v.id)}" role="listitem">
          ${esc(v.title)}${v.badge ? ` <span class="ph-chip-badge">${esc(v.badge)}</span>` : ''}</button>`).join('');
      };
      const dismissable = (it: Item) => !!dismissAction([it]);
      const paint = () => {
        paintHead();
        if (loading && !items.length) { list.innerHTML = '<div class="ph-loading">Loading…</div>'; return; }
        if (!items.length) { list.innerHTML = `<div class="ph-empty">${icon('list')}<h3>Nothing here</h3><p>${esc(src()?.description || '')}</p></div>`; return; }
        const runs = groupRuns(items);
        const grouped = runs.length > 1 || !!runs[0].group;
        list.innerHTML = runs.map((r, i) => {
          const cards = !grouped || i === 0;
          const head = r.group ? `<div class="ph-group-head"><h2>${esc(r.group)} <span>${r.items.length}</span></h2>${i === 0 && runs.length > 1 ? '' : i === 1 ? '<span class="ph-muted">Swipe → add · ← dismiss</span>' : ''}</div>` : '';
          return head + (cards
            ? `<div class="ph-cards">${r.items.map((it) => cardHtml(it, { dismiss: dismissable(it) })).join('')}</div>`
            : `<div class="ph-group swipe">${r.items.map((it) => rowHtml(it, { extra: rowExtra(it), marks: it._outside ? '' : `<span class="ph-mark in" title="In your library">${icon('check')}</span>` })).join('')}</div>`);
        }).join('');
      };
      const load = async () => {
        const s = src();
        if (!s) { list.innerHTML = '<div class="ph-empty"><p>This list went away with its plugin.</p></div>'; return; }
        try {
          const view = `source:${sourceId}${variant ? `~${variant}` : ''}`;
          const res = await api.listItems({ view });
          items = [...res.items].sort((a, b) => (a._rank ?? 0) - (b._rank ?? 0));
          for (const it of items) it._lean = !it._outside;
        } catch (e) { toastError(e); }
        loading = false;
        paint();
      };
      const byId = (id: string) => items.find((i) => i.id === id);
      const act = async (id: string, what: 'add' | 'dismiss' | 'pdf' | 'open') => {
        const it = byId(id);
        if (!it) return;
        if (what === 'open') { push(itemScreen(it)); return; }
        if (what === 'pdf') { readPdf(it); return; }
        if (what === 'add') {
          if (!it._outside) { paint(); return; }
          await A.addOutside([it]);
        } else {
          const d = dismissAction([it]);
          if (!d) { paint(); return; }
          items = items.filter((x) => x !== it);
          paint();
          await d.run([it]);
        }
        await load();
      };
      el.addEventListener('click', (e) => {
        const t = e.target as Element;
        const v = t.closest<HTMLElement>('[data-variant]');
        if (v) { variant = v.dataset.variant!; loading = true; items = []; paint(); void load(); return; }
        if (t.closest('[data-act="settings"]')) { void import('./main.js').then((m) => m.settingsMenu()); return; }
        const pill = t.closest<HTMLElement>('[data-act]');
        const card = t.closest<HTMLElement>('[data-id]');
        if (!card) return;
        const what = (pill?.dataset.act as 'add' | 'dismiss' | 'pdf' | undefined) || 'open';
        void act(card.dataset.id!, what);
      });
      swipeRows(list, (id) => { const it = byId(id); return { add: !!it?._outside, dismiss: !!it && dismissable(it) }; }, (id, what) => void act(id, what));
      const reload = () => { void load(); };
      on('sources', reload);
      paint();
      void load();
      return () => off('sources', reload);
    },
  };
}

/** A row's second line in a plugin's list: who wrote it, then the plugin's first reason. */
function rowExtra(it: Item): string {
  const why = (it.reasons || []).find((r) => r.kind !== 'match' && r.kind !== 'following')?.text;
  return [itemMeta(it), why].filter(Boolean).join(' · ');
}

