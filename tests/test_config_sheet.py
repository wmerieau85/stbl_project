import json

import pytest

from scripts import config_sheet as C
from scripts.config import load_bootstrap, load_config


def _cells(rows):
    return [[str(c) if c is not None else "" for c in r] for r in rows]


@pytest.fixture
def cfg():
    return load_config(), load_bootstrap()


def test_round_trip_is_lossless(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))
    c2, b2 = C.parse_rows(rows, config, boot)
    assert (c2, b2) == (config, boot)
    assert set(c2["grids"]) >= {"draft", "lt", "st"}


def test_sheet_alone_rebuilds_sources_and_grids(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))
    bare = {"league": {}, "settings": {}, "grids": {}, "aliases": {},
            "sources": {n: {"code": s["code"], ("urls" if "urls" in s else "files"): {}}
                        for n, s in config["sources"].items()}}
    c2, _ = C.parse_rows(rows, bare, boot)
    assert c2["sources"] == config["sources"]
    assert c2["grids"] == config["grids"]


def test_order_is_free_and_aliases_accepted(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))[1:]
    rows.reverse()
    rows = [r for r in rows if r[:2] != ["Sheets | Season", "Rosters Source"]]
    rows.append(["Sheets | Season", "Choices Source", "sheet", ""])
    c2, _ = C.parse_rows(rows, config, boot)
    assert c2["league"]["draft"]["order"] == config["league"]["draft"]["order"]
    assert c2["league"]["season"]["roster_source"] == "sheet"


def test_per_stage_activation(cfg):
    config, boot = cfg
    rows = [r for r in _cells(C.build_rows(config, boot))
            if not (r[0] == "Sources | Active" and r[1] == "ros" and r[2] == "cbs")]
    c2, _ = C.parse_rows(rows, config, boot)
    assert c2["settings"]["sources"]["cbs"] == {"draft": True, "ros": False}


def test_old_sheet_gets_new_draft_sources_and_weights(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))
    old_rows = [
        row for row in rows
        if not (
            (row[0] in ("Sources | Types", "Sources | Draft", "Sources | Active")
             and (row[1] in ("es", "rb") or row[2] in ("es", "rb")))
            or (row[0].startswith("Transco |") and row[1] in ("es", "rb"))
            or (row[0].startswith("Grid Draft |") and row[1] in ("es26", "rb26"))
        )
    ]
    for row in old_rows:
        if row[0] == "Grid Draft | GP":
            row[2] = {"fs26": "22,50%", "fp26": "22,50%", "cbs26": "10,00%",
                      "le26": "22,50%", "dk26": "22,50%"}.get(row[1], row[2])
        elif row[0] in ("Grid Draft | MIN", "Grid Draft | STATS"):
            row[2] = {"fs26": "20,00%", "fp26": "20,00%", "cbs26": "20,00%",
                      "le26": "20,00%", "dk26": "20,00%"}.get(row[1], row[2])

    updated, _ = C.parse_rows(old_rows, config, boot)
    settings = updated["settings"]["sources"]
    assert settings["espn"] == {"draft": True, "ros": False}
    assert settings["rotoballer"] == {"draft": True, "ros": False}
    assert updated["sources"]["espn"]["urls"]["draft"].endswith("view=kona_player_info")
    assert updated["sources"]["rotoballer"]["urls"]["draft"].endswith("format=9cat")

    draft = updated["grids"]["draft"]
    codes = draft[1][1:]
    rows_by_level = {row[0]: dict(zip(codes, row[1:])) for row in draft[2:]}
    from scripts.weighting.weights import parse_weight

    assert parse_weight(rows_by_level["GP"]["es26"]) == pytest.approx(0.1667, abs=0.0001)
    assert parse_weight(rows_by_level["GP"]["rb26"]) == 0
    assert parse_weight(rows_by_level["GP"]["fs26"]) == pytest.approx(0.1875)
    assert parse_weight(rows_by_level["STATS"]["fs26"]) == pytest.approx(0.16)
    assert parse_weight(rows_by_level["STATS"]["es26"]) == pytest.approx(0.1)
    assert parse_weight(rows_by_level["STATS"]["rb26"]) == pytest.approx(0.1)


def test_wrong_manager_count_rejected(cfg):
    config, boot = cfg
    rows = [r for r in _cells(C.build_rows(config, boot)) if not (r[0] == "Draft | Order" and r[1] == "15")]
    with pytest.raises(ValueError):
        C.parse_rows(rows, config, boot)


def test_unknown_source_code_rejected(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot)) + [["Sources | RoS", "xx", "https://x", ""]]
    with pytest.raises(ValueError):
        C.parse_rows(rows, config, boot)


def test_invalid_grid_rejected(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))
    i = next(i for i, r in enumerate(rows) if r[0] == "Grid lt | GP")
    rows[i][2] = "90,00%"
    with pytest.raises(ValueError):
        C.parse_rows(rows, json.loads(json.dumps(config)), boot)


def test_old_local_config_gets_new_sources(tmp_path, monkeypatch):
    """config.json créé avant l'ajout de 9cat : le code nc doit être reconnu dans l'onglet settings."""
    import json

    from scripts import config, config_sheet

    old = config._read_json(config.DEFAULTS_PATH)
    old["sources"] = {k: v for k, v in old["sources"].items() if k not in ("ninecat", "fantasynerds")}
    old["sources"]["fanscout"].pop("import_dir", None)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    data = config.load_config()
    assert data["sources"]["ninecat"]["code"] == "nc" and "import_dir" in data["sources"]["fanscout"]
    rows = [config_sheet.HEADER, ["Sources | Types", "nc", "URL", ""], ["Sources | Active", "draft", "nc", ""]]
    cfg, _ = config_sheet.parse_rows(rows, cfg=data)
    assert cfg["settings"]["sources"]["ninecat"]["draft"] is True


def test_invalid_active_stage_rejected(tmp_path, monkeypatch):
    from scripts import config

    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "settings": {"active_stage": "invalid"}
    }), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    with pytest.raises(ValueError, match="active_stage"):
        config.load_settings()


def test_default_configuration_is_accepted(tmp_path, monkeypatch):
    from scripts import config

    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "missing-config.json"))
    settings = config.load_settings()
    league = config.load_league()
    assert settings["active_stage"] in {"draft", "ros"}
    assert league["teams"] > 0


@pytest.mark.parametrize(
    "payload,match",
    [
        ([], "objet JSON"),
        ({"settings": []}, "settings doit être un dictionnaire"),
        ({"sources": []}, "sources doit être un dictionnaire"),
        ({"league": "invalid"}, "league doit être un dictionnaire"),
    ],
)
def test_invalid_config_structure_is_rejected_cleanly(tmp_path, monkeypatch, payload, match):
    from scripts import config

    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))

    with pytest.raises(ValueError, match=match):
        config.load_config()


@pytest.mark.parametrize(
    "sources,match",
    [
        ({"": {}}, "nom de source invalide"),
        ({"cbs": {"urls": {"draft": ""}}}, "chaîne non vide"),
        ({"cbs": {"files": {"draft": None}}}, "chaîne non vide"),
    ],
)
def test_invalid_source_configuration_is_rejected(sources, match):
    from scripts.config import validate_sources_config

    with pytest.raises(ValueError, match=match):
        validate_sources_config(sources)


def test_partial_settings_inherit_defaults(tmp_path, monkeypatch):
    from scripts import config

    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "settings": {
            "active_stage": "ros",
            "export": {"delimiter": "|"},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))

    loaded = config.load_settings()
    assert loaded["active_stage"] == "ros"
    assert loaded["active_season"] == config.DEFAULT_SETTINGS["active_season"]
    assert loaded["export"] == {"delimiter": "|", "decimal": ","}
    assert loaded["http"] == config.DEFAULT_SETTINGS["http"]


def test_legacy_boolean_source_activation_is_normalized(tmp_path, monkeypatch):
    from scripts import config

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"settings": {"sources": {"cbs": True}}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))

    assert config.load_settings()["sources"]["cbs"] == {"draft": True, "ros": True}


@pytest.mark.parametrize(
    "http,valid",
    [
        ({"retries": 0}, True),
        ({"retries": 1.5}, False),
        ({"retries": True}, False),
        ({"pause_seconds": 0}, True),
        ({"pause_seconds": True}, False),
        ({"timeout": 0}, False),
        ({"timeout": True}, False),
    ],
)
def test_http_settings_types_and_bounds(tmp_path, monkeypatch, http, valid):
    from scripts import config

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"settings": {"http": http}}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))

    if valid:
        config.load_settings()
    else:
        with pytest.raises(ValueError, match="settings.http"):
            config.load_settings()


def test_invalid_sheet_config_does_not_overwrite_local_config(tmp_path, monkeypatch):
    from scripts import config, config_sheet

    path = tmp_path / "config.json"
    current = config._read_json(config.DEFAULTS_PATH)
    path.write_text(json.dumps(current), encoding="utf-8")
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))

    invalid = json.loads(json.dumps(current))
    invalid["settings"]["active_stage"] = "invalid"
    monkeypatch.setattr(config_sheet, "parse_rows", lambda *args, **kwargs: (invalid, config.load_bootstrap()))
    monkeypatch.setattr(config_sheet, "_read_tab", lambda *args, **kwargs: [])
    monkeypatch.setattr(config_sheet, "_read_aliases", lambda *args, **kwargs: None)
    monkeypatch.setattr(config_sheet, "_check_tabs", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="active_stage"):
        config_sheet.pull(object())
    assert path.read_text(encoding="utf-8") == original


def test_valid_sheet_config_repairs_invalid_local_config(tmp_path, monkeypatch):
    from scripts import config, config_sheet

    path = tmp_path / "config.json"
    path.write_text('{"sources": []}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))

    valid = config.load_default_config()
    monkeypatch.setattr(
        config_sheet, "parse_rows", lambda *args, **kwargs: (valid, config.load_bootstrap())
    )
    monkeypatch.setattr(config_sheet, "_read_tab", lambda *args, **kwargs: [])
    monkeypatch.setattr(config_sheet, "_read_aliases", lambda *args, **kwargs: None)
    monkeypatch.setattr(config_sheet, "_check_tabs", lambda *args, **kwargs: None)

    config_sheet.pull(object())

    repaired = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(repaired["sources"], dict)
    assert repaired["settings"]["active_stage"] in {"draft", "ros"}


def test_pipeline_returns_configuration_error_for_sheet_validation(monkeypatch, caplog):
    from types import SimpleNamespace

    from scripts import config_sheet, sheets
    import main

    def reject_sheet_config(*args, **kwargs):
        raise ValueError("invalid sheet")

    monkeypatch.setattr(main, "load_bootstrap", lambda: {
        "spreadsheet_id": "spreadsheet",
        "config_tab": "settings",
        "google": {},
    })
    monkeypatch.setattr(sheets, "open_spreadsheet", lambda *args: object())
    monkeypatch.setattr(config_sheet, "pull", reject_sheet_config)

    assert main.run_pipeline(SimpleNamespace(no_sync_config=False)) == 2
    assert "Configuration invalide : Onglet config : invalid sheet" in caplog.text
