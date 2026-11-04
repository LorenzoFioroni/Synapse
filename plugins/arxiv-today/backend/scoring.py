"""How well a new paper matches the user: a 0–100 match built from specific, current links to the
library, not from how many links it has (docs/plugins.md, "arXiv Today: the match").

Each library paper is an *anchor* weighted by interest (the user wrote it, notes cite it, it was
added lately) times specificity (how rarely the papers arXiv announces cite it, measured on the
reference lists the plugin has read). Six channels turn a paper's evidence into features with
diminishing returns, added as log-odds:

    match = 100 · σ(BIAS + Σ weight_k · feature_k)

- ``anchors``: the library papers it cites, Σ interest · specificity², the square so that a
  textbook everyone cites counts little next to a niche paper;
- ``own``: the papers the user authored that it cites;
- ``shared``: rare references it shares with the library's own reference lists, also works
  outside the library;
- ``people``: its authors who wrote library papers, matched by full name where both sides have
  one, discounted by how common the name is among announced papers (rarity², as for citations);
- ``topic``: title and abstract against the library's (TF-IDF, top five, weighted by interest),
  counted only where it stands out from the rest of that day;
- ``cross``: a small penalty for cross-lists.

Nothing here reads the database or the web: the plugin builds a ``Library`` and a ``Background``
and passes papers in, so the scoring is tested on its own.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

from backend.plugins import scholarly

BIAS = -2.5  # most papers are not for you: no evidence at all gives a match of 8
WEIGHTS = {"anchors": 3.5, "own": 5.0, "shared": 1.5, "people": 1.5, "topic": 0.8, "cross": -0.4}
READ_FROM, SKIM_FROM = 70.0, 35.0

INTEREST_CAP = 4.0
AUTHORED, NOTED, NOTED_RECENTLY, RECENT = 3.0, 2.0, 2.5, 1.5
RECENT_DAYS, NOTE_RECENT_DAYS = 90, 60
TRUST_PAPERS = 300  # background size at which measured rarity counts half (below: closer to 0.5)
GENERIC = 0.45  # specificity under which a cited library paper is "widely cited"
TOPIC_TOP, TOPIC_FROM, TOPIC_CAP = 5, 1.5, 3.0  # top matches averaged; z-score from which it counts
COMMON = 0.25  # words in more than this share of the library say little and are left out
SHOWN_FROM = 0.05  # contributions (in log-odds) from which a channel is named as a reason

STOP = set(
    """the and for with from that this these those are was were been being have has had not but its our
    their which what when where who whom how why can could may might will would should into onto over
    under than then also such via using use used based new two one three show shows shown study
    studies paper work results result method methods approach approaches we here there both each
    other more most less many much some any all between within without about against among""".split()
)
WORD_RE = re.compile(r"[a-z][a-z0-9]{2,}")
LATEX_ACCENT = re.compile(r"\\[^A-Za-z\s]|\\[A-Za-z](?=\{)|[{}]")


# ---------------------------------------------------------------------------------------- text
def fold(text: str | None) -> str:
    """Lower case, without accents or LaTeX accent commands (P\\'erez → perez)."""
    s = LATEX_ACCENT.sub("", str(text or "")).replace("\\", "")
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def words(text: str | None) -> list[str]:
    return [w for w in WORD_RE.findall(fold(text)) if w not in STOP]


def title_key(title: str | None) -> str | None:
    """A title as a key for references without identifiers (four words at least)."""
    ws = re.findall(r"[a-z0-9]+", fold(title))
    return "t:" + " ".join(ws) if len(ws) >= 4 else None


def ref_keys(ref: dict) -> set[str]:
    """``doi:…`` / ``arxiv:…`` keys of a reference, or its title key when it has neither."""
    keys = set()
    doi = scholarly.norm_doi(ref.get("doi"))
    arx = scholarly.norm_arxiv(ref.get("arxiv_id") or ref.get("arxiv"))
    if doi and doi.startswith("10.48550/arxiv."):
        arx = arx or scholarly.norm_arxiv(doi.split("arxiv.", 1)[1])
        doi = None
    if doi:
        keys.add("doi:" + doi)
    if arx:
        keys.add("arxiv:" + arx)
    if not keys:
        t = title_key(ref.get("title"))
        if t:
            keys.add(t)
    return keys


def item_keys(item: dict) -> set[str]:
    """The keys under which references to a library item appear."""
    doi, arx = scholarly.identifiers(item)
    keys = ref_keys({"doi": doi, "arxiv_id": arx})
    t = title_key(item.get("title"))
    return keys | ({t} if t else set())


# ---------------------------------------------------------------------------------------- names
@dataclass(frozen=True)
class Name:
    family: str
    initial: str
    first: str  # the first given name in full, "" when only initials are known
    shown: str

    @property
    def short(self) -> str:
        return f"{self.family}|{self.initial}"

    @property
    def full(self) -> str | None:
        return f"{self.family}|{self.first}" if self.first else None

    def same(self, other: Name) -> bool:
        """Same person as far as names tell: family and initial agree, and the first names too
        when both are known in full."""
        if self.family != other.family or self.initial != other.initial:
            return False
        return not (self.first and other.first) or self.first == other.first


def name(display: str | tuple[str, str]) -> Name | None:
    """A name from "Given Family", "Family, Given" or a (family, given) pair."""
    if isinstance(display, tuple):
        family, given = display
    else:
        family, given = scholarly.parse_display_name(LATEX_ACCENT.sub("", str(display)).replace("\\", ""))
    fam = " ".join(re.findall(r"[a-z]+", fold(family)))
    tokens = [t for t in re.split(r"[\s.]+", fold(given)) if t.strip("-")]
    if not fam or not tokens:
        return None
    first = tokens[0].strip("-")
    full = first if len(first.replace("-", "")) > 1 else ""
    return Name(fam, first[0], full, str(family).strip())


def item_names(item: dict) -> list[Name]:
    authors = item.get("authors")
    pairs = scholarly.split_authors(authors) if isinstance(authors, str) else [a for a in authors or []]
    return [n for n in (name(p) for p in pairs) if n]


# ---------------------------------------------------------------------------------------- inputs
def rarity(count: int, total: int) -> float:
    """0 (everyone has it) … 1 (nobody else does), shrunk toward 0.5 while the background is
    small, and with a pseudo-count so that 0 or 1 sightings are not taken as maximal rarity."""
    if total <= 0:
        return 0.5
    raw = math.log((total + 2) / (min(count, total) + 2)) / math.log(total + 2)
    trust = total / (total + TRUST_PAPERS)
    return trust * raw + (1 - trust) * 0.5


@dataclass
class Background:
    """What announced papers usually cite and who usually writes them: counts of papers citing
    each reference key (out of ``papers``, those with a reference list), and of papers by each
    author key (out of ``authored``)."""

    papers: int = 0
    cites: dict[str, int] = field(default_factory=dict)
    authored: int = 0
    authors: dict[str, int] = field(default_factory=dict)

    def specificity(self, keys: set[str]) -> float:
        n = max((self.cites.get(k, 0) for k in keys), default=0)
        return rarity(n, self.papers)

    def name_rarity(self, key: str) -> float:
        return rarity(self.authors.get(key, 0), self.authored)


@dataclass
class Library:
    """The library side, built once per scoring run."""

    titles: dict[str, str]
    interest: dict[str, float]
    why: dict[str, list[str]]
    specificity: dict[str, float]
    authored: set[str]
    arxiv_of: dict[str, str]
    names: dict[str, list[tuple[Name, str]]]  # short name key -> (name, item id)
    refs: dict[str, set[str]]  # item id -> keys of the works it cites outside the library
    by_ref: dict[str, list[str]]  # key -> library items citing it
    idf: dict[str, float]
    postings: dict[str, list[tuple[str, float]]]

    def is_self(self, pid: str, arxiv: str | None) -> bool:
        """Whether library item ``pid`` is the paper with this arXiv id (scored against itself)."""
        return bool(arxiv) and self.arxiv_of.get(pid) == arxiv


def _when(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc)
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def build_library(
    items: list[dict],
    *,
    notes: list[dict] = (),  # type: ignore[assignment]
    links: list[dict] = (),  # type: ignore[assignment]
    graph: dict | None = None,
    author_names: list[str] = (),  # type: ignore[assignment]
    from_feed: set[str] = frozenset(),  # type: ignore[assignment]
    background: Background | None = None,
    now: datetime | None = None,
) -> Library:
    """``items``: library items; ``notes`` / ``links``: plugin.notes.list() / links();
    ``graph``: plugin.library.citation_graph(); ``author_names``: the user's names;
    ``from_feed``: arXiv ids the plugin listed (an item with one was added from the feed)."""
    now = now or datetime.now(timezone.utc)
    bg = background or Background()
    by_key = {str(i.get("citation_key") or "").casefold(): i["id"] for i in items if i.get("citation_key")}
    ids = {i["id"] for i in items}

    # notes citing or about a paper, and how lately they changed
    noted: dict[str, float] = {}
    mtime = {note["path"]: _when(note.get("mtime") or note.get("updated")) for note in notes}

    def note_on(item_id: str | None, path: str) -> None:
        if not item_id:
            return
        when = mtime.get(path)
        recent = bool(when and now - when <= timedelta(days=NOTE_RECENT_DAYS))
        noted[item_id] = max(noted.get(item_id, 0), NOTED_RECENTLY if recent else NOTED)

    for link in links:
        if link.get("kind") == "cite":
            note_on(by_key.get(str(link.get("target") or "").casefold()), link.get("source", ""))
    for note in notes:
        if note.get("paper"):
            note_on(by_key.get(str(note["paper"]).casefold()), note["path"])

    mine = [n for n in (name(s) for s in author_names if str(s).strip()) if n]
    titles, interest, why, spec, arxiv_of = {}, {}, {}, {}, {}
    authored: set[str] = set()
    names: dict[str, list[tuple[Name, str]]] = defaultdict(list)
    for it in items:
        pid = it["id"]
        titles[pid] = it.get("title") or "a paper in your library"
        _, arx = scholarly.identifiers(it)
        if arx:
            arxiv_of[pid] = scholarly.norm_arxiv(arx) or arx
        its_names = item_names(it)
        for n in its_names:
            names[n.short].append((n, pid))
        m, reasons = 1.0, []
        if any(a.same(b) for a in its_names for b in mine):
            authored.add(pid)
            m *= AUTHORED
            reasons.append("you wrote it")
        if pid in noted:
            m *= noted[pid]
            reasons.append("your notes cite it" + (" lately" if noted[pid] == NOTED_RECENTLY else ""))
        added = _when(it.get("date_added"))
        if (arxiv_of.get(pid) in from_feed) or (added and now - added <= timedelta(days=RECENT_DAYS)):
            m *= RECENT
            reasons.append("added lately")
        interest[pid] = min(m, INTEREST_CAP)
        why[pid] = reasons
        spec[pid] = bg.specificity(item_keys(it))

    # the library's own reference lists, for references shared with a new paper
    refs: dict[str, set[str]] = defaultdict(set)
    if graph:
        nodes = {n["id"]: n for n in graph.get("nodes", [])}
        for e in graph.get("edges", []):
            src, dst = str(e.get("source", "")), str(e.get("target", ""))
            pid = src.removeprefix("item:")
            if pid not in ids or dst.startswith("item:"):
                continue
            node = nodes.get(dst, {})
            refs[pid] |= ref_keys(
                {"doi": node.get("doi"), "arxiv_id": node.get("arxiv_id"), "title": node.get("title")}
            )
    by_ref: dict[str, list[str]] = defaultdict(list)
    for pid, keys in refs.items():
        for k in keys:
            by_ref[k].append(pid)

    # titles and abstracts (TF-IDF)
    docs: dict[str, Counter] = {}
    df: Counter = Counter()
    for it in items:
        ws = Counter(words(f"{it.get('title') or ''} {it.get('abstract') or ''}"))
        if ws:
            docs[it["id"]] = ws
            df.update(ws.keys())
    ndocs = len(docs)
    limit = max(3, int(COMMON * ndocs)) if ndocs >= 20 else ndocs + 1
    idf = {w: math.log((ndocs + 1) / (c + 1)) + 1 for w, c in df.items() if c <= limit}
    postings: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for pid, ws in docs.items():
        vec = {w: (1 + math.log(c)) * idf[w] for w, c in ws.items() if w in idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        for w, v in vec.items():
            postings[w].append((pid, v / norm))

    return Library(
        titles, interest, why, spec, authored, arxiv_of, dict(names), dict(refs), dict(by_ref), idf, dict(postings)
    )


# ---------------------------------------------------------------------------------------- scoring
def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _quote(title: str) -> str:
    return f"“{title}”"


def _topic(lib: Library, paper: dict) -> tuple[float, list[str]]:
    ws = Counter(words(f"{paper.get('title') or ''} {paper.get('abstract') or ''}"))
    vec = {w: (1 + math.log(c)) * lib.idf[w] for w, c in ws.items() if w in lib.idf}
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if not norm:
        return 0.0, []
    sims: dict[str, float] = defaultdict(float)
    for w, v in vec.items():
        for pid, lw in lib.postings.get(w, ()):
            sims[pid] += v / norm * lw
    own = scholarly.norm_arxiv(paper.get("arxiv"))
    best = sorted(
        ((s * math.sqrt(lib.interest[pid]), pid) for pid, s in sims.items() if not lib.is_self(pid, own)),
        reverse=True,
    )[:TOPIC_TOP]
    return sum(s for s, _ in best) / TOPIC_TOP, [pid for _, pid in best]


def _zscores(values: list[float]) -> list[float]:
    if len(values) < 5:
        return [0.0] * len(values)
    mid = median(values)
    spread = median(abs(v - mid) for v in values) * 1.4826 or 1e-9
    return [(v - mid) / spread for v in values]


def score(
    papers: list[dict],
    lib: Library,
    bg: Background,
    *,
    refs: dict[str, list[dict]] | None = None,
    matched: dict[str, list[str | None]] | None = None,
    weights: dict[str, float] | None = None,
    bias: float = BIAS,
    read_from: float = READ_FROM,
    skim_from: float = SKIM_FROM,
) -> list[dict]:
    """For each paper (``key``, ``arxiv``, ``title``, ``abstract``, ``authors`` as display names,
    ``kind``, ``day``): ``{"score": 0–100, "tier": "read" | "skim" | "rest", "reasons": [...],
    "features": {...}}``. ``refs[key]`` is its reference list, ``matched[key]`` the library item
    each reference is (``plugin.services.match``)."""
    w = {**WEIGHTS, **(weights or {})}
    refs, matched = refs or {}, matched or {}

    # topic: similarity, then how much it stands out within its day
    topic = [_topic(lib, p) for p in papers]
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(papers):
        by_day[str(p.get("day") or "")].append(i)
    z = [0.0] * len(papers)
    for idx in by_day.values():
        for i, v in zip(idx, _zscores([topic[i][0] for i in idx]), strict=True):
            z[i] = v

    out = []
    for i, p in enumerate(papers):
        key = str(p.get("key") or "")
        features: dict[str, float] = {}
        reasons: list[tuple[float, dict]] = []

        # the library papers it cites
        own_arxiv = scholarly.norm_arxiv(p.get("arxiv"))
        cited = sorted({m for m in matched.get(key, []) if m and m in lib.interest and not lib.is_self(m, own_arxiv)})
        own = [c for c in cited if c in lib.authored]
        other = sorted(
            (c for c in cited if c not in lib.authored),
            key=lambda c: lib.interest[c] * lib.specificity[c] ** 2,
            reverse=True,
        )
        anchor = sum(lib.interest[c] * lib.specificity[c] ** 2 for c in other)
        features["anchors"] = math.log1p(anchor)
        features["own"] = math.log1p(len(own))
        if other:
            if max(lib.specificity[c] for c in other) < GENERIC:
                text = f"Cites {_plural(len(other), 'widely cited paper')} in your library · counts little"
            else:
                rest = len(other) - 1
                text = f"Cites {_quote(lib.titles[other[0]])}" + (
                    f" and {_plural(rest, 'other library paper')}" if rest else ""
                )
                if lib.why[other[0]]:
                    text += f" ({', '.join(lib.why[other[0]])})"
            reasons.append((w["anchors"] * features["anchors"], {"kind": "cites", "text": text, "items": other}))
        if own:
            text = f"Cites {_plural(len(own), 'paper')} you authored"
            reasons.append((w["own"] * features["own"], {"kind": "cites_own", "text": text, "items": own}))

        # references shared with the library's own reference lists
        mine: set[str] = set()
        for r, m in zip(refs.get(key, []), matched.get(key, []) or [None] * len(refs.get(key, [])), strict=False):
            if not m:
                mine |= ref_keys(r)
        coupling: dict[str, float] = defaultdict(float)
        for k in mine:
            holders = lib.by_ref.get(k)
            if holders:
                s = bg.specificity({k}) ** 2
                for pid in holders:
                    coupling[pid] += s
        shared = sorted(
            (
                (lib.interest[pid] * c / math.sqrt(len(mine) * len(lib.refs[pid])), pid)
                for pid, c in coupling.items()
                if pid in lib.interest and not lib.is_self(pid, own_arxiv)
            ),
            reverse=True,
        )[:3]
        features["shared"] = math.log1p(10 * sum(s for s, _ in shared))
        if shared:
            more = len(shared) - 1
            text = f"Shares rare references with {_quote(lib.titles[shared[0][1]])}" + (
                f" and {_plural(more, 'other library paper')}" if more else ""
            )
            reasons.append(
                (w["shared"] * features["shared"], {"kind": "shared", "text": text, "items": [p for _, p in shared]})
            )

        # people
        people = 0.0
        met: dict[str, set[str]] = {}
        for display in p.get("authors") or []:
            a = name(display)
            if not a:
                continue
            same = [(b, pid) for b, pid in lib.names.get(a.short, []) if a.same(b) and not lib.is_self(pid, own_arxiv)]
            if not same:
                continue
            ids = {pid for _, pid in same}
            full = a.full if a.full and all(b.first for b, _ in same) else None
            rare = bg.name_rarity(full or a.short)
            people += rare**2 * math.log1p(sum(lib.interest[pid] for pid in ids))
            met[a.shown] = met.get(a.shown, set()) | ids
        features["people"] = math.log1p(people)
        if met:
            met_ids = sorted(set().union(*met.values()))
            shown = ", ".join(sorted(met)[:4]) + (" …" if len(met) > 4 else "")
            text = f"Shares authors with {_plural(len(met_ids), 'paper')} in your library ({shown})"
            reasons.append((w["people"] * features["people"], {"kind": "authors", "text": text, "items": met_ids}))

        # topic
        features["topic"] = min(TOPIC_CAP, max(0.0, z[i] - TOPIC_FROM))
        if features["topic"] > 0 and topic[i][1]:
            text = f"Close in topic to {_quote(lib.titles[topic[i][1][0]])}"
            reasons.append(
                (w["topic"] * features["topic"], {"kind": "similar", "text": text, "items": topic[i][1][:3]})
            )

        features["cross"] = 1.0 if str(p.get("kind") or "").startswith("cross") else 0.0

        logit = bias + sum(w[k] * v for k, v in features.items())
        match = round(100 / (1 + math.exp(-logit)), 1)
        tier = "read" if match >= read_from else "skim" if match >= skim_from else "rest"
        shown_reasons = [r for c, r in sorted(reasons, key=lambda x: -x[0]) if c >= SHOWN_FROM]
        out.append({"score": match, "tier": tier, "reasons": shown_reasons, "features": features})
    return out


TIER_TEXT = {"read": "Read", "skim": "Skim", "rest": "Rest"}
FEATURE_LABELS = {
    "anchors": "Library papers it cites",
    "own": "Your papers it cites",
    "shared": "Rare references shared with your library",
    "people": "Authors in your library",
    "topic": "Topic, against the day",
    "cross": "Cross-list",
}
