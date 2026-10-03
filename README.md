# Zotero Library Cleaner

A local tool for cleaning up and normalising a large Zotero library. You review every change before anything is written to Zotero.

* Suggests metadata fixes from local rules and from Crossref, DOI registries (DataCite, …) and arXiv.
* Stores special characters as **LaTeX** (`Schr{\"o}dinger`, `$\alpha$`, `CO$_{2}$`, `\&`) without breaking LaTeX that is already in your fields.
* Finds duplicates, and missing, broken or wrong PDFs. Proposes normalised PDF file names.
* Nothing is written until you press **Apply**. A backup is made first, and every commit can be **rolled back**.

## Requirements

| | |
|---|---|
| Reading and analysing | Zotero 7 or later, with *Settings → Advanced → "Allow other applications on this computer to communicate with Zotero"* enabled |
| Applying changes | **Zotero 10 or later**. The first time, Zotero shows a dialog: choose **Always Allow** (plain *Allow* is valid for a single write only) |
| Python | 3.10+ |

## Install and run

```bash
cd zotero_organizer
source virtual_env/bin/activate        # or create one: python3 -m venv virtual_env
pip install -r requirements.txt        # optional: pip install pymupdf  (faster PDF text extraction)
python app.py                          # opens http://127.0.0.1:5000
```

Keep Zotero open while you use the tool.

## Workflow

1. **Dashboard → Sync & analyse.** The first run reads the whole library. Later runs read only what changed. Local rules run, and duplicates are detected.
2. **Check PDFs.** Flags missing PDFs, files missing on disk, HTML pages saved as `.pdf`, PDFs that belong to another paper (DOI or title on the first pages), scans without a text layer, identical files and standalone PDFs. Proposes file names from your template.
3. **Online lookups** (optional). Compares items with Crossref, doi.org and arXiv, and searches for DOIs of items that have none. Results are cached, so you can re-run for free.
4. **Review changes.** Each proposal shows the current and the new value as rendered text, with a word diff (tick *LaTeX source* to see the raw text). For each proposal you can:
   * accept, reject or edit it (edits show a live preview of how the LaTeX renders);
   * filter by rule, source, field or confidence;
   * bulk-accept all proposals that match a filter (e.g. all *high-confidence DOI clean-ups*).

   Keyboard: `j`/`k` move, `a` accept, `r` reject, `e` edit, `u` undo, `A` accept the whole item.
5. **Issues, Duplicates, PDFs.** These need your judgement.
   * "Open in Zotero" links jump straight to the item.
   * You can **tag** items in bulk, e.g. `_needs-pdf`. After applying, select them in Zotero and add the PDFs, or use *Find Available PDF*.
   * Duplicates are tagged here and merged in Zotero's *Duplicate Items* pane, which keeps notes, tags, collections and attachments.
6. **Apply to Zotero.** Authorise writes once, then *Back up & apply*.
7. **History.** See exactly what changed (before/after) and **roll back** a commit.

## Safety model

* **Runs converge:** each field has exactly one target value, and every rule and source agrees on it. Local rules are always computed from the field's *base*, which is its value before the tool's own automatic changes (stored with every change), never from their own earlier output. So after applying the proposals, a new run proposes nothing, while a genuine improvement of the rules is still picked up. The chain stops at anything that is not a pure re-formatting: a value you typed, data from Crossref or arXiv, or a change made in Zotero. Values you typed yourself are never argued with. Item types follow the same rule: an arXiv paper is a preprint unless a published version is known, whichever check looks at it. Only your explicit rejections are remembered as blocks. Changes that belong together (a type change and its fields, a switch to the published version, a search match) are accepted, rejected and applied as one unit.
* **Review first:** every change is a proposal stored in `workspace/workspace.sqlite3`. Your decisions survive restarts and rescans. Rejected proposals are not suggested again.
* **Backup before every commit and every roll-back** (in `workspace/backups/<timestamp>/`):
  * `library.json.gz`: the complete library, exported through the API;
  * `items_before.json`: the items about to change, as they were;
  * `zotero.sqlite`: a copy of the Zotero database, verified with SQLite's integrity check.
* **Three-way check:** each item is re-read right before writing. A change is applied only if the field still holds the value you reviewed. Otherwise it is reported as a *conflict* and left alone. Writes also carry the item version, so Zotero rejects them if the item changed in the meantime.
* **No blind overwrites:**
  * if a DOI resolves to a *different* paper, you get an issue, not an overwrite;
  * dates never become less precise;
  * richer author names (full first names, accents) are kept;
  * fields that don't exist for an item type are never written;
  * item-type changes move fields to their equivalents, and anything without a place goes into *Extra*.
* **Roll-back** reverts through the API, so it also works with Zotero sync. Fields changed again after the commit are left alone and reported.
* **Local only:** the tool listens on 127.0.0.1. It checks the `Host` header (against DNS rebinding) and requires a per-session token on every API call. The Zotero write key is stored in `workspace/secrets.json`, which only your user can read.

To restore the database copy by hand: quit Zotero, keep your current `zotero.sqlite`, put the backup copy in its place and restart. If you use Zotero sync, prefer *Roll back*: restoring an old database with sync enabled causes conflicts.

## LaTeX handling

All LaTeX fields are treated as LaTeX source (configurable in Settings; the abstract is off by default).

* Raw Unicode is converted to LaTeX:
  * accents: `{\"o}`, `{\v c}`, `{\'\i}`
  * special letters: `{\ss}`, `{\o}`, `{\L}`
  * Greek and math symbols: `$\alpha$`, `$\leq$`, `CO$_{2}$`, `x$^{2}$`
  * dashes and quotes: `--`, `---`, ``` ``…'' ```
  * `&`, `%`, `#` and `_` are escaped
* Existing commands, math and brace groups are left untouched, and the conversion is idempotent (running it twice changes nothing).
* Whole words in Greek are left as Greek text; isolated letters become math.
* HTML/JATS markup, from Crossref or from Zotero rich text, becomes LaTeX: `<i>` → `\textit{}`, `<sub>` → `$_{}$`, `<span class="nocase">` → `{…}`.
* Comparisons (duplicates, lookups, "is this a real change?") use the rendered text, so `Schr{\"o}dinger` = `Schrödinger`.
* Proposals that would produce broken LaTeX (unbalanced braces or `$`, unescaped `&`) are flagged.

Note: Zotero itself does not render LaTeX. Citations produced *inside Zotero* (e.g. the Word plugin) will show the raw LaTeX. This setup suits BibTeX/Better BibTeX workflows.

## Rules (each can be switched off in Settings)

| Area | What the rules do |
|---|---|
| Text clean-up | whitespace · HTML/JATS → LaTeX · Unicode → LaTeX · escaping of `& % # _` |
| Identifiers | DOI clean-up (resolver prefixes, DOIs moved out of URL/Extra) · ISSN/ISBN formatting |
| Titles | ALL-CAPS titles → sentence case (acronyms, `{protected}` text, math and proper nouns learnt from your library are preserved) · optional sentence/Title Case policy · trailing periods |
| Creators | `Last, First` in single-field names · ALL-CAPS names (`VAN DER BERG` → `van der Berg`) · initials (`J.R.` → `J. R.`) · empty and duplicate creators |
| Other fields | page ranges (`pp. 12 - 19` → `12--19`) · unambiguous dates → ISO · URLs without tracking parameters or library-proxy host names · language → ISO code |
| Library-wide | journal-name variants unified · missing journal abbreviations filled · tag variants merged |
| Item type | e.g. arXiv papers stored as journal articles → preprint |

## Finding missing PDFs

*PDFs → Find missing PDFs online* (or *Find PDF online* on a single item) looks for **legal open-access copies** using every identifier the item has:

| Identifier | Where it looks |
|---|---|
| **DOI** | Unpaywall (uses your e-mail address from Settings), OpenAlex (publishers, repositories, PubMed Central), Semantic Scholar (also finds the arXiv version of a published paper) |
| **arXiv id** | arXiv |
| **PMID / PMCID** (in Extra) | Europe PMC, which also supplies the DOI |
| **ISBN** | OAPEN (open-access books), Open Library / Internet Archive (only books with public full access), and Crossref (to find a DOI for the book or chapter) |
| **HAL link, or a URL ending in .pdf** | the link itself |
| **none of these** | a title search on OpenAlex, accepted only when title, year and first author match |

**Institutional access** (Settings → PDFs, off by default). With your university VPN or campus network active, the tool also fetches the publisher's own PDF, the way you would by hand:

1. It opens the DOI and finds the PDF on the article page: the `citation_pdf_url` tag, the publisher's known PDF address (APS, Springer, Wiley, IOP, Nature, IEEE, ScienceDirect, ACS, T&F, World Scientific, JPSJ, Science, SIAM, ACM, PNAS, AIP, Optica…), or a "Download PDF" / "View PDF" link. PDF viewer pages are unwrapped.
2. If a site only works in a real browser (JavaScript buttons, bot checks), it opens the page in Chrome and clicks "Download PDF" / "PDF". This needs `pip install playwright` and uses your installed Google Chrome; without Chrome, run `playwright install chromium`. The window is visible, so you can complete your university login or a check once. The browser profile keeps the cookies (in `workspace/browser_profile`).
3. Optionally, with a free Elsevier API key from dev.elsevier.com, Elsevier articles are fetched through Elsevier's official full-text API, which honours your institution's subscription.

**Captchas** (ScienceDirect often shows one) are recognised and never reported as "no PDF". The browser window comes to the front and the top bar of the app says *solve the check in the browser window*. The tool waits for you (3 minutes by default), then continues with the download. Checks that pass by themselves are simply waited for.

Some sites (Elsevier's Cloudflare check, for example) detect that the browser is automated and ask for the check again after every solve. The tool notices this: when the check comes back after you solved it (or when you close that tab), it stops waiting, skips the automated browser for that publisher for the rest of the run, and marks the item *blocked by the publisher's captcha*. For those items, use **Get with my own browser**:

1. *PDFs → Get PDFs with your own browser* (or the button in the search summary, or *Get with my browser* on a single item) opens the articles in your normal browser, up to 25 tabs at a time. There you are a regular visitor, so the check passes as usual.
2. Click "Download PDF" in each tab. The top bar shows how many files have arrived.
3. The tool watches your Downloads folder (configurable in Settings → PDFs), matches each new PDF to its item by DOI, arXiv id, ISBN or title, and proposes it in Review. Your downloaded files are left where they are; a copy is attached.

*Pick up PDFs I already downloaded* checks the PDFs downloaded in the last 24 hours against every item with a missing PDF, without opening anything. Scanned PDFs without text cannot be matched, unless only one item is being waited for.

Requests go one at a time, with pauses between them. Use this for your own copies, as your licence allows.

Candidates are tried in order: published version, then accepted manuscript, then preprint. Every downloaded file is checked before it is proposed:

* it must be a real PDF, not a login or cookie page;
* it must be the right paper: the item's DOI, arXiv id or ISBN, or its title (and first author), must appear on the first pages (on the first six pages for books).

The PDFs that pass the checks appear in Review as "PDF found online". Each one shows its source, version, licence and the evidence, and has a link to open the downloaded file. Preprint versions of published articles are clearly marked.

Accepted PDFs are attached when you apply, named with your file-name template. Roll-back moves them to the trash. Items with no open-access copy keep their "Missing PDF" warning, with a note that nothing was found online. For those, use your library access (for example through the library proxy) or Zotero's *Find Available PDF*.

For **books** (configurable in Settings → PDFs), a missing PDF is only reported as *info*.

## PDF file names

Template tokens: `{firstCreator} {authors} {year} {title} {shortTitle} {journal} {journalAbbrev} {citekey} {doi} {key} {itemType}`. If a token is empty, its separator is dropped too. The default is `{firstCreator} - {year} - {title}`, which gives e.g. `Smith et al. - 2020 - Title.pdf`.

The name is recomputed from the *final* metadata when you apply, unless you edited it. PDFs that look wrong (e.g. they belong to another paper) are never renamed.

* **Stored files**: the tool points the attachment to the new name through the API, moves the file in `storage/<key>/`, and then checks that Zotero finds the file under the new name. If any step fails, everything is undone.
* **Linked files** are renamed on disk, and the link in Zotero is updated.

Avoid Zotero's own *Rename File from Parent Metadata* and its automatic renaming (Settings → General → File Renaming): Zotero builds names from the raw fields, so LaTeX ends up in file names (`Schr{o}dinger`). The next PDF check proposes clean names for such files.

## Tests

```bash
pip install pytest reportlab
python -m pytest tests          # unit tests + end-to-end against a mock Zotero local API
python tests/mock_zotero.py     # a messy demo library on port 23999 (set it as "Local API address" in Settings)
```
