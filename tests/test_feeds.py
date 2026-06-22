"""Offline tests for the edge data-feed layer + OFAC SDN sanctions enrichment.

These tests NEVER touch the network: COGNIS_FEEDS_CACHE is pointed at the committed
trimmed snapshot under tests/fixtures/feeds_cache, and all reads use offline=True.
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE_CACHE = os.path.join(HERE, "fixtures", "feeds_cache")
EVENTS = os.path.join(ROOT, "demos", "sample_events_sanctions.json")


@pytest.fixture(autouse=True)
def _offline_cache(monkeypatch):
    """Force the bundled datafeeds module to use the committed fixture cache."""
    monkeypatch.setenv("COGNIS_FEEDS_CACHE", FIXTURE_CACHE)
    yield


def _load_events():
    from conflictwatch.sources import parse_generic_json
    with open(EVENTS, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


# --- catalog / bundled module ------------------------------------------------
def test_bundled_catalog_present_and_has_relevant_feeds():
    from conflictwatch import datafeeds as df
    ids = {f["id"] for f in df.list_feeds()}
    assert {"gdelt", "ofac-sdn"}.issubset(ids)


def test_cli_relevant_feeds_only():
    from conflictwatch.cli import RELEVANT_FEEDS
    assert set(RELEVANT_FEEDS) == {"gdelt", "ofac-sdn"}


def test_offline_get_serves_from_cache_no_network():
    from conflictwatch import datafeeds as df
    sdn = df.get("ofac-sdn", offline=True)
    assert isinstance(sdn, str) and "WAGNER GROUP" in sdn
    gdelt = df.get("gdelt", offline=True)
    assert ".export.CSV.zip" in gdelt


def test_offline_get_missing_feed_raises():
    from conflictwatch import datafeeds as df
    with pytest.raises(FileNotFoundError):
        df.get("aws-ip-ranges", offline=True)  # not in fixture cache


# --- OFAC SDN parsing + index ------------------------------------------------
def test_parse_sdn_csv_handles_placeholder_and_columns():
    from conflictwatch import sanctions
    text = open(os.path.join(FIXTURE_CACHE, "ofac-sdn.data"), encoding="utf-8").read()
    ents = sanctions.parse_sdn_csv(text)
    assert len(ents) == 6
    wagner = next(e for e in ents if e["sdn_name"] == "WAGNER GROUP")
    assert wagner["title"] == ""        # "-0-" placeholder normalized to empty
    vessel = next(e for e in ents if e["sdn_type"] == "vessel")
    assert vessel["sdn_name"] == "STENA IMPERO"


def test_index_matches_names_and_aliases():
    from conflictwatch import sanctions
    idx = sanctions.load_index(offline=True)
    assert len(idx) == 6
    assert [m["sdn_name"] for m in idx.match("Wagner Group")] == ["WAGNER GROUP"]
    # alias resolution from the remarks field
    assert idx.match("Houthis")[0]["sdn_name"] == "ANSARALLAH"
    assert idx.match("Hezbollah")[0]["sdn_name"] == "HIZBALLAH"
    assert idx.match("IRGC")[0]["sdn_name"] == "ISLAMIC REVOLUTIONARY GUARD CORPS"


def test_index_no_false_positive_on_generic_actor():
    from conflictwatch import sanctions
    idx = sanctions.load_index(offline=True)
    assert idx.match("Local Defense Forces") == []
    assert idx.match("Police") == []
    assert idx.match("") == []


def test_strong_match_flag():
    from conflictwatch import sanctions
    idx = sanctions.load_index(offline=True)
    hit = idx.match("Wagner Group")[0]
    assert hit["strong"] is True and hit["program"]


# --- end-to-end enrichment ---------------------------------------------------
def test_screen_events_flags_sanctioned_actors_only():
    from conflictwatch import sanctions
    events = _load_events()
    flagged = sanctions.screen_events(events, offline=True)
    # Wagner, Hizballah, Houthis -> 3 events; the Nairobi protest is clean
    names = sorted(m["sdn_name"] for f in flagged for m in f["matches"])
    assert names == ["ANSARALLAH", "HIZBALLAH", "WAGNER GROUP"]
    assert all(f["matches"] for f in flagged)


# --- air-gap snapshot round-trip --------------------------------------------
def test_snapshot_export_import_roundtrip(tmp_path, monkeypatch):
    from conflictwatch import datafeeds as df
    snap = tmp_path / "feeds.tar.gz"
    n = df.snapshot_export(str(snap))
    assert n >= 2 and snap.exists()
    # import into a fresh empty cache dir, then read offline
    monkeypatch.setenv("COGNIS_FEEDS_CACHE", str(tmp_path / "enclave"))
    imported = df.snapshot_import(str(snap))
    assert imported >= 2
    assert "WAGNER GROUP" in df.get("ofac-sdn", offline=True)


# --- CLI ---------------------------------------------------------------------
def _run_cli(argv, capsys):
    from conflictwatch.cli import main
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_feeds_list(capsys):
    rc, out = _run_cli(["feeds", "list"], capsys)
    assert rc == 0
    assert "ofac-sdn" in out and "gdelt" in out
    assert "treasury.gov" in out  # real source URL documented


def test_cli_feeds_get_offline(capsys):
    rc, out = _run_cli(["feeds", "get", "ofac-sdn", "--offline"], capsys)
    assert rc == 0 and "WAGNER GROUP" in out


def test_cli_feeds_rejects_unrelated_feed(capsys):
    # argparse choices restrict to the relevant feeds -> SystemExit
    with pytest.raises(SystemExit):
        _run_cli(["feeds", "get", "cisa-kev", "--offline"], capsys)


def test_cli_sanctions_table_and_json(capsys):
    rc, out = _run_cli(["sanctions", EVENTS, "--offline"], capsys)
    assert rc == 0 and "WAGNER GROUP" in out and "ANSARALLAH" in out

    rc, out = _run_cli(["sanctions", EVENTS, "--offline", "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert sum(len(f["matches"]) for f in data) == 3
