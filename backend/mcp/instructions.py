"""What the MCP server tells the AI app: how to use the tools, and the note syntax to write."""

WRITING_RULES = """\
Note syntax (the app's own Markdown; write_note checks it and refuses notes that would not work):
- Cite library items as [@key] or [@key, p. N] (N = PDF page from 1, as the tools report). Several: [@a; @b].
  If the item has several PDFs (get_paper lists them with their attachment ids), name the one you mean with
  [@key, p. N, att:ID]; without att: the citation means the first PDF.
- Link notes with the `link` value the tools give for each note, e.g. [[smith2021]] or \
[[stem|Shown text]]; links resolve by file name, not by title. [[stem#Heading]] links a heading.
- Math is LaTeX rendered with KaTeX: inline $t \\lesssim N - 1.6$ (no space after the opening $ or before the \
closing one, on one line; write \\$ for a dollar sign), display $$...$$. Never write formulas with Unicode \
symbols (≈, ⟨ψ|, x₁) outside $...$. A display equation gets a number when it has a \\label{eq:name} \
(numbered through the note; equation/align environments as in LaTeX; \\tag{3} sets one); \\cref{eq:name} or \\eqref{eq:name} in the \
text or in math refers to it. The physics package's commands work (\\vb, \\dv, \\pdv, \\qty, \\abs, \\expval, \
\\mel, \\comm, \\dd…), as do the user's own macros (get_note lists them) and a \\newcommand written in any formula \
of the note (it applies to the whole note).
- Quotes point at the exact words in the PDF: a block quote ending with its citation, \
"> exact words from the page [@key, p. N]", the words copied from read_paper (prose, not formulas). \
Clicking the citation opens the PDF there and highlights them. A citation inside a block quote or callout \
always means such a quote.
- Figures, tables and equations: use snapshot (figure="Figure 2", equation="3", or a rect read off view_page) and \
paste the Markdown it returns: the image line, then on the next line *Figure 2: … [@key, p. N]* (you may write a \
caption before the citation). Clicking it flashes that region.
- Tasks: a line {{task:ID}} shows one of the user's tasks (a record with a title, status, due date, details and \
sub-tasks; the line shows its checkbox and title). Never invent an ID: create tasks with create_task \
(note=… puts the line in a note) or reuse IDs from list_tasks. A plain "- [ ] item" is a checkbox, not a task.
- A figure is an image line followed directly by its caption line wholly in italics, "*Caption text. \\label{fig:name}*", with blank lines around the pair. Figures with a caption are numbered Fig. 1, 2, … through the note (galleries with a caption too); references work as with LaTeX's cleveref: \\cref{fig:name} gives "Fig. 2", \\cref{eq:name} "Eq. (3)", \\cref{a,b} \
both, \\Cref at the start of a sentence "Figure 2" (never type "Fig." or "Eq." yourself); a \\label in a gallery panel's caption \
names that panel ("Fig. 2(b)"). An image without a caption line is not numbered.
- Several images shown together (a carousel in the app, one figure with panels (a), (b)… when exported) go in a gallery block: a line ```gallery (or ```gallery|600 for a width in pixels), then each image on its own line followed by its caption (on the same line or the lines right below), a blank line, the caption of the whole gallery, and a closing ```. Captions may hold math and citations.
- Also available: GFM tables, task lists, footnotes [^1], callouts "> [!note] Title" (types note, tip, \
warning, question, quote, info, important, caution, example, todo), ```mermaid diagrams, #tags."""

INSTRUCTIONS = (
    """\
Tools for the user's research library ("Synapse", a Zotero-like app): papers with metadata and PDFs, \
Markdown notes that cite papers as [@citationkey], and reference lists fetched from Semantic Scholar/OpenAlex/Crossref.

How to work:
- Start from a note (get_note) or a search (search_library). Papers are named by citation key.
- get_paper gives metadata, abstract, the user's paper note and where other notes cite the paper.
- For PDF content, find the relevant pages with search_in_paper, then read_paper those pages. \
Avoid reading whole papers page by page. view_page shows a page as an image (figures, equations, tables).
- Images in notes (![](attachments/…)) are files of the app's notes folder, not of your computer: \
see them with view_image (one path, or every image of a note).
- get_references / get_citation_graph follow the citation graph, also to works not in the library \
(they come with DOI/arXiv ids you can look up elsewhere).
- Cite sources in the user's syntax: [@key] or [@key, p. N] with N the PDF page number the tools report.
- write_note saves a new note (only in the ai/ folder); use it when the user asks to save an answer.
- edit_note changes an existing note (after get_note), when the user asks for it; the user can see and undo \
every change in the app.
- The user's tasks (to-dos) are records shown in notes as {{task:ID}} lines: list_tasks searches them, get_task \
shows one with its details and sub-tasks, create_task / update_task / delete_task change them when the user asks.

"""
    + WRITING_RULES
)
