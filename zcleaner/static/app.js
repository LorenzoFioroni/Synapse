/* Zotero Library Cleaner — review GUI (vanilla JS, no build step) */
"use strict";
const TOKEN = document.querySelector('meta[name=token]').content;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = x => String(x ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmtN = n => (n ?? 0).toLocaleString();
const S = {status: null, view: "dashboard", review: {status: "pending", offset: 0, order: "title"}, issues: {status: "open", offset: 0, source: "rules,lookup"},
  dups: {status: "open", offset: 0}, showRaw: false, focus: 0, lastJob: null, ruleLabels: {}};

async function api(path, opts = {}) {
  const o = {...opts, headers: {"X-Token": TOKEN, "Content-Type": "application/json", ...(opts.headers || {})}};
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const j = await r.json().catch(() => ({error: r.statusText}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const post = (p, b = {}) => api(p, {method: "POST", body: b});
function qs(o) { return Object.entries(o).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&"); }

function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.className = "toast", err ? 6000 : 2800);
}
function modal(html, onReady) {
  $("#modal-card").innerHTML = html; $("#modal").classList.remove("hidden");
  if (onReady) onReady($("#modal-card"));
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
function confirmBox(title, text, okLabel = "Continue", danger = false) {
  return new Promise(res => {
    modal(`<h2 style="margin-top:0">${esc(title)}</h2><div class="sub">${text}</div>
      <div class="row"><span class="spacer"></span><button id="mc-no">Cancel</button><button id="mc-yes" class="${danger ? "danger" : "primary"}">${esc(okLabel)}</button></div>`, c => {
      $("#mc-no", c).onclick = () => { closeModal(); res(false); };
      $("#mc-yes", c).onclick = () => { closeModal(); res(true); };
      $("#mc-yes", c).focus();
    });
  });
}
const confBucket = c => c >= 0.85 ? "high" : c >= 0.6 ? "medium" : "low";
const sevChip = s => `<span class="chip ${s === "error" ? "err" : s === "warning" ? "warn" : "info"}">${esc(s)}</span>`;
const zlink = it => it && it.zotero ? `<a href="${esc(it.zotero)}" title="Show in Zotero">Open in Zotero ↗</a>` : "";
const FIELD_LABELS = {publicationTitle: "Publication", proceedingsTitle: "Proceedings", bookTitle: "Book title", abstractNote: "Abstract",
  journalAbbreviation: "Journal abbr.", shortTitle: "Short title", DOI: "DOI", ISSN: "ISSN", ISBN: "ISBN", url: "URL",
  itemType: "Item type", filename: "File name", parentItem: "Parent item", creators: "Creators", tags: "Tags", attachment: "PDF found online"};
const fieldLabel = f => FIELD_LABELS[f] || (f ? f.charAt(0).toUpperCase() + f.slice(1).replace(/([A-Z])/g, " $1").toLowerCase() : "");
const ISSUE_LABELS = {missing_title: "Missing title", missing_creators: "Missing authors", missing_date: "Missing date",
  missing_publication: "Missing journal/proceedings", missing_doi: "No DOI", malformed_doi: "Malformed DOI", duplicate_doi: "Duplicate DOI",
  type_suspect: "Suspicious item type", invalid_isbn: "Invalid ISBN", invalid_issn: "Invalid ISSN", latex_error: "LaTeX error",
  latex_unmapped: "No LaTeX equivalent", doi_mismatch: "DOI → different work", doi_not_found: "DOI not registered",
  published_version: "Published version exists", missing_pdf: "Missing PDF", pdf_file_missing: "PDF file missing on disk",
  pdf_not_pdf: "Not a real PDF", pdf_empty: "Empty PDF file", pdf_unreadable: "Unreadable PDF", pdf_encrypted: "Encrypted PDF",
  pdf_no_text: "No text layer (OCR)", pdf_mismatch: "PDF ≠ item", pdf_duplicate_file: "Identical PDF files",
  pdf_multiple: "Several PDFs", orphan_pdf: "Standalone PDF"};
const issueLabel = c => ISSUE_LABELS[c] || c;

/* ---------------------------------------------------------------- word diff */
function tokens(s) { return String(s ?? "").match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) || []; }
function diffWords(a, b) {
  const A = tokens(a), B = tokens(b);
  if (A.length * B.length > 250000) return {old: `<del>${esc(a)}</del>`, new: `<ins>${esc(b)}</ins>`};
  const n = A.length, m = B.length, dp = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, o = "", nw = "";
  const flush = (arr, tag) => arr.length ? `<${tag}>${esc(arr.join(""))}</${tag}>` : "";
  let da = [], ib = [];
  while (i < n || j < m) {
    if (i < n && j < m && A[i] === B[j]) { o += flush(da, "del"); nw += flush(ib, "ins"); da = []; ib = []; o += esc(A[i]); nw += esc(B[j]); i++; j++; }
    else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) { ib.push(B[j++]); }
    else { da.push(A[i++]); }
  }
  o += flush(da, "del"); nw += flush(ib, "ins");
  return {old: o, new: nw};
}

/* ---------------------------------------------------------------- status polling */
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    S.status = s;
    const p = s.proposals || {};
    $("#c-pending").textContent = p.pending ? fmtN(p.pending) : "";
    $("#c-accepted").textContent = p.accepted ? fmtN(p.accepted) : "";
    const iss = Object.entries(s.issues || {}).filter(([k]) => !k.startsWith("pdf") && !["missing_pdf", "orphan_pdf"].includes(k)).reduce((a, [, v]) => a + v, 0);
    $("#c-issues").textContent = iss ? fmtN(iss) : "";
    $("#c-dups").textContent = s.duplicates ? fmtN(s.duplicates) : "";
    const pdfIss = (s.issues.missing_pdf || 0) + (s.issues.pdf_file_missing || 0) + (s.issues.pdf_not_pdf || 0) + (s.issues.pdf_empty || 0) + (s.issues.pdf_mismatch || 0) + (s.issues.orphan_pdf || 0);
    $("#c-pdfs").textContent = pdfIss ? fmtN(pdfIss) : "";
    $("#conn").innerHTML = `<div><span class="dot ${s.server_id ? "on" : "off"}"></span>${s.server_id ? "Zotero linked" : "Not connected yet"}</div>
      <div class="faint small">${s.last_scan ? "Last scan " + new Date(s.last_scan).toLocaleString() : "Library not scanned yet"}</div>
      <div class="faint small">${s.write_authorised ? "Writes authorised" : "Writes not authorised"}</div>`;
    renderJob(s.job);
  } catch (e) {
    $("#conn").innerHTML = `<span class="dot off"></span>Server unreachable`;
  }
}
function renderJob(j) {
  const bar = $("#jobbar");
  if (j && j.status === "running") {
    bar.classList.remove("hidden");
    const needsYou = /^ACTION NEEDED/.test(j.message || "");
    bar.classList.toggle("attention", needsYou);
    $("#jobmsg").textContent = needsYou ? j.message.replace(/^ACTION NEEDED:\s*/, "👉 ") : `${j.kind}: ${j.message}`;
    if (needsYou && !S.lastAttention) toast(j.kind === "own_browser" ? "Download each PDF in your browser (the tabs just opened); the tool picks them up from your Downloads folder." : "A publisher asks for a check: solve it in the browser window that just opened.", true);
    S.lastAttention = needsYou;
    $("#jobprog").style.width = j.total ? `${Math.min(100, 100 * j.n / j.total)}%` : "15%";
    $("#jobcancel").onclick = () => post(`/api/jobs/${j.id}/cancel`).then(() => toast("Cancelling …"));
  } else {
    bar.classList.add("hidden");
  }
  if (S.lastJob && S.lastJob.status === "running" && j && j.id === S.lastJob.id && j.status !== "running") {
    if (j.status === "done" && (j.kind === "find_pdfs" || j.kind === "own_browser") && j.result) showFindResult(j.result, j.kind);
    else if (j.status === "done" && j.kind === "discover_tags" && j.result) {
      TAG_STATE = j.result;
      toast("Tag discovery complete");
    }
    else if (j.status === "done" && j.kind === "reassign_tags") toast("Tags reassigned! Check 'Review changes' to apply them.");
    else if (j.status === "done" && j.kind === "create_smart_folders") toast("Smart Folders created in Zotero!");
    else if (j.status === "done") toast(`${j.kind} finished`);
    else if (j.status === "failed") modal(`<h2 style="margin-top:0">${esc(j.kind)} failed</h2><div class="notice err">${esc(j.error)}</div><div class="row" style="margin-top:12px"><span class="spacer"></span><button onclick="closeModal()">Close</button></div>`);
    else toast(`${j.kind} ${j.status}`);
    render();
  }
  S.lastJob = j;
}
async function showFindResult(r, kind = "find_pdfs") {
  const reasons = Object.entries(r.reasons || {}).map(([k, n]) => `<li>${fmtN(n)} × ${esc(k)}</li>`).join("");
  let blocked = [];
  try { blocked = (await api("/api/pdfs/needs-browser")).keys; } catch (e) { /* ignore */ }
  const own = kind === "own_browser";
  modal(`<h2 style="margin-top:0">${own ? "Downloads picked up" : "PDF search finished"}</h2>
    <p>${own ? "Waited for" : "Searched"} <b>${fmtN(r.searched)}</b> item(s): <b style="color:var(--ok)">${fmtN(r.found)} found</b>${r.errors ? `, <span style="color:var(--err)">${fmtN(r.errors)} error(s)</span>` : ""}.</p>
    ${reasons ? `<p class="small muted">${own ? "Not picked up:" : "Not found:"}</p><ul class="small">${reasons}</ul>${own ? "" : `<p class="small muted">Most paywalled articles simply have no legal open-access copy; use your library access or Zotero's “Find Available PDF” for those.</p>`}` : ""}
    ${blocked.length ? `<div class="notice warn small"><b>${fmtN(blocked.length)} item(s) are blocked by a publisher's captcha</b>, which keeps re-asking in the automated browser. They do have a PDF: <b>Get them with your own browser</b> opens them in your normal browser; click “Download PDF” in each tab and the tool picks the files up from your Downloads folder.</div>` : ""}
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button onclick="closeModal()">Close</button>
    ${blocked.length ? `<button id="fr-own">Get ${fmtN(blocked.length)} with my own browser</button>` : ""}
    ${r.found ? `<button class="primary" onclick="closeModal(); location.hash='#review?kind=attach_pdf&status=pending'">Review found PDFs</button>` : ""}</div>`,
    c => { const b = $("#fr-own", c); if (b) b.onclick = () => { closeModal(); startJob("own_browser"); }; });
}
async function poll() {
  await refreshStatus();
  const running = S.status && S.status.job && S.status.job.status === "running";
  setTimeout(poll, running ? 1200 : 6000);
}
async function startJob(kind, params = {}) {
  try {
    const j = await post(`/api/jobs/${kind}`, params);
    S.lastJob = j; renderJob(j); setTimeout(refreshStatus, 400);
  } catch (e) { toast(e.message, true); }
}

/* ---------------------------------------------------------------- router */
const VIEWS = {dashboard: viewDashboard, review: viewReview, issues: viewIssues, duplicates: viewDuplicates, pdfs: viewPdfs,
  apply: viewApply, history: viewHistory, settings: viewSettings, tags: viewTags};
function route() {
  const h = (location.hash || "#dashboard").slice(1).split("?");
  S.view = VIEWS[h[0]] ? h[0] : "dashboard";
  if (h[1]) {
    const p = Object.fromEntries(new URLSearchParams(h[1]));
    if (S.view === "review") S.review = {status: "pending", offset: 0, order: "title", ...p};
    if (S.view === "issues") S.issues = {status: "open", offset: 0, source: "rules,lookup", ...p};
  }
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === S.view));
  render();
}
async function render() {
  const v = $("#view");
  try { await VIEWS[S.view](v); } catch (e) { v.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);

/* ---------------------------------------------------------------- dashboard */
async function viewDashboard(v) {
  await refreshStatus();
  const s = S.status, it = s.items || {}, p = s.proposals || {}, sev = s.severity || {};
  const scanned = !!s.last_scan;
  v.innerHTML = `
  <h1>Dashboard</h1>
  <p class="sub">Nothing is written to Zotero until you review the proposed changes and press <b>Apply</b>. A full backup is made first, and every commit can be rolled back.</p>
  <div class="grid g4">
    <div class="tile"><div class="label">Items</div><div class="num">${fmtN(it.regular)}</div><div class="hint">${fmtN(it.att)} attachments · ${fmtN(it.trashed)} in trash</div></div>
    <div class="tile link" data-go="#review?status=pending"><div class="label">Changes to review</div><div class="num">${fmtN(p.pending)}</div><div class="hint">${fmtN(p.stale || 0)} stale · ${fmtN(p.rejected || 0)} rejected</div></div>
    <div class="tile link" data-go="#apply"><div class="label">Accepted, not yet applied</div><div class="num" style="color:var(--accent)">${fmtN(p.accepted)}</div><div class="hint">${fmtN(p.applied || 0)} applied so far</div></div>
    <div class="tile link" data-go="#issues"><div class="label">Open issues</div><div class="num">${fmtN((sev.error || 0) + (sev.warning || 0))}</div><div class="hint">${fmtN(sev.error || 0)} errors · ${fmtN(sev.warning || 0)} warnings · ${fmtN(sev.info || 0)} info</div></div>
  </div>
  <h2>Run checks</h2>
  <div class="grid g4">
    <div class="card"><h3 style="margin-top:0">1 · Sync &amp; analyse</h3><p class="small muted">Reads the library (only changes after the first run), applies the local rules and finds duplicates. Fast.</p>
      <div class="row"><button class="primary" data-job="scan">Sync &amp; analyse</button><button class="ghost small" data-job="scan" data-full="1" title="Re-read everything">Full re-read</button></div></div>
    <div class="card"><h3 style="margin-top:0">2 · Check PDFs</h3><p class="small muted">Finds missing, broken, wrong and unreadable PDFs and proposes normalised file names.</p>
      <button data-job="pdfs">Check PDFs</button></div>
    <div class="card"><h3 style="margin-top:0">3 · Online lookups</h3><p class="small muted">Compares items with Crossref, DOI registries and arXiv; searches DOIs for items without one. Can take a while for big libraries (results are cached).</p>
      <div class="row"><button data-job="lookups">Look up all</button><button class="ghost small" data-job="lookups" data-problems="1">Only flagged items</button></div>
      ${s.mailto ? "" : `<p class="small" style="color:var(--warn)">Tip: set your e-mail in Settings to use Crossref's faster “polite” service.</p>`}</div>
    <div class="card"><h3 style="margin-top:0">Everything</h3><p class="small muted">Runs 1 → 2 → 3 in sequence.</p><button data-job="all">Run everything</button>
      <p class="small muted" style="margin-bottom:0">Changed settings? <a href="#" id="reanalyse">Re-analyse</a> the snapshot without reading Zotero again.</p></div>
  </div>
  <h2>Workflow</h2>
  <div class="card"><ol class="steps">
    <li>Connect: in Zotero enable <i>Settings → Advanced → Allow other applications on this computer to communicate with Zotero</i>. ${s.server_id ? '<span class="chip ok">done</span>' : '<button class="small" id="btn-connect">Test connection</button>'}</li>
    <li>Run the checks above. ${scanned ? '<span class="chip ok">scanned</span>' : ""}</li>
    <li>Go through <a href="#review">Review changes</a>: accept, reject or edit each proposal (bulk actions are available per rule and confidence).</li>
    <li>Look at <a href="#issues">Issues</a>, <a href="#duplicates">Duplicates</a> and <a href="#pdfs">PDFs</a> — these need your judgement; tag items to fix them in Zotero.</li>
    <li><a href="#apply">Apply</a>: authorise writes once (Zotero 10+ shows a dialog), then back up &amp; apply. Roll back from <a href="#history">History</a> if needed.</li>
  </ol></div>`;
  $$("[data-go]", v).forEach(t => t.onclick = () => location.hash = t.dataset.go);
  $$("[data-job]", v).forEach(b => b.onclick = () => startJob(b.dataset.job, b.dataset.full ? {full: true} : b.dataset.problems ? {only_problems: true} : {}));
  $("#reanalyse", v).onclick = e => { e.preventDefault(); startJob("analyse"); };
  const bc = $("#btn-connect", v);
  if (bc) bc.onclick = async () => { try { await post("/api/connect"); toast("Connected to Zotero"); render(); } catch (e) { toast(e.message, true); } };
}

/* ---------------------------------------------------------------- review */
async function viewReview(v) {
  const f = S.review;
  const facets = await api("/api/proposals/facets?" + qs({status: f.status}));
  S.ruleLabels = facets.rule_labels || {};
  const opt = (obj, cur, lab = x => x) => Object.entries(obj).map(([k, n]) => `<option value="${esc(k)}" ${k === cur ? "selected" : ""}>${esc(lab(k))} (${fmtN(n)})</option>`).join("");
  const stTabs = [["pending", "To review"], ["accepted", "Accepted"], ["rejected", "Rejected"], ["stale,conflict,failed", "Stale / conflicts"], ["applied", "Applied"], ["all", "All"]];
  v.innerHTML = `
  <h1>Review changes</h1>
  <p class="sub">Every proposal shows the current value and the proposed one (as rendered; toggle the LaTeX source). Accept, reject or edit. Accepted changes are written only when you apply them.</p>
  <div class="toolbar">
    <div class="row">
      <div class="seg" id="st">${stTabs.map(([k, l]) => `<button data-st="${k}" class="${f.status === k ? "on" : ""}">${l}${facets.status && facets.status[k] ? ` <span class="count">${fmtN(facets.status[k])}</span>` : ""}</button>`).join("")}</div>
      <span class="spacer"></span>
      <label class="small muted"><input type="checkbox" id="raw" ${S.showRaw ? "checked" : ""}> LaTeX source</label>
    </div>
    <div class="row">
      <select id="f-rule"><option value="">All rules</option>${opt(facets.rule, f.rule, k => S.ruleLabels[k] || k)}</select>
      <select id="f-source"><option value="">All sources</option>${opt(facets.source, f.source)}</select>
      <select id="f-field"><option value="">All fields</option>${opt(facets.field, f.field, fieldLabel)}</select>
      <select id="f-conf"><option value="">Any confidence</option>
        <option value="high" ${f.conf === "high" ? "selected" : ""}>High (${fmtN(facets.confidence.high || 0)})</option>
        <option value="medium" ${f.conf === "medium" ? "selected" : ""}>Medium (${fmtN(facets.confidence.medium || 0)})</option>
        <option value="low" ${f.conf === "low" ? "selected" : ""}>Low (${fmtN(facets.confidence.low || 0)})</option></select>
      <input type="search" id="f-q" placeholder="Search title / value / key" value="${esc(f.q || "")}" style="width:220px">
      <select id="f-order"><option value="title">Sort: title</option><option value="confidence" ${f.order === "confidence" ? "selected" : ""}>Most confident first</option><option value="low" ${f.order === "low" ? "selected" : ""}>Least confident first</option></select>
    </div>
    <div class="row bulk" id="bulk"></div>
  </div>
  <div id="list"><div class="empty">Loading …</div></div>`;
  $$("#st button", v).forEach(b => b.onclick = () => { S.review = {...S.review, status: b.dataset.st, offset: 0}; render(); });
  $("#raw", v).onchange = e => { S.showRaw = e.target.checked; loadReview(); };
  const upd = () => { S.review = {...S.review, rule: $("#f-rule").value, source: $("#f-source").value, field: $("#f-field").value,
    conf: $("#f-conf").value, q: $("#f-q").value, order: $("#f-order").value, offset: 0}; loadReview(); };
  ["#f-rule", "#f-source", "#f-field", "#f-conf", "#f-order"].forEach(s => $(s, v).onchange = upd);
  let t; $("#f-q", v).oninput = () => { clearTimeout(t); t = setTimeout(upd, 350); };
  await loadReview();
}
function reviewFilter() {
  const f = S.review, c = {high: [0.85, null], medium: [0.6, 0.8499], low: [null, 0.5999]}[f.conf] || [null, null];
  return {status: f.status, rule: f.rule, source: f.source, field: f.field, kind: f.kind, q: f.q, min_conf: c[0], max_conf: c[1], group: f.group};
}
async function loadReview() {
  const f = S.review, filt = reviewFilter();
  const data = await api("/api/proposals?" + qs({...filt, offset: f.offset || 0, limit: 40, order: f.order}));
  const list = $("#list");
  const bulk = $("#bulk");
  const nP = data.total_proposals;
  bulk.innerHTML = nP ? `<span>${fmtN(nP)} proposals on ${fmtN(data.total_items)} items match.</span>
      ${f.status !== "accepted" ? `<button class="small ok" id="b-acc">Accept all ${fmtN(nP)}</button>` : ""}
      ${f.status !== "rejected" ? `<button class="small danger" id="b-rej">Reject all ${fmtN(nP)}</button>` : ""}
      ${f.status !== "pending" ? `<button class="small" id="b-pen">Back to “to review”</button>` : ""}` : "";
  const bulkDo = async (status, label) => {
    if (!await confirmBox(`${label} ${fmtN(nP)} proposals?`, "This affects every proposal matching the current filters, not only the ones on this page.", label)) return;
    const r = await post("/api/proposals/bulk", {filter: filt, status});
    toast(`${fmtN(r.count)} proposals updated`); refreshStatus(); render();
  };
  if ($("#b-acc")) $("#b-acc").onclick = () => bulkDo("accepted", "Accept");
  if ($("#b-rej")) $("#b-rej").onclick = () => bulkDo("rejected", "Reject");
  if ($("#b-pen")) $("#b-pen").onclick = () => bulkDo("pending", "Reset");
  if (!data.items.length) { list.innerHTML = `<div class="empty">Nothing here. ${f.status === "pending" ? "Run the checks from the dashboard, or relax the filters." : ""}</div>`; return; }
  list.innerHTML = data.items.map(renderItemCard).join("") + pager(data.offset, 40, data.total_items, "items");
  bindReview(list);
  S.focus = Math.min(S.focus, $$(".prop", list).length - 1);
  setFocus(Math.max(0, S.focus), false);
  $$(".pager button", list).forEach(b => b.onclick = () => { S.review.offset = +b.dataset.off; S.focus = 0; loadReview(); window.scrollTo(0, 0); });
}
function pager(off, lim, total, noun) {
  if (total <= lim) return "";
  return `<div class="pager"><button ${off <= 0 ? "disabled" : ""} data-off="${Math.max(0, off - lim)}">← Previous</button>
    <span class="muted small">${fmtN(off + 1)}–${fmtN(Math.min(off + lim, total))} of ${fmtN(total)} ${noun}</span>
    <button ${off + lim >= total ? "disabled" : ""} data-off="${off + lim}">Next →</button></div>`;
}
function renderItemCard(row) {
  const it = row.item || {};
  const groups = {};
  row.proposals.forEach(p => { if (p.group_id) (groups[p.group_id] = groups[p.group_id] || []).push(p.id); });
  let lastGroup = null;
  const perField = {};
  row.proposals.forEach(p => { if (["field", "creators"].includes(p.kind) && ["pending", "accepted", "superseded"].includes(p.status)) perField[p.field] = (perField[p.field] || 0) + 1; });
  const body = row.proposals.map(p => {
    if (perField[p.field] > 1 && !p._alt) p.note = (p.note ? p.note + " · " : "") + `one of ${perField[p.field]} alternatives for this field (accepting one sets the others aside)`;
    let g = "";
    if (p.group_id && p.group_id !== lastGroup && groups[p.group_id].length > 1) {
      g = `<div class="group">These ${groups[p.group_id].length} changes belong together (${esc(p.group_id.startsWith("pub-") ? "switch to the published version" : p.group_id.startsWith("match-") ? "matched by search" : p.group_id.startsWith("type-") ? "item type change" : "group")}).
        <button class="small ok" data-gacc="${esc(groups[p.group_id].join(","))}">Accept group</button> <button class="small" data-grej="${esc(groups[p.group_id].join(","))}">Reject group</button></div>`;
    }
    lastGroup = p.group_id;
    return g + renderProp(p);
  }).join("");
  const parent = it.parent ? `<div class="m">attachment of <a href="#" data-item="${esc(it.parent.key)}">${esc(it.parent.title_plain)}</a></div>` : "";
  const issues = (row.issues || []).map(i => `<span class="chip ${i.severity === "error" ? "err" : i.severity === "warning" ? "warn" : "info"}" title="${esc(i.message)}">${esc(issueLabel(i.code))}</span>`).join(" ");
  return `<div class="icard" data-key="${esc(it.key)}">
    <div class="ihead"><div style="flex:1;min-width:0">
      <div class="t">${esc(it.title_plain || "(untitled)")}</div>
      <div class="m">${esc([it.creators, it.year, it.itemType, it.publication].filter(Boolean).join(" · "))} · <span class="mono">${esc(it.key)}</span></div>${parent}
      ${issues ? `<div style="margin-top:4px">${issues}</div>` : ""}</div>
      <div class="row small"><a href="#" data-item="${esc(it.key)}">Details</a>${zlink(it)}
      ${row.proposals.some(p => p.status === "pending") ? `<button class="small ok" data-iacc="${esc(row.proposals.filter(p => p.status === "pending").map(p => p.id).join(","))}" title="Accept all pending changes of this item (A)">Accept item</button>` : ""}</div></div>
    ${body}</div>`;
}
function statusChip(p) {
  const m = {accepted: "ok", rejected: "", applied: "acc", stale: "warn", conflict: "err", failed: "err", superseded: "", rolled_back: "warn"};
  return p.status === "pending" ? "" : `<span class="chip ${m[p.status] || ""}" title="${esc(p.message || "")}">${esc(p.status)}${p.edited != null ? " · edited" : ""}</span>`;
}
function renderProp(p) {
  const rules = (p.rules || []).map(r => `<span class="chip">${esc(S.ruleLabels[r] || r)}</span>`).join(" ");
  const src = p.source !== "rules" ? `<span class="chip acc">${esc(p.source)}</span>` : "";
  const cb = confBucket(p.confidence);
  const canAct = ["pending", "accepted", "rejected", "stale", "superseded"].includes(p.status);
  const editable = ["field", "rename_file", "creators", "tags"].includes(p.kind) && canAct;
  return `<div class="prop st-${esc(p.status)}" data-id="${p.id}" data-kind="${esc(p.kind)}" tabindex="-1">
    <div class="f">${esc(fieldLabel(p.field))}<small><span class="conf ${cb}" title="confidence ${Math.round(p.confidence * 100)}%"></span> ${Math.round(p.confidence * 100)}% ${src}</small></div>
    <div><div class="diff">${renderChange(p)}</div>
      <div class="note">${rules} ${p.note ? esc(p.note) : ""} ${statusChip(p)} ${p.message ? `<span class="faint">${esc(p.message)}</span>` : ""}
      ${(p.latex_problems || []).map(x => `<span class="chip err">${esc(x)}</span>`).join(" ")}</div>
      <div class="editor-slot"></div></div>
    <div class="acts">${canAct ? `
      <button class="iconbtn ${p.status === "accepted" ? "primary" : ""}" data-act="accepted" title="Accept (a)">✓</button>
      <button class="iconbtn" data-act="rejected" title="Reject (r)">✕</button>
      ${editable ? `<button class="iconbtn" data-act="edit" title="Edit (e)">✎</button>` : ""}
      ${p.status !== "pending" ? `<button class="iconbtn" data-act="pending" title="Undo (u)">↺</button>` : ""}` : ""}</div>
  </div>`;
}
function renderChange(p) {
  if (p.kind === "field" || p.kind === "rename_file") {
    const oldP = p.old_plain ?? "", newP = p.new_plain ?? "";
    const oldR = String(p.old ?? ""), newR = String(p.value ?? "");
    const encodingOnly = oldP === newP && oldR !== newR;
    const useRaw = S.showRaw || encodingOnly || p.kind === "rename_file" || ["DOI", "url", "extra", "pages"].includes(p.field);
    const d = useRaw ? diffWords(oldR, newR) : diffWords(oldP, newP);
    let h = `<span class="old"><span class="lbl">now</span>${d.old || '<span class="faint">(empty)</span>'}</span>
      <span class="new"><span class="lbl">new</span>${d.new || '<span class="faint">(empty)</span>'}</span>`;
    if (oldR === newR) h += `<div class="small" style="color:var(--warn)">No change: this value is identical to the current one${p.edited != null ? " (your edit restores it)" : ""}. Reject it, or edit it.</div>`;
    else if (encodingOnly) h += `<div class="faint small">Same text, different encoding (LaTeX/whitespace).</div>`;
    if (p.kind === "rename_file" && p.edited == null) h += `<div class="faint small">The name is recomputed from the final metadata when applied (edit it to fix it).</div>`;
    return h;
  }
  if (p.kind === "creators") return renderCreators(p.old_names || [], p.new_names || [], p.old || [], p.value || []);
  if (p.kind === "tags") {
    const o = new Set((p.old || []).map(t => t.tag)), n = new Set((p.value || []).map(t => t.tag));
    const rem = [...o].filter(x => !n.has(x)), add = [...n].filter(x => !o.has(x));
    return `${rem.map(t => `<del>${esc(t)}</del>`).join(" ")} ${add.map(t => `<ins>${esc(t)}</ins>`).join(" ")}`;
  }
  if (p.kind === "itemType") {
    const n = p.value || {};
    const dropped = Object.keys(n.dropped || {});
    const set = Object.entries(n.set || {}).map(([k, v]) => `${fieldLabel(k)} = ${esc(v)}`).join(", ");
    return `<span class="old"><span class="lbl">now</span>${esc(p.old)}</span><span class="new"><span class="lbl">new</span><ins>${esc(n.itemType)}</ins></span>
      ${set ? `<div class="small muted">also sets ${set}</div>` : ""}
      ${dropped.length ? `<div class="small" style="color:var(--warn)">Fields without a place in ${esc(n.itemType)} are kept in Extra: ${esc(dropped.join(", "))}</div>` : ""}`;
  }
  if (p.kind === "attach_pdf") {
    const v = p.value || {};
    const mb = v.size ? (v.size / 1048576).toFixed(1) + " MB" : "";
    return `<div>Attach <b>${esc(v.filename)}</b> — <a href="/api/staged/${p.id}?token=${TOKEN}" target="_blank" rel="noopener">open the downloaded PDF ↗</a></div>
      <div class="small muted">${esc(v.version_label || "")} · from ${esc(v.source)} (${esc(v.host)})${v.license ? " · licence " + esc(v.license) : ""}${v.pages ? " · " + v.pages + " pages" : ""}${mb ? " · " + mb : ""}</div>
      <div class="small ${v.score >= 90 ? "" : "muted"}">Check: ${esc(v.evidence)}</div>
      ${v.version === "submittedVersion" ? `<div class="small" style="color:var(--warn)">This is the preprint of a published article; its content may differ from the published version.</div>` : ""}`;
  }
  if (p.kind === "create_parent") {
    const d = (p.value || {}).data || {};
    const cr = (d.creators || []).map(c => c.lastName || c.name).slice(0, 4).join(", ");
    return `<div>Create a new <b>${esc(d.itemType)}</b> and attach this PDF to it:</div>
      <div class="small"><b>${esc(d.title)}</b><br>${esc(cr)} · ${esc(d.date || "")} · ${esc(d.publicationTitle || "")} · ${esc(d.DOI || "")}</div>`;
  }
  return esc(JSON.stringify(p.value));
}
function crName(c) { return c ? (c.name != null ? c.name : `${c.lastName || ""}${c.firstName ? ", " + c.firstName : ""}`) : ""; }
function renderCreators(oldN, newN, oldR, newR) {
  const rows = Math.max(oldN.length, newN.length);
  let h = `<table class="cr"><tr><th>now</th><th>new</th></tr>`;
  for (let i = 0; i < rows; i++) {
    const d = diffWords(oldN[i] || "", newN[i] || "");
    let n = d.new;
    if ((oldN[i] || "") === (newN[i] || "") && JSON.stringify(oldR[i]) !== JSON.stringify(newR[i])) n += ` <span class="faint small">(encoding)</span>`;
    h += `<tr><td>${d.old}</td><td>${n}</td></tr>`;
  }
  h += `</table>`;
  if (S.showRaw) h += `<div class="raw">${esc(newR.map(crName).join("; "))}</div>`;
  return h;
}
function bindReview(root) {
  root.onclick = async e => {
    const it = e.target.closest("[data-item]");
    if (it) { e.preventDefault(); openItem(it.dataset.item); return; }
    const ga = e.target.closest("[data-gacc],[data-grej],[data-iacc]");
    if (ga) {
      const ids = (ga.dataset.gacc || ga.dataset.grej || ga.dataset.iacc).split(",").map(Number);
      await setStatus(ids, ga.dataset.grej ? "rejected" : "accepted"); return;
    }
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const pr = b.closest(".prop"), id = +pr.dataset.id;
    S.focus = $$(".prop").indexOf(pr);
    if (b.dataset.act === "edit") return openEditor(pr);
    await setStatus([id], b.dataset.act);
  };
}
async function setStatus(ids, status, advance = false) {
  try {
    const r = await post("/api/proposals/set", {ids, status});
    r.proposals.forEach(p => {
      const el = $(`.prop[data-id="${p.id}"]`);
      if (el) { const n = document.createElement("div"); n.innerHTML = renderProp(p); el.replaceWith(n.firstElementChild); }
    });
    if (ids.length > 1 || status === "accepted") {
      // superseded siblings may have changed: refresh their status lazily
      const keys = new Set(r.proposals.map(p => p.item_key));
      keys.forEach(k => refreshCard(k));
    }
    if (advance) setFocus(S.focus + 1);
    else setFocus(S.focus, false);
    refreshStatus();
  } catch (e) { toast(e.message, true); }
}
async function refreshCard(key) {
  const d = await api("/api/proposals?" + qs({...reviewFilter(), status: "all", q: key, limit: 1}));
  const row = d.items.find(r => r.item.key === key);
  const card = $(`.icard[data-key="${CSS.escape(key)}"]`);
  if (!row || !card) return;
  const ids = new Set($$(".prop", card).map(p => +p.dataset.id));
  row.proposals = row.proposals.filter(p => ids.has(p.id));
  const n = document.createElement("div"); n.innerHTML = renderItemCard(row);
  card.replaceWith(n.firstElementChild);
  setFocus(S.focus, false);
}
function setFocus(i, scroll = true) {
  const ps = $$(".prop");
  if (!ps.length) return;
  i = Math.max(0, Math.min(ps.length - 1, i));
  ps.forEach(p => p.classList.remove("focus"));
  ps[i].classList.add("focus");
  S.focus = i;
  if (scroll) ps[i].scrollIntoView({block: "center", behavior: "smooth"});
}
document.addEventListener("keydown", e => {
  if (S.view !== "review" || e.target.closest("input,textarea,select") || !$("#modal").classList.contains("hidden") || e.metaKey || e.ctrlKey) return;
  const ps = $$(".prop"); const cur = ps[S.focus];
  if (e.key === "j") { setFocus(S.focus + 1); e.preventDefault(); }
  else if (e.key === "k") { setFocus(S.focus - 1); e.preventDefault(); }
  else if (!cur) return;
  else if (e.key === "a") setStatus([+cur.dataset.id], "accepted", true);
  else if (e.key === "r") setStatus([+cur.dataset.id], "rejected", true);
  else if (e.key === "u") setStatus([+cur.dataset.id], "pending");
  else if (e.key === "e") { e.preventDefault(); openEditor(cur); }
  else if (e.key === "A") { const ids = $$(".prop", cur.closest(".icard")).filter(p => p.classList.contains("st-pending")).map(p => +p.dataset.id); if (ids.length) setStatus(ids, "accepted"); }
});

async function openEditor(pr) {
  const id = +pr.dataset.id, kind = pr.dataset.kind;
  const slot = $(".editor-slot", pr);
  if (slot.innerHTML) { slot.innerHTML = ""; return; }
  const d = await api("/api/proposals?" + qs({status: "all", q: pr.closest(".icard").dataset.key, limit: 1}));
  const p = d.items[0].proposals.find(x => x.id === id);
  if (kind === "field" || kind === "rename_file") {
    slot.innerHTML = `<div class="editor"><textarea rows="${Math.min(8, Math.max(2, Math.ceil(String(p.value).length / 90)))}">${esc(p.value)}</textarea>
      <div class="preview"></div><div class="row"><button class="small primary" data-save>Save &amp; accept</button><button class="small" data-cancel>Cancel</button><span class="faint small">Ctrl/⌘+Enter saves</span></div></div>`;
    const ta = $("textarea", slot), pv = $(".preview", slot);
    const prev = async () => { if (kind !== "field") return; const r = await post("/api/tools/latex", {text: ta.value});
      pv.innerHTML = `Renders as: <b>${esc(r.unicode)}</b>${r.problems.length ? ` <span class="chip err">${esc(r.problems.join("; "))}</span>` : ""}`; };
    let t; ta.oninput = () => { clearTimeout(t); t = setTimeout(prev, 250); }; prev();
    ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
    const save = async () => { const r = await post("/api/proposals/set", {ids: [id], edited: ta.value, status: "accepted"});
      if (r.kept_current) toast("Same as the current value — nothing to change, the proposal is set aside (rejected)."); refreshCard(pr.closest(".icard").dataset.key); refreshStatus(); };
    ta.onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") slot.innerHTML = ""; };
    $("[data-save]", slot).onclick = save; $("[data-cancel]", slot).onclick = () => slot.innerHTML = "";
  } else if (kind === "creators") {
    const rows = (p.value || []).map(c => ({...c}));
    const draw = () => {
      slot.innerHTML = `<div class="editor"><table class="cr"><tr><th>type</th><th>last name / single field</th><th>first name</th><th></th></tr>
        ${rows.map((c, i) => `<tr><td><input type="text" data-i="${i}" data-k="creatorType" value="${esc(c.creatorType)}" style="width:95px"></td>
          <td><input type="text" data-i="${i}" data-k="${c.name != null ? "name" : "lastName"}" value="${esc(c.name != null ? c.name : c.lastName)}" style="width:100%"></td>
          <td>${c.name != null ? `<button class="small ghost" data-split="${i}">two fields</button>` : `<input type="text" data-i="${i}" data-k="firstName" value="${esc(c.firstName)}" style="width:100%">`}</td>
          <td><button class="small ghost" data-up="${i}" title="Move up">↑</button><button class="small ghost" data-del="${i}" title="Remove">✕</button></td></tr>`).join("")}</table>
        <div class="row"><button class="small" data-add>Add creator</button><span class="spacer"></span><button class="small primary" data-save>Save &amp; accept</button><button class="small" data-cancel>Cancel</button></div></div>`;
      $$("input", slot).forEach(inp => inp.oninput = () => rows[+inp.dataset.i][inp.dataset.k] = inp.value);
      $$("[data-del]", slot).forEach(b => b.onclick = () => { rows.splice(+b.dataset.del, 1); draw(); });
      $$("[data-up]", slot).forEach(b => b.onclick = () => { const i = +b.dataset.up; if (i > 0) { [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; draw(); } });
      $$("[data-split]", slot).forEach(b => b.onclick = () => { const i = +b.dataset.split; const n = rows[i].name; const parts = n.includes(",") ? n.split(",") : [n.split(" ").slice(-1)[0], n.split(" ").slice(0, -1).join(" ")];
        rows[i] = {creatorType: rows[i].creatorType, lastName: parts[0].trim(), firstName: (parts[1] || "").trim()}; draw(); });
      $("[data-add]", slot).onclick = () => { rows.push({creatorType: "author", lastName: "", firstName: ""}); draw(); };
      $("[data-save]", slot).onclick = async () => { await post("/api/proposals/set", {ids: [id], edited: rows, status: "accepted"}); refreshCard(pr.closest(".icard").dataset.key); refreshStatus(); };
      $("[data-cancel]", slot).onclick = () => slot.innerHTML = "";
    };
    draw();
  } else if (kind === "tags") {
    slot.innerHTML = `<div class="editor"><input type="text" value="${esc((p.value || []).map(t => t.tag).join("; "))}" style="width:100%">
      <div class="faint small">Separate tags with “;”.</div><div class="row"><button class="small primary" data-save>Save &amp; accept</button><button class="small" data-cancel>Cancel</button></div></div>`;
    $("[data-save]", slot).onclick = async () => {
      const types = Object.fromEntries((p.value || []).map(t => [t.tag, t.type]));
      const tags = $("input", slot).value.split(";").map(s => s.trim()).filter(Boolean).map(t => types[t] ? {tag: t, type: types[t]} : {tag: t});
      await post("/api/proposals/set", {ids: [id], edited: tags, status: "accepted"}); refreshCard(pr.closest(".icard").dataset.key); refreshStatus();
    };
    $("[data-cancel]", slot).onclick = () => slot.innerHTML = "";
  }
}

/* ---------------------------------------------------------------- item drawer */
async function openItem(key) {
  const dr = $("#drawer");
  dr.classList.remove("hidden"); $(".shell").classList.add("with-drawer");
  dr.innerHTML = `<div class="empty">Loading …</div>`;
  const d = await api(`/api/items/${encodeURIComponent(key)}`);
  const it = d.item;
  const fields = Object.entries(d.data).filter(([k, v]) => typeof v === "string" && v && !["key", "dateAdded", "dateModified"].includes(k));
  dr.innerHTML = `<div class="row"><b style="flex:1">${esc(it.title_plain)}</b><button class="ghost iconbtn" id="dclose">✕</button></div>
    <div class="muted small" style="margin-bottom:10px">${esc(it.itemType)} · <span class="mono">${esc(it.key)}</span> · ${zlink(it)}</div>
    <div class="kv">${fields.map(([k, v]) => `<div class="k">${esc(fieldLabel(k))}</div><div class="v">${esc(d.plain[k] ?? v)}${d.plain[k] !== undefined && d.plain[k] !== v ? `<div class="raw">${esc(v)}</div>` : ""}</div>`).join("")}
      <div class="k">Creators</div><div class="v">${(d.data.creators || []).map(c => esc(crName(c)) + ` <span class="faint">(${esc(c.creatorType)})</span>`).join("<br>") || "—"}</div>
      <div class="k">Tags</div><div class="v">${(d.data.tags || []).map(t => `<span class="chip">${esc(t.tag)}</span>`).join(" ") || "—"}</div></div>
    ${d.issues.length ? `<h3>Issues</h3>${d.issues.map(i => `<div class="small">${sevChip(i.severity)} ${esc(i.message)} ${i.status === "ignored" ? '<span class="faint">(ignored)</span>' : ""}</div>`).join("")}` : ""}
    ${d.attachments.length ? `<h3>Attachments</h3>${d.attachments.map(a => `<div class="small" style="margin-bottom:6px"><b>${esc(a.filename || a.title_plain)}</b> <span class="faint">${esc(a.linkMode || "")}</span>
       ${a.pdf && a.pdf.att_key ? `<div class="faint">${a.pdf.exists_ ? "on disk" : a.pdf.exists_ === 0 ? '<span style="color:var(--err)">file missing</span>' : ""} ${a.pdf.pages ? "· " + a.pdf.pages + " pages" : ""} ${a.pdf.has_text === 0 ? "· no text layer" : ""} ${a.pdf.found_doi ? "· DOI in PDF: " + esc(a.pdf.found_doi) : ""}</div>` : ""}</div>`).join("")}` : ""}
    <h3>Propose an edit</h3>
    <div class="editor"><select id="pe-f">${d.fields.filter(f => f !== "creators").map(f => `<option value="${esc(f)}">${esc(fieldLabel(f))}</option>`).join("")}</select>
      <textarea id="pe-v" rows="3" placeholder="New value (LaTeX allowed)"></textarea><div class="preview" id="pe-p"></div>
      <div class="row"><button class="small primary" id="pe-save">Add as accepted change</button></div></div>
    ${d.proposals.length ? `<h3>Proposals for this item</h3>${d.proposals.map(p => `<div class="small">${esc(fieldLabel(p.field))}: ${esc(p.new_plain ?? JSON.stringify(p.value).slice(0, 80))} ${statusChip(p) || '<span class="chip">pending</span>'}</div>`).join("")}` : ""}`;
  $("#dclose").onclick = closeDrawer;
  const sel = $("#pe-f"), ta = $("#pe-v");
  const fill = () => ta.value = d.data[sel.value] || ""; sel.onchange = fill; fill();
  let t; ta.oninput = () => { clearTimeout(t); t = setTimeout(async () => { const r = await post("/api/tools/latex", {text: ta.value}); $("#pe-p").textContent = "Renders as: " + r.unicode; }, 250); };
  $("#pe-save").onclick = async () => { try { await post(`/api/items/${encodeURIComponent(key)}/propose`, {field: sel.value, value: ta.value}); toast("Change added (accepted)"); refreshStatus(); openItem(key); } catch (e) { toast(e.message, true); } };
}
function closeDrawer() { $("#drawer").classList.add("hidden"); $(".shell").classList.remove("with-drawer"); }
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeModal(); closeDrawer(); } });

/* ---------------------------------------------------------------- issues */
async function viewIssues(v, preset) {
  const f = preset ? {...S.issues, ...preset} : S.issues;
  const d = await api("/api/issues?" + qs({...f, limit: 50}));
  const codes = Object.entries(d.codes).filter(([c, x]) => preset ? x.source === "pdf" : x.source !== "pdf");
  const total = codes.reduce((a, [, x]) => a + x.n, 0);
  const isPdf = !!preset;
  v.innerHTML = `${isPdf ? "" : `<h1>Issues</h1><p class="sub">Problems that cannot be fixed automatically. Fix them in Zotero (use “Open in Zotero”), ignore them, or tag the items to find them later.</p>`}
  <div class="split">
    <div class="facets card" style="padding:8px">
      <button data-code="" class="${!f.code ? "on" : ""}"><span>All</span><span class="count">${fmtN(total)}</span></button>
      ${codes.map(([c, x]) => `<button data-code="${esc(c)}" class="${f.code === c ? "on" : ""}"><span>${esc(issueLabel(c))}</span><span class="count">${fmtN(x.n)}</span></button>`).join("")}
      <hr style="border:0;border-top:1px solid var(--border);width:100%">
      <button data-status="${f.status === "ignored" ? "open" : "ignored"}"><span>${f.status === "ignored" ? "← Open issues" : "Ignored issues"}</span></button>
    </div>
    <div>
      <div class="row" style="margin-bottom:10px">
        <input type="search" id="i-q" placeholder="Search" value="${esc(f.q || "")}" style="width:240px">
        <span class="spacer"></span>
        <span class="muted small">${fmtN(d.total)} shown</span>
        ${d.total ? `<button class="small" id="i-tag">Tag these items…</button>
        <a class="small" href="/api/issues/export.csv?${qs({code: f.code, status: f.status, q: f.q, source: f.source, token: TOKEN})}">Export CSV</a>
        ${f.status === "open" ? `<button class="small" id="i-ign">Ignore all ${fmtN(d.total)}</button>` : ""}` : ""}
      </div>
      ${d.rows.length ? `<table class="list"><tr><th style="width:36%">Item</th><th>Issue</th><th style="width:150px"></th></tr>
      ${d.rows.map(r => `<tr data-id="${r.id}"><td><a href="#" data-item="${esc(r.item_key)}">${esc(r.item.title_plain || r.item_key)}</a>
          <div class="faint small">${esc([r.item.creators, r.item.year, r.item.itemType].filter(Boolean).join(" · "))}</div></td>
        <td>${sevChip(r.severity)} <b>${esc(issueLabel(r.code))}</b><div class="small">${esc(r.message)}</div>
          ${r.links ? `<div class="small row" style="gap:10px;margin-top:3px">${Object.entries(r.links).map(([k, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc({doi: "Publisher (DOI)", arxiv_pdf: "arXiv PDF", unpaywall: "Unpaywall", scholar: "Google Scholar"}[k] || k)} ↗</a>`).join("")}</div>` : ""}
          ${r.details && r.details.their_title ? `<div class="small faint">DOI record: ${esc(r.details.their_title)} — ${esc((r.details.their_authors || []).join(", "))} ${esc(r.details.their_date || "")}</div>` : ""}
          ${r.details && r.details.path ? `<div class="mono faint" style="font-size:11px">${esc(r.details.path)}</div>` : ""}</td>
        <td class="small">${zlink(r.item)}${r.code === "missing_pdf" ? `<br><button class="small ghost" data-find="${esc(r.item_key)}">Find PDF online</button><br><button class="small ghost" data-own="${esc(r.item_key)}" title="Open it in your normal browser; the PDF you download is picked up from your Downloads folder">Get with my browser</button>` : ""}<br><button class="small ghost" data-ign="${r.id}">${r.status === "ignored" ? "Un-ignore" : "Ignore"}</button></td></tr>`).join("")}</table>` : `<div class="empty">No ${f.status} issues${f.code ? " of this kind" : ""}.</div>`}
      ${pager(d.offset, 50, d.total, "issues")}
    </div></div>`;
  const reload = () => isPdf ? render() : viewIssues(v);
  $$("[data-code]", v).forEach(b => b.onclick = () => { if (isPdf) S.pdfIssues = {...(S.pdfIssues || {}), code: b.dataset.code, offset: 0}; else S.issues = {...S.issues, code: b.dataset.code, offset: 0}; reload(); });
  $$("[data-status]", v).forEach(b => b.onclick = () => { if (isPdf) S.pdfIssues = {...(S.pdfIssues || {}), status: b.dataset.status, offset: 0}; else S.issues = {...S.issues, status: b.dataset.status, offset: 0}; reload(); });
  $$("[data-item]", v).forEach(a => a.onclick = e => { e.preventDefault(); openItem(a.dataset.item); });
  $$("[data-find]", v).forEach(b => b.onclick = () => startJob("find_pdfs", {keys: [b.dataset.find], refresh: true}));
  $$("[data-own]", v).forEach(b => b.onclick = () => startJob("own_browser", {keys: [b.dataset.own], minutes: 5}));
  $$("[data-ign]", v).forEach(b => b.onclick = async () => { await post("/api/issues/ignore", {ids: [+b.dataset.ign], ignore: f.status !== "ignored"}); b.closest("tr").remove(); refreshStatus(); });
  $$(".pager button", v).forEach(b => b.onclick = () => { if (isPdf) S.pdfIssues = {...(S.pdfIssues || {}), offset: +b.dataset.off}; else S.issues.offset = +b.dataset.off; reload(); });
  let t; const q = $("#i-q", v); if (q) q.oninput = () => { clearTimeout(t); t = setTimeout(() => { if (isPdf) S.pdfIssues = {...(S.pdfIssues || {}), q: q.value, offset: 0}; else S.issues = {...S.issues, q: q.value, offset: 0}; reload(); }, 350); };
  const filt = {code: f.code, status: f.status, q: f.q, source: f.source};
  if ($("#i-ign", v)) $("#i-ign", v).onclick = async () => {
    if (!await confirmBox(`Ignore ${fmtN(d.total)} issues?`, "They will not be reported again (you can un-ignore them later).", "Ignore")) return;
    const all = await api("/api/issues?" + qs({...filt, limit: 500}));
    let ids = all.rows.map(r => r.id);
    for (let off = 500; off < all.total; off += 500) ids = ids.concat((await api("/api/issues?" + qs({...filt, limit: 500, offset: off}))).rows.map(r => r.id));
    await post("/api/issues/ignore", {ids}); reload(); refreshStatus();
  };
  if ($("#i-tag", v)) $("#i-tag", v).onclick = () => {
    const def = (S.status && f.code && ["missing_pdf"].includes(f.code)) ? "_needs-pdf" : (f.source === "pdf" ? "_pdf-problem" : "_to-check");
    modal(`<h2 style="margin-top:0">Tag ${fmtN(d.total)} items</h2><p class="sub">Adds an (accepted) tag change to each item. After applying, filter by this tag in Zotero to work through them — e.g. select all items tagged “_needs-pdf” and use <i>Find Available PDF</i>, or add PDFs by drag &amp; drop.</p>
      <input type="text" id="tagname" value="${esc(def)}" style="width:100%"><div class="row" style="margin-top:12px"><span class="spacer"></span><button id="tno">Cancel</button><button class="primary" id="tyes">Add tag</button></div>`, c => {
      $("#tno", c).onclick = closeModal;
      $("#tyes", c).onclick = async () => { const r = await post("/api/issues/tag", {filter: filt, tag: $("#tagname", c).value}); closeModal(); toast(`${fmtN(r.count)} tag changes added — apply them from “Apply to Zotero”`); refreshStatus(); };
    });
  };
}

/* ---------------------------------------------------------------- PDFs */
async function viewPdfs(v) {
  await refreshStatus();
  const s = S.status, iss = s.issues || {}, pdf = s.pdf || {};
  const f = S.pdfIssues || (S.pdfIssues = {status: "open", offset: 0});
  const tile = (code, label, hint) => `<div class="tile link" data-code="${code}"><div class="label">${label}</div><div class="num">${fmtN(iss[code] || 0)}</div><div class="hint">${hint}</div></div>`;
  v.innerHTML = `<h1>PDFs</h1>
  <p class="sub">${fmtN(pdf.n || 0)} PDF attachments checked (${fmtN(pdf.present || 0)} on disk). For missing PDFs, <b>Find PDFs online</b> looks for legal open-access copies (Unpaywall, OpenAlex, Semantic Scholar, arXiv, Europe PMC, OAPEN, Open Library) using the DOI, arXiv id, ISBN, PubMed id or — as a last resort — the title, and — if you enable institutional access in Settings — the publisher's own PDF through your university's VPN; each download is checked (real PDF, right paper) and proposed in <a href="#review?kind=attach_pdf&status=pending">Review</a> — nothing is attached before you accept. File-name proposals are in <a href="#review?kind=rename_file&status=pending" id="ren">Review</a> too.</p>
  <div class="grid g4">
    ${tile("missing_pdf", "Missing PDF", "no usable PDF attached")}
    ${tile("pdf_file_missing", "File missing on disk", "attachment without its file")}
    ${tile("pdf_not_pdf", "Not a real PDF", "e.g. saved login pages")}
    ${tile("pdf_mismatch", "PDF ≠ item", "DOI/title on the PDF differ")}
    ${tile("orphan_pdf", "Standalone PDFs", "no parent item")}
    ${tile("pdf_no_text", "No text layer", "scanned — needs OCR")}
    ${tile("pdf_duplicate_file", "Identical files", "same PDF attached twice")}
    <div class="tile link" id="rename-tile"><div class="label">File names to normalise</div><div class="num">…</div><div class="hint">template in Settings</div></div>
  </div>
  <div class="row" style="margin:14px 0"><button class="primary" id="pdffind">Find missing PDFs online</button><button id="pdfscan">Re-check PDFs</button><span class="muted small">After adding PDFs in Zotero, run “Sync &amp; analyse” then re-check.</span></div>
  <div class="card" id="own-card" style="margin-bottom:14px"><h3 style="margin-top:0">Get PDFs with your own browser</h3>
    <p class="small muted" style="margin-top:0">Some publishers (e.g. ScienceDirect) show a captcha that keeps coming back in the automated browser, however often you solve it. Your normal browser is not affected. This opens the articles there (up to 25 tabs at a time); click “Download PDF” in each tab and the tool picks up the files from your Downloads folder (<span class="mono" id="own-dir">…</span>), matches each one to its item by DOI or title, and proposes it in Review. Your downloads are left untouched.</p>
    <div class="row"><button id="own-go">Open <span id="own-n">…</span> blocked item(s) &amp; watch Downloads</button><button id="own-pick" title="Match PDFs you downloaded in the last 24 hours against all items with a missing PDF">Pick up PDFs I already downloaded</button></div></div>
  <div id="pdf-issues"></div>`;
  api("/api/pdfs/needs-browser").then(r => {
    $("#own-dir").textContent = r.downloads; $("#own-n").textContent = fmtN(r.keys.length);
    $("#own-go").disabled = !r.keys.length;
  }).catch(() => {});
  $("#own-go", v).onclick = () => startJob("own_browser");
  $("#own-pick", v).onclick = async () => {
    const all = await api("/api/issues?" + qs({code: "missing_pdf", status: "open", source: "pdf", limit: 1}));
    startJob("own_browser", {open_tabs: false, lookback_hours: 24, all_missing: true});
    toast(`Checking recent downloads against ${fmtN(all.total)} item(s) with a missing PDF …`);
  };
  $("#pdfscan", v).onclick = () => startJob("pdfs");
  $("#pdffind", v).onclick = () => startJob("find_pdfs");
  $$("[data-code]", v).forEach(t => t.onclick = () => { S.pdfIssues = {...f, code: t.dataset.code, offset: 0}; viewIssues($("#pdf-issues"), {...S.pdfIssues, source: "pdf"}); });
  $("#rename-tile", v).onclick = () => location.hash = "#review?kind=rename_file&status=pending";
  $("#ren", v).onclick = e => { e.preventDefault(); location.hash = "#review?kind=rename_file&status=pending"; };
  api("/api/proposals/facets?status=pending").then(fc => { $("#rename-tile .num").textContent = fmtN((fc.kind || {}).rename_file || 0); });
  await viewIssues($("#pdf-issues"), {...f, source: "pdf"});
}

/* ---------------------------------------------------------------- duplicates */
async function viewDuplicates(v) {
  const f = S.dups;
  const d = await api("/api/duplicates?" + qs({status: f.status, offset: f.offset, limit: 20}));
  v.innerHTML = `<h1>Duplicates</h1>
  <p class="sub">Candidate groups (identical DOI/arXiv/ISBN or very similar titles with matching year and first author; “Part I/II”, errata and replies are excluded). Merging is best done in Zotero's <i>Duplicate Items</i> pane, which keeps notes, tags, collections and attachments: tag a group here, apply, then merge in Zotero.</p>
  <div class="row" style="margin-bottom:12px"><div class="seg">${[["open", "Open"], ["tagged", "Tagged"], ["dismissed", "Not duplicates"]].map(([k, l]) => `<button data-st="${k}" class="${f.status === k ? "on" : ""}">${l}</button>`).join("")}</div>
    <span class="muted small">${fmtN(d.total)} groups</span></div>
  ${d.groups.map(g => `<div class="card" style="margin-bottom:12px" data-g="${g.id}">
    <div class="row" style="margin-bottom:8px"><b>${Math.round(g.score)}%</b> ${g.reasons.map(r => `<span class="chip">${esc(r)}</span>`).join(" ")}<span class="spacer"></span>
      ${f.status !== "dismissed" ? `<button class="small" data-a="dismiss">Not duplicates</button>` : `<button class="small" data-a="reopen">Re-open</button>`}
      ${f.status === "open" ? `<button class="small primary" data-a="tag">Tag for merging</button>` : ""}</div>
    <div class="dupgrid" style="grid-template-columns:repeat(${g.members.length},minmax(0,1fr))">
    ${g.members.map(m => `<div class="dupm ${m.suggested_master ? "master" : ""}">
      ${m.suggested_master ? '<span class="chip ok">most complete</span> ' : ""}<b>${esc(m.title_plain)}</b>
      <div class="kv" style="margin-top:6px;grid-template-columns:80px 1fr"><div class="k">Authors</div><div>${esc(m.creators || "—")}</div>
      <div class="k">Year</div><div>${esc(m.year || "—")}</div><div class="k">Type</div><div>${esc(m.itemType)}</div>
      <div class="k">In</div><div>${esc(m.publication || "—")}</div><div class="k">DOI</div><div class="mono">${esc(m.doi || "—")}</div>
      <div class="k">Files</div><div>${m.n_pdf} PDF · ${m.n_attachments} attachments</div><div class="k">Added</div><div>${esc((m.dateAdded || "").slice(0, 10))}</div></div>
      <div class="small" style="margin-top:6px">${zlink(m)} · <a href="#" data-item="${esc(m.key)}">Details</a></div></div>`).join("")}</div></div>`).join("") || `<div class="empty">No ${f.status} duplicate groups.</div>`}
  ${pager(d.offset, 20, d.total, "groups")}`;
  $$("[data-st]", v).forEach(b => b.onclick = () => { S.dups = {status: b.dataset.st, offset: 0}; render(); });
  $$("[data-a]", v).forEach(b => b.onclick = async () => { await post(`/api/duplicates/${b.closest("[data-g]").dataset.g}`, {action: b.dataset.a});
    b.closest("[data-g]").remove(); toast(b.dataset.a === "tag" ? "Tag changes added (apply them to see the tag in Zotero)" : "Updated"); refreshStatus(); });
  $$("[data-item]", v).forEach(a => a.onclick = e => { e.preventDefault(); openItem(a.dataset.item); });
  $$(".pager button", v).forEach(b => b.onclick = () => { S.dups.offset = +b.dataset.off; render(); });
}

/* ---------------------------------------------------------------- apply */
async function viewApply(v) {
  const d = await api("/api/commit/preview");
  const s = d.summary, k = s.by_kind || {};
  const checks = [
    [!!(S.status && S.status.server_id), "Zotero is reachable", S.status && S.status.server_id ? "" : "Run a scan first."],
    [d.write_authorised, "Writes are authorised", d.write_authorised ? "" : `Requires Zotero 10 or later. <button class="small" id="auth">Authorise now</button> — in the Zotero dialog choose <b>Always Allow</b> (plain “Allow” is valid for one write only)`],
    [!d.db_backup || !!d.data_dir, "Backup", d.db_backup ? (d.data_dir ? `Full JSON export + copy of zotero.sqlite from <span class="mono">${esc(d.data_dir)}</span>` : "Zotero data directory unknown — set it in Settings (or run the PDF check to detect it).") : "JSON export only (database copy disabled in Settings)"],
  ];
  v.innerHTML = `<h1>Apply to Zotero</h1>
  <p class="sub">Writes the accepted changes. Each item is re-read right before writing; if a field changed in Zotero since it was reviewed, that change is skipped and reported as a conflict.</p>
  <div class="grid g4">
    <div class="tile"><div class="label">Accepted changes</div><div class="num" style="color:var(--accent)">${fmtN(s.proposals)}</div><div class="hint">on ${fmtN(s.items)} items/attachments</div></div>
    <div class="tile"><div class="label">Field edits</div><div class="num">${fmtN((k.field || 0) + (k.creators || 0) + (k.tags || 0))}</div><div class="hint">${fmtN(k.creators || 0)} author lists · ${fmtN(k.tags || 0)} tag changes</div></div>
    <div class="tile"><div class="label">Item type changes</div><div class="num">${fmtN(k.itemType || 0)}</div><div class="hint">${fmtN(k.create_parent || 0)} new parent items</div></div>
    <div class="tile"><div class="label">PDF files</div><div class="num">${fmtN((k.rename_file || 0) + (k.attach_pdf || 0))}</div><div class="hint">${fmtN(k.attach_pdf || 0)} to attach · ${fmtN(k.rename_file || 0)} renames</div></div>
  </div>
  <h2>Before applying</h2>
  <div class="card">${checks.map(([ok, t, h]) => `<div class="check"><span class="st ${ok ? "ok" : "bad"}">${ok ? "✓" : "!"}</span><div><b>${t}</b><div class="small muted">${h}</div></div></div>`).join("")}
    <div class="check"><span class="st warn">i</span><div><b>Zotero sync</b><div class="small muted">If you sync with zotero.org, the changes will propagate to your other devices after the next sync. Roll-back also works through sync.</div></div></div></div>
  <div class="row" style="margin:18px 0"><button class="primary big" id="go" ${s.proposals && d.write_authorised ? "" : "disabled"}>Back up &amp; apply ${fmtN(s.proposals)} changes</button>
    <a href="#review?status=accepted">Review accepted changes</a></div>
  ${d.sample.length ? `<h2>Accepted changes ${d.sample.length < s.proposals ? `(first ${d.sample.length})` : ""}</h2>
  <table class="list"><tr><th>Item</th><th>Field</th><th>New value</th></tr>${d.sample.map(p => `<tr><td>${esc((p.item && p.item.title_plain) || p.item_key)}</td><td>${esc(fieldLabel(p.field))}</td>
    <td>${esc(p.new_plain ?? (p.kind === "creators" ? (p.value || []).map(crName).join("; ") : p.kind === "tags" ? (p.value || []).map(t => t.tag).join("; ") : p.kind === "itemType" ? p.value.itemType : p.kind === "create_parent" ? "new parent: " + ((p.value.data || {}).title || "") : p.kind === "attach_pdf" ? "attach " + (p.value.filename || "PDF") + " (" + (p.value.source || "") + ")" : JSON.stringify(p.value)))}</td></tr>`).join("")}</table>` : ""}`;
  if ($("#auth", v)) $("#auth", v).onclick = async () => { toast("Confirm the dialog in Zotero …"); try { const r = await post("/api/authorize"); if (r.remember) toast("Writes authorised"); else toast("You clicked “Allow”, which is valid for a single write. Click “Authorise now” again and choose “Always Allow”.", true); await refreshStatus(); render(); } catch (e) { toast(e.message, true); } };
  $("#go", v).onclick = async () => {
    if (!await confirmBox(`Apply ${fmtN(s.proposals)} changes?`, "A backup is created first (library JSON export" + (d.db_backup ? " + copy of zotero.sqlite" : "") + "). You can roll back from History.", "Back up & apply")) return;
    startJob("commit");
  };
}

/* ---------------------------------------------------------------- history */
async function viewHistory(v) {
  const cs = await api("/api/commits");
  v.innerHTML = `<h1>History &amp; roll-back</h1><p class="sub">Every commit records the values before and after. Roll-back reverts through the Zotero API; fields changed again since are left alone and reported.</p>
  ${cs.length ? `<table class="list"><tr><th>#</th><th>When</th><th>Kind</th><th>Status</th><th>Result</th><th>Backup</th><th></th></tr>
  ${cs.map(c => `<tr><td>${c.id}</td><td>${esc(new Date(c.ts).toLocaleString())}</td><td>${esc(c.kind)}${c.parent ? ` of #${c.parent}` : ""}</td>
    <td><span class="chip ${c.status === "done" ? "ok" : c.status === "running" ? "acc" : "warn"}">${esc(c.status)}</span></td>
    <td class="small">${Object.entries(c.summary || {}).filter(([k]) => !["backup", "error"].includes(k)).map(([k, n]) => `${esc(k)}: ${fmtN(n)}`).join(" · ")}${c.summary && c.summary.error ? `<div style="color:var(--err)">${esc(c.summary.error)}</div>` : ""}</td>
    <td class="mono faint" style="font-size:11px">${esc((c.backup_dir || "").split(/[\\/]/).slice(-1)[0])}</td>
    <td><button class="small" data-show="${c.id}">Details</button> ${c.kind === "commit" && c.status !== "running" ? (c.rolled_back ? `<span class="chip warn">rolled back</span>` : `<button class="small danger" data-rb="${c.id}">Roll back</button>`) : ""}</td></tr>`).join("")}</table>` : `<div class="empty">No commits yet.</div>`}
  <div id="cdetail"></div>`;
  $$("[data-rb]", v).forEach(b => b.onclick = async () => {
    if (!await confirmBox(`Roll back commit #${b.dataset.rb}?`, "The previous values are written back (after a new backup). Items changed since the commit are skipped for those fields.", "Roll back", true)) return;
    startJob("rollback", {commit_id: +b.dataset.rb});
  });
  $$("[data-show]", v).forEach(b => b.onclick = async () => {
    const rows = await api(`/api/commits/${b.dataset.show}`);
    const fmt = x => typeof x === "string" ? x : Array.isArray(x) ? x.map(y => y.tag || crName(y)).join("; ") : JSON.stringify(x);
    $("#cdetail").innerHTML = `<h2>Commit #${b.dataset.show}</h2><table class="list"><tr><th>Item</th><th>Action</th><th>Before</th><th>After</th><th>Status</th></tr>
      ${rows.map(r => `<tr><td>${esc((r.item && r.item.title_plain) || r.item_key)}</td><td>${esc(r.action)}</td>
      <td class="small">${Object.entries(r.before || {}).map(([k, x]) => `<b>${esc(fieldLabel(k))}</b>: ${esc(fmt(x))}`).join("<br>")}</td>
      <td class="small">${Object.entries(r.after || {}).map(([k, x]) => `<b>${esc(fieldLabel(k))}</b>: ${esc(fmt(x))}`).join("<br>")}</td>
      <td><span class="chip ${r.status === "applied" ? "ok" : r.status === "rolled_back" ? "warn" : "err"}">${esc(r.status)}</span><div class="small faint">${esc(r.message || "")}</div></td></tr>`).join("")}</table>`;
    $("#cdetail").scrollIntoView({behavior: "smooth"});
  });
}

/* ---------------------------------------------------------------- settings */
async function viewSettings(v) {
  const d = await api("/api/settings");
  const s = d.settings;
  const types = d.item_types.length ? d.item_types.filter(t => !["attachment", "note", "annotation"].includes(t)) : s.pdf.types_needing_pdf;
  const LATEX_FIELDS = ["title", "shortTitle", "publicationTitle", "journalAbbreviation", "publisher", "place", "series", "seriesTitle", "conferenceName", "edition", "section", "meetingName", "university", "institution", "creators"];
  v.innerHTML = `<h1>Settings</h1><p class="sub">Changes to rules take effect on the next analysis (Dashboard → Re-analyse). Existing decisions are kept.</p>
  <div class="card"><h2 style="margin-top:0">Zotero</h2><div class="form">
    <label class="l">Local API address</label><input type="text" id="s-api" value="${esc(s.zotero_api)}">
    <label class="l">Library</label><input type="text" id="s-lib" value="${esc(s.library)}"><div class="help">users/0 = your personal library; groups/&lt;id&gt; for a group library.</div>
    <label class="l">Zotero data directory</label><input type="text" id="s-dd" value="${esc(s.data_dir)}" placeholder="auto-detected: ${esc((S.status && S.status.data_dir) || "not yet")}"><div class="help">Zotero → Settings → Advanced → Files and Folders. Needed for the database backup and for PDF checks.</div>
    <label class="l">Connection</label><div class="row"><button class="small" id="s-reset">Reset connection</button><button class="small" id="s-forget">Forget write key</button></div>
  </div></div>
  <div class="card" style="margin-top:12px"><h2 style="margin-top:0">LaTeX</h2><div class="form">
    <label class="l">Store special characters as LaTeX</label><label><input type="checkbox" id="s-lx" ${s.latex.enabled ? "checked" : ""}> Enabled</label>
    <label class="l">Accent style</label><select id="s-lxs"><option value="braced" ${s.latex.style === "braced" ? "selected" : ""}>{\\"o} (BibTeX-safe)</option><option value="arg" ${s.latex.style === "arg" ? "selected" : ""}>\\"{o}</option></select>
    <label class="l">Escape &amp; % # _</label><label><input type="checkbox" id="s-lxe" ${s.latex.escape_specials ? "checked" : ""}> Escape in text mode</label>
    <label class="l">LaTeX fields</label><div class="checks">${LATEX_FIELDS.map(f => `<label><input type="checkbox" class="lxf" value="${f}" ${s.latex.fields.includes(f) ? "checked" : ""}> ${esc(fieldLabel(f))}</label>`).join("")}
      <label><input type="checkbox" id="s-lxa" ${s.latex.include_abstract ? "checked" : ""}> Abstract</label></div>
    <div class="help">Type-specific fields follow their base field (Proceedings/Book title → Publication, University/Institution → Publisher).</div>
    <label class="l">Try it</label><div><input type="text" id="s-try" placeholder="Schrödinger — α-helix & CO₂" style="width:100%"><div class="small muted" id="s-tryout"></div></div>
  </div></div>
  <div class="card" style="margin-top:12px"><h2 style="margin-top:0">Rules</h2><div class="form">
    <label class="l">Enabled rules</label><div class="checks">${Object.entries(d.rules).map(([k, r]) => `<label title="${esc(r.description)}"><input type="checkbox" class="rule" value="${k}" ${s.rules[k] !== false ? "checked" : ""}> ${esc(r.label)}</label>`).join("")}</div>
    <label class="l">Title case policy</label><select id="s-tc"><option value="keep" ${s.title_case === "keep" ? "selected" : ""}>Keep as is (only fix ALL CAPS)</option><option value="sentence" ${s.title_case === "sentence" ? "selected" : ""}>Sentence case (Zotero recommendation)</option><option value="title" ${s.title_case === "title" ? "selected" : ""}>Title Case</option></select>
    <label class="l">Extra acronyms to preserve</label><input type="text" id="s-acr" value="${esc((s.acronyms || []).join(", "))}" placeholder="e.g. EPFL, QCD, LIGO">
    <label class="l">Page range separator</label><select id="s-ps"><option value="--" ${s.pages_separator === "--" ? "selected" : ""}>12--19 (LaTeX en dash)</option><option value="-" ${s.pages_separator === "-" ? "selected" : ""}>12-19</option><option value="–" ${s.pages_separator === "–" ? "selected" : ""}>12–19</option></select>
    <label class="l">Library proxy host suffixes</label><input type="text" id="s-px" value="${esc((s.proxy_suffixes || []).join(", "))}" placeholder="e.g. proxy.library.edu, ezproxy.myuni.ch"><div class="help">URLs like www-nature-com.proxy.library.edu are turned back into www.nature.com.</div>
    <label class="l">Duplicate threshold</label><input type="number" id="s-dt" min="80" max="100" value="${esc(s.duplicates.threshold)}">
  </div></div>
  <div class="card" style="margin-top:12px"><h2 style="margin-top:0">Online lookups</h2><div class="form">
    <label class="l">Your e-mail (Crossref polite pool)</label><input type="email" id="s-mail" value="${esc(s.mailto)}" placeholder="you@example.org"><div class="help">Sent only to Crossref/doi.org/arXiv as contact information, as they request.</div>
    <label class="l">Sources</label><div class="checks"><label><input type="checkbox" id="s-cr" ${s.lookups.crossref ? "checked" : ""}> Crossref</label><label><input type="checkbox" id="s-cn" ${s.lookups.doi_content_negotiation ? "checked" : ""}> doi.org (DataCite etc.)</label><label><input type="checkbox" id="s-ax" ${s.lookups.arxiv ? "checked" : ""}> arXiv</label><label><input type="checkbox" id="s-sm" ${s.lookups.search_missing_doi ? "checked" : ""}> Search DOIs for items without one</label></div>
    <label class="l">Minimum search match score</label><input type="number" id="s-ms" min="80" max="100" value="${esc(s.lookups.min_match_score)}">
    <label class="l">Requests per second</label><input type="number" id="s-rps" min="1" max="20" value="${esc(s.lookups.requests_per_second)}">
  </div></div>
  <div class="card" style="margin-top:12px"><h2 style="margin-top:0">PDFs</h2><div class="form">
    <label class="l">Item types that need a PDF</label><div class="checks">${types.map(t => `<label><input type="checkbox" class="pt" value="${t}" ${s.pdf.types_needing_pdf.includes(t) ? "checked" : ""}> ${esc(t)}</label>`).join("")}</div>
    <label class="l">File name template</label><input type="text" id="s-tpl" value="${esc(s.pdf.filename_template)}">
    <div class="help">Tokens: {firstCreator} {authors} {year} {title} {shortTitle} {journal} {journalAbbrev} {citekey} {doi} {key} {itemType}. Empty tokens drop their separator.</div>
    <label class="l">Missing PDF is fine for</label><div class="checks">${types.map(t => `<label><input type="checkbox" class="po" value="${t}" ${(s.pdf.types_optional_pdf || []).includes(t) ? "checked" : ""}> ${esc(t)}</label>`).join("")}</div>
    <div class="help">For these types a missing PDF is only reported as info (e.g. printed books you own).</div>
    <label class="l">Title words / max length</label><div class="row"><input type="number" id="s-tw" min="3" max="40" value="${esc(s.pdf.max_title_words)}" style="width:80px"><input type="number" id="s-ml" min="40" max="240" value="${esc(s.pdf.max_length)}" style="width:80px"><label><input type="checkbox" id="s-ascii" ${s.pdf.ascii_filenames ? "checked" : ""}> ASCII only</label></div>
    <label class="l">Preview</label><div id="s-prev" class="small mono muted">…</div>
    <label class="l">Find PDFs online from</label><div class="checks"><label><input type="checkbox" id="s-fu" ${s.pdf.find_unpaywall !== false ? "checked" : ""}> Unpaywall (uses your e-mail)</label><label><input type="checkbox" id="s-fo" ${s.pdf.find_openalex !== false ? "checked" : ""}> OpenAlex</label><label><input type="checkbox" id="s-fs" ${s.pdf.find_semanticscholar !== false ? "checked" : ""}> Semantic Scholar</label><label><input type="checkbox" id="s-fa" ${s.pdf.find_arxiv !== false ? "checked" : ""}> arXiv</label><label><input type="checkbox" id="s-fe" ${s.pdf.find_europepmc !== false ? "checked" : ""}> Europe PMC</label><label><input type="checkbox" id="s-fb" ${s.pdf.find_oapen !== false ? "checked" : ""}> OAPEN (open-access books)</label><label><input type="checkbox" id="s-fl" ${s.pdf.find_openlibrary !== false ? "checked" : ""}> Open Library / Internet Archive (public-domain books)</label></div>
    <label class="l">Institutional access</label><div><label><input type="checkbox" id="s-inst" ${s.pdf.institutional ? "checked" : ""}> Also fetch the publisher's PDF through my university's access (VPN / campus network)</label>
      <div class="checks" style="margin-top:6px"><label><input type="checkbox" id="s-brw" ${s.pdf.use_browser !== false ? "checked" : ""}> Use a real browser when a site needs it (clicks “Download PDF”; needs <span class="mono">pip install playwright</span>)</label>
      <label><input type="checkbox" id="s-hdl" ${s.pdf.browser_headless ? "checked" : ""}> Hide the browser window</label></div>
      <div class="row" style="margin-top:6px"><input type="text" id="s-els" value="${esc(s.pdf.elsevier_api_key || "")}" placeholder="Elsevier API key (optional, dev.elsevier.com)" style="width:320px"></div></div>
    <label class="l">Downloads folder</label><div><input type="text" id="s-dl" value="${esc(s.pdf.downloads_dir || "")}" placeholder="default: your Downloads folder" style="width:320px"></div>
    <div class="help">Where your normal browser saves files — used by “Get with my own browser”, for sites whose captcha rejects the automated browser.</div>
    <div class="help">One item at a time, with pauses between requests. The browser window stays visible so you can complete your university login or a check once; it keeps its cookies for next time.</div>
    <label class="l">Rename</label><div class="checks"><label><input type="checkbox" id="s-ren" ${s.pdf.rename ? "checked" : ""}> Propose file renames</label><label><input type="checkbox" id="s-renl" ${s.pdf.rename_linked ? "checked" : ""}> Also linked files</label><label><input type="checkbox" id="s-vc" ${s.pdf.verify_content ? "checked" : ""}> Read PDF contents (text layer, DOI, title)</label></div>
    <label class="l">Tag names</label><div class="row"><input type="text" id="s-tnp" value="${esc(s.tags.needs_pdf)}" style="width:140px"><input type="text" id="s-tpp" value="${esc(s.tags.pdf_problem)}" style="width:140px"><input type="text" id="s-tdu" value="${esc(s.tags.duplicate)}" style="width:140px"></div>
  </div></div>
  <div class="card" style="margin-top:12px"><h2 style="margin-top:0">Applying</h2><div class="form">
    <label class="l">Database copy before applying</label><label><input type="checkbox" id="s-dbb" ${s.commit.db_backup ? "checked" : ""}> Copy zotero.sqlite (recommended)</label>
    <label class="l">Batch size</label><input type="number" id="s-bs" min="1" max="50" value="${esc(s.commit.batch_size)}">
    <label class="l">Backups folder</label><div class="mono small">${esc(d.backup_dir)}</div>
  </div></div>
  <div class="row" style="margin-top:16px"><button class="primary" id="s-save">Save settings</button><button id="s-save2">Save &amp; re-analyse</button></div>`;
  const list = s2 => s2.split(",").map(x => x.trim()).filter(Boolean);
  const collect = () => ({
    zotero_api: $("#s-api").value, library: $("#s-lib").value, data_dir: $("#s-dd").value.trim(), mailto: $("#s-mail").value.trim(),
    latex: {enabled: $("#s-lx").checked, style: $("#s-lxs").value, escape_specials: $("#s-lxe").checked, include_abstract: $("#s-lxa").checked, fields: $$(".lxf").filter(c => c.checked).map(c => c.value)},
    rules: Object.fromEntries($$(".rule").map(c => [c.value, c.checked])), title_case: $("#s-tc").value, acronyms: list($("#s-acr").value),
    pages_separator: $("#s-ps").value, proxy_suffixes: list($("#s-px").value), duplicates: {threshold: +$("#s-dt").value},
    lookups: {crossref: $("#s-cr").checked, doi_content_negotiation: $("#s-cn").checked, arxiv: $("#s-ax").checked, search_missing_doi: $("#s-sm").checked, min_match_score: +$("#s-ms").value, requests_per_second: +$("#s-rps").value},
    pdf: {types_needing_pdf: $$(".pt").filter(c => c.checked).map(c => c.value), types_optional_pdf: $$(".po").filter(c => c.checked).map(c => c.value), filename_template: $("#s-tpl").value, max_title_words: +$("#s-tw").value, max_length: +$("#s-ml").value,
      ascii_filenames: $("#s-ascii").checked, rename: $("#s-ren").checked, rename_linked: $("#s-renl").checked, verify_content: $("#s-vc").checked,
      find_unpaywall: $("#s-fu").checked, find_openalex: $("#s-fo").checked, find_semanticscholar: $("#s-fs").checked, find_arxiv: $("#s-fa").checked,
      find_europepmc: $("#s-fe").checked, find_oapen: $("#s-fb").checked, find_openlibrary: $("#s-fl").checked,
      institutional: $("#s-inst").checked, use_browser: $("#s-brw").checked, browser_headless: $("#s-hdl").checked, elsevier_api_key: $("#s-els").value.trim(), downloads_dir: $("#s-dl").value.trim()},
    tags: {needs_pdf: $("#s-tnp").value, pdf_problem: $("#s-tpp").value, duplicate: $("#s-tdu").value},
    commit: {db_backup: $("#s-dbb").checked, batch_size: +$("#s-bs").value},
  });
  const save = async (then) => { try { await post("/api/settings", collect()); toast("Settings saved"); if (then) startJob("analyse"); } catch (e) { toast(e.message, true); } };
  $("#s-save").onclick = () => save(false); $("#s-save2").onclick = () => save(true);
  $("#s-reset").onclick = async () => { if (await confirmBox("Reset connection?", "Use this only if you moved to a different Zotero database. The next scan re-reads the whole library.", "Reset")) { await post("/api/reset-connection"); toast("Connection reset"); refreshStatus(); } };
  $("#s-forget").onclick = async () => { await post("/api/forget-key"); toast("Write key forgotten"); refreshStatus(); };
  let t1; $("#s-try").oninput = () => { clearTimeout(t1); t1 = setTimeout(async () => { const r = await post("/api/tools/latex", {text: $("#s-try").value}); $("#s-tryout").innerHTML = `<span class="mono">${esc(r.latex)}</span>`; }, 250); };
  const prev = async () => { const r = await post("/api/tools/filename-preview", {template: $("#s-tpl").value, ascii_filenames: $("#s-ascii").checked, max_title_words: +$("#s-tw").value, max_length: +$("#s-ml").value});
    $("#s-prev").innerHTML = r.length ? r.map(x => `${esc(x.current)} → <b>${esc(x.new)}</b>`).join("<br>") : "No PDFs in the snapshot yet."; };
  let t2; ["#s-tpl", "#s-tw", "#s-ml", "#s-ascii"].forEach(sel => $(sel).addEventListener("input", () => { clearTimeout(t2); t2 = setTimeout(prev, 300); }));
  prev();
}

/* ---------------------------------------------------------------- boot */
route();
poll();

let TAG_STATE = { topics: {}, tags: {}, keys: [], embeddings: [] };

async function viewTags(v) {
  v.innerHTML = `<h1>Topic Tags (AI)</h1>
    <p class="sub">Discover topics mathematically using local embeddings (BERTopic) and snap papers to your curated list.</p>
    <div class="card">
      <div class="row">
        <label>Min Topic Size: <input type="number" id="min-size" value="5" style="width: 60px"></label>
        <button class="primary" onclick="discoverTags()">Discover Tags</button>
      </div>
      <div id="tag-results" style="margin-top: 15px;"></div>
    </div>
    <div class="card" id="reassign-card" style="display:none; margin-top: 15px;">
      <h2>Review & Reassign</h2>
      <p class="sub">Edit the discovered tags below. When you click Reassign, papers will be snapped to the closest tag in this list (Cosine Similarity &ge; 0.3).</p>
      <textarea id="curated-tags" style="width: 100%; height: 150px; font-family: monospace;"></textarea>
      <div class="row" style="margin-top: 10px;">
        <button class="primary" onclick="reassignTags()">Reassign Tags to Papers</button>
        <button onclick="createSmartFolders()">Create Smart Folders in Zotero</button>
      </div>
    </div>
  `;
  
  if (TAG_STATE.topics && Object.keys(TAG_STATE.topics).length > 0) {
    let html = `<h3>Discovered ${Object.keys(TAG_STATE.topics).length} Topics</h3><ul>`;
    const tagNames = [];
    for (const [id, t] of Object.entries(TAG_STATE.topics)) {
      html += `<li><b>${esc(t.name)}</b> (${t.count} papers) - <i>${esc(t.words.join(", "))}</i></li>`;
      tagNames.push(t.name);
    }
    html += `</ul>`;
    $("#tag-results").innerHTML = html;
    $("#reassign-card").style.display = "block";
    if (!$("#curated-tags").value) {
      $("#curated-tags").value = tagNames.join("\n");
    }
  }
}

async function discoverTags() {
  const minSize = parseInt(document.getElementById("min-size").value, 10);
  startJob("discover_tags", { min_topic_size: minSize });
}

async function reassignTags() {
  const text = document.getElementById("curated-tags").value;
  const tags = text.split("\n").map(x => x.trim()).filter(x => x);
  startJob("reassign_tags", { keys: TAG_STATE.keys, embeddings: TAG_STATE.embeddings, curated_tags: tags });
}

async function createSmartFolders() {
  const text = document.getElementById("curated-tags").value;
  const tags = text.split("\n").map(x => x.trim()).filter(x => x);
  startJob("create_smart_folders", { tags: tags });
}
