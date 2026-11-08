"""UI changes select relevant real checklists; unknown areas must not silently lose coverage."""

from tools.browser_scope import PORTABLE, ROOT, matrix, select

KNOWN = {p.name.removesuffix("_checklist.py") for p in (ROOT / "tests/e2e").glob("*_checklist.py")}


def test_docs_do_not_start_browsers():
    assert matrix(["AGENTS.md", "docs/frontend.md", "README.md"], KNOWN) == {"include": []}


def test_backup_settings_include_related_flows_and_portable_coverage():
    rows = matrix(["frontend/src/views/settings/remoteBackups.ts"], KNOWN)["include"]
    assert {"settings_organization", "backups", "remote_backups"} <= set(rows[0]["checklists"])
    assert set(rows[1]["checklists"]) == PORTABLE
    assert set(rows[0]["checklists"]) < KNOWN


def test_unknown_shared_and_deleted_checklists_fall_back_to_all():
    for path in (
        "frontend/src/new-area.ts",
        "backend/services.py",
        "tests/e2e/deleted_checklist.py",
        "frontend/src/views/settings/page.ts",
        "frontend/src/views/notes/notesView.ts",
        "backend/notes/service.py",
    ):
        assert select([path], KNOWN) == KNOWN


def test_note_subsystems_keep_their_direct_checklists():
    cases = {
        "frontend/src/views/notes/historyView.ts": "notes_history",
        "backend/notes/history/recorder.py": "notes_history",
        "frontend/src/views/notes/attachmentsDialog.ts": "attachments",
        "frontend/src/views/notes/equations.ts": "equations",
        "frontend/src/views/notes/commands.ts": "note_commands",
    }
    for path, checklist in cases.items():
        assert checklist in select([path], KNOWN)


def test_extension_checks_do_not_request_unsupported_webkit_flows():
    rows = matrix(["browser_extension/find_pdfs.js"], KNOWN)["include"]
    assert rows == [{"browser": "chromium", "checklists": ["extension", "find_pdfs"]}]


def test_changed_checklists_and_multiple_areas_are_combined():
    names = select(["tests/e2e/links_checklist.py", "plugins/autocompletion/frontend/source.ts"], KNOWN)
    assert {"links", "assist", "notes"} | PORTABLE <= names
    assert names <= KNOWN


def test_nightly_and_manual_runs_keep_full_coverage():
    rows = matrix(None, KNOWN)["include"]
    assert set(rows[0]["checklists"]) == KNOWN
    assert set(rows[1]["checklists"]) == PORTABLE
    assert [r["browser"] for r in matrix(None, KNOWN, "webkit")["include"]] == ["webkit"]
