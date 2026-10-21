"""Lanceur du menu (scripts.run) et synchronisation Drive (scripts.cloud), sans réseau."""

import hashlib
import json
import os
import re

import pytest

from scripts import cloud, db, run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_menu_workflow_and_runner_list_the_same_commands():
    workflow = open(os.path.join(ROOT, ".github", "workflows", "stbl-run.yml"), encoding="utf-8").read()
    options = re.findall(r"^\s+- (\w+)$", workflow, flags=re.M)
    assert options == list(run.COMMANDS)
    script = open(os.path.join(ROOT, "apps_script", "stbl_menu.gs"), encoding="utf-8").read()
    block = script[script.index("var LABELS"):script.index("};", script.index("var LABELS"))]
    labels = dict(re.findall(r"^\s+(\w+): '([^']+)',?$", block, flags=re.M))
    assert labels == {code: c.label for code, c in run.COMMANDS.items()}
    for code in run.COMMANDS:
        assert f"function run_{code}()" in script and f"'run_{code}'" in script


class Response:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._payload, self.content, self.text = status, payload or {}, content, ""

    def json(self):
        return self._payload


class FakeHttp:
    """Dossier Drive en mémoire : {id: {name, parent, content, folder}}."""

    def __init__(self, files):
        self.files, self.uploads = files, []

    def _meta(self, fid):
        f = self.files[fid]
        meta = {"id": fid, "name": f["name"], "mimeType": cloud.FOLDER if f.get("folder") else "text/plain",
                "modifiedTime": "2026-10-17T08:00:00Z"}
        if not f.get("folder"):
            meta["md5Checksum"] = hashlib.md5(f["content"]).hexdigest()
        return meta

    def get(self, url, params=None, timeout=None):
        if params.get("alt") == "media":
            return Response(content=self.files[url.rsplit("/", 1)[1]]["content"])
        parent = re.search(r"'([^']+)' in parents", params["q"]).group(1)
        want = None if "mimeType" not in params["q"] else ("!=" not in params["q"])
        out = [self._meta(fid) for fid, f in self.files.items() if f["parent"] == parent
               and (want is None or bool(f.get("folder")) == want)]
        return Response(payload={"files": out})

    def patch(self, url, params=None, data=None, headers=None, timeout=None):
        fid = url.rsplit("/", 1)[1]
        self.files[fid]["content"] = data
        self.uploads.append(fid)
        return Response()


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "database.sqlite"))
    http = FakeHttp({"db": {"name": "database.sqlite", "parent": "root", "content": b"base drive"},
                     "le": {"name": "lineupexperts", "parent": "root", "folder": True},
                     "x": {"name": "autre", "parent": "root", "folder": True},
                     "f1": {"name": "le_ros.csv", "parent": "le", "content": b"a;b"},
                     "f2": {"name": "notes.txt", "parent": "le", "content": b"-"},
                     "f3": {"name": "y.csv", "parent": "x", "content": b"-"}})
    return cloud.Drive("root", http=http), http, tmp_path


def test_pull_db_downloads_only_when_different(drive):
    d, http, tmp = drive
    md5 = cloud.pull_db(d)
    assert (tmp / "database.sqlite").read_bytes() == b"base drive" and md5 == hashlib.md5(b"base drive").hexdigest()
    (tmp / "database.sqlite").write_bytes(b"base drive")
    assert cloud.pull_db(d) == md5


def test_push_db_only_when_changed(drive):
    d, http, tmp = drive
    before = cloud.pull_db(d)
    assert cloud.push_db(d, before) is False and http.uploads == []
    (tmp / "database.sqlite").write_bytes(b"nouvelle base")
    assert cloud.push_db(d, before) is True and http.files["db"]["content"] == b"nouvelle base"


def test_missing_db_explains_initial_upload(drive):
    d, http, _ = drive
    del http.files["db"]
    with pytest.raises(cloud.CloudError, match="déposez-y une première fois"):
        cloud.pull_db(d)


def test_pull_imports_copies_csv_of_known_sources(drive):
    d, _, tmp = drive
    local = tmp / "imports" / "lineupexperts"
    assert cloud.pull_imports(d, {"lineupexperts": str(local)}) == 1
    assert (local / "le_ros.csv").read_bytes() == b"a;b" and not (local / "notes.txt").exists()
    assert cloud.pull_imports(d, {"lineupexperts": str(local)}) == 0     # déjà à jour


def test_local_sync_disabled_inside_runner(monkeypatch):
    league = {"google_sheets": {"drive_sync": True, "drive_folder_id": "abc"}}
    assert cloud.local_sync_enabled(league) is True
    monkeypatch.setenv(cloud.RUNNER_ENV, "1")
    assert cloud.local_sync_enabled(league) is False
    monkeypatch.delenv(cloud.RUNNER_ENV)
    assert cloud.local_sync_enabled({"google_sheets": {"drive_sync": True}}) is False


class FakeDrive:
    calls = []

    def __init__(self, folder):
        assert folder == "abc"


def _patch_cloud(monkeypatch, calls):
    monkeypatch.setattr(cloud, "pull_db", lambda d: calls.append("pull_db") or "md5")
    monkeypatch.setattr(cloud, "pull_imports", lambda d: calls.append("pull_imports") or 0)
    monkeypatch.setattr(cloud, "push_db", lambda d, before: calls.append(("push_db", before)) or True)


LEAGUE = {"google_sheets": {"drive_folder_id": "abc"}}


def test_execute_syncs_db_around_steps(monkeypatch):
    calls = []
    _patch_cloud(monkeypatch, calls)

    def step(args, env):
        assert env[cloud.RUNNER_ENV] == "1"
        calls.append(args)
        return 0, []

    assert run.execute("projections_update", "k", None, LEAGUE, step_runner=step, drive_factory=FakeDrive) == 0
    assert calls == ["pull_db", "pull_imports", ("main.py", "--push-sheet"), ("push_db", "md5")]


def test_execute_stops_at_failed_step_and_keeps_drive_db(monkeypatch):
    calls = []
    _patch_cloud(monkeypatch, calls)
    steps = iter([(0, []), (1, ["ERROR boom"])])
    assert run.execute("season_all", "k", None, LEAGUE, step_runner=lambda a, e: (calls.append(a), next(steps))[1],
                       drive_factory=FakeDrive) == 1
    assert calls == ["pull_db", ("-m", "scripts.yahoo", "rosters"), ("-m", "scripts.yahoo", "stats")]


def test_read_only_and_no_db_commands(monkeypatch):
    calls = []
    _patch_cloud(monkeypatch, calls)
    ok = lambda a, e: (calls.append(a), (0, []))[1]
    assert run.execute("season_moves", "k", None, LEAGUE, step_runner=ok, drive_factory=FakeDrive) == 0
    assert calls == ["pull_db", ("-m", "scripts.season", "moves")]
    calls.clear()
    assert run.execute("settings_check", "k", None, {}, step_runner=ok, drive_factory=FakeDrive) == 0
    assert calls == [("-m", "scripts.config_sheet", "check")]


def test_journal_row_is_updated_by_key():
    class Sheet:
        def __init__(self):
            self.cells = {1: run.RUNS_HEADER, 2: ["20261017-k", "2026-10-17 10:00:00", "Saison > Moves", "demandé"]}

        def col_values(self, col):
            return [self.cells[r][0] for r in sorted(self.cells)]

        def update(self, values, range_name):
            row = int(re.search(r"\d+", range_name).group())
            start = ord(range_name[0]) - ord("A")
            current = list(self.cells.get(row, [])) + [""] * 9
            current[start:start + len(values[0])] = values[0]
            self.cells[row] = current[:9]

    class Book:
        sheet = Sheet()

        def worksheet(self, tab):
            return self.sheet

    book = Book()
    journal = run.Journal(book, "runs", "20261017-k", "Saison > Moves")
    assert journal.row == 2
    journal.update("OK", run.now(), run.now())
    assert book.sheet.cells[2][3] == "OK" and book.sheet.cells[2][0] == "20261017-k"
    assert json.dumps(book.sheet.cells[2])


def test_history_snapshot_syncs_history_file(monkeypatch):
    calls = []
    _patch_cloud(monkeypatch, calls)
    monkeypatch.setattr(cloud, "pull_history", lambda d: calls.append("pull_history") or "h0")
    monkeypatch.setattr(cloud, "push_history", lambda d, before: calls.append(("push_history", before)) or True)
    ok = lambda a, e: (calls.append(a), (0, []))[1]
    assert run.execute("history_snapshot", "k", None, LEAGUE, step_runner=ok, drive_factory=FakeDrive) == 0
    assert calls == ["pull_db", "pull_imports", "pull_history", ("-m", "scripts.history", "daily"),
                     ("push_history", "h0")]
