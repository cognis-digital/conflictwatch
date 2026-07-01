"""Tests for conflictwatch.reports — markdown / csv / kml / intsum briefs.
Deterministic, offline; validates structure without external parsers where
possible, and uses xml.dom.minidom to confirm the KML is well-formed."""

from __future__ import annotations

import csv
import io
import os
from xml.dom import minidom

from conflictwatch import reports
from conflictwatch.events import ConflictEvent
from conflictwatch.sources import parse_generic_json
from conflictwatch.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "demos", "sample_escalation.json")
GEO_FIXTURE = os.path.join(ROOT, "demos", "sample_correlation.json")


def _fixture(path=FIXTURE):
    with open(path, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


# --- formats registry --------------------------------------------------------
def test_formats_known():
    assert set(reports.FORMATS) == {"markdown", "csv", "kml", "intsum"}


def test_render_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        reports.render(_fixture(), "pdf")


# --- markdown ----------------------------------------------------------------
def test_markdown_has_sections():
    md = reports.to_markdown(_fixture(), title="Test Brief")
    assert md.startswith("# Test Brief")
    for section in ("## Severity mix", "## Hotspots", "## Most-active actors",
                    "## Event mix"):
        assert section in md
    assert "| country | area | events | fatalities |" in md


def test_markdown_reports_totals():
    md = reports.to_markdown(_fixture())
    assert "**Events:** 85" in md


# --- csv ---------------------------------------------------------------------
def test_csv_parses_and_has_header():
    text = reports.to_csv(_fixture())
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["date", "events", "fatalities"]
    assert len(rows) > 1
    # every data row: date, int, int
    for r in rows[1:]:
        assert len(r) == 3 and int(r[1]) >= 0 and int(r[2]) >= 0


def test_csv_dates_sorted():
    text = reports.to_csv(_fixture())
    rows = list(csv.reader(io.StringIO(text)))[1:]
    dates = [r[0] for r in rows]
    assert dates == sorted(dates)


# --- kml ---------------------------------------------------------------------
def test_kml_is_well_formed_xml():
    kml = reports.to_kml(_fixture(GEO_FIXTURE))
    dom = minidom.parseString(kml)  # raises if malformed
    assert dom.getElementsByTagName("Placemark")
    assert dom.getElementsByTagName("kml")


def test_kml_skips_ungeolocated():
    # escalation fixture has no coordinates -> zero placemarks, still valid xml
    kml = reports.to_kml(_fixture())
    dom = minidom.parseString(kml)
    assert dom.getElementsByTagName("Placemark") == []


def test_kml_has_severity_styles():
    kml = reports.to_kml(_fixture(GEO_FIXTURE))
    for sev in ("critical", "high", "medium", "low", "info"):
        assert f'id="sev-{sev}"' in kml


def test_kml_escapes_special_chars():
    evs = [ConflictEvent(date="2026-06-01", country="X", location="A & B <co>",
                         lat=1.0, lon=2.0, actor1="R&D")]
    kml = reports.to_kml(evs)
    minidom.parseString(kml)  # must not raise despite & and < in data
    assert "&amp;" in kml


# --- intsum ------------------------------------------------------------------
def test_intsum_has_bluf_and_sections():
    txt = reports.to_intsum(_fixture(), area="TEST AOI")
    assert "INTSUM — TEST AOI" in txt
    for section in ("1. BLUF", "2. SITUATION", "3. ASSESSMENT"):
        assert section in txt


def test_intsum_empty_events():
    txt = reports.to_intsum([])
    assert "No dated events" in txt


# --- CLI ---------------------------------------------------------------------
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_brief_markdown(capsys):
    rc, out = _run(["brief", FIXTURE, "--to", "markdown"], capsys)
    assert rc == 0 and out.startswith("# Situational Report")


def test_cli_brief_intsum(capsys):
    rc, out = _run(["brief", FIXTURE, "--to", "intsum"], capsys)
    assert rc == 0 and "BLUF" in out


def test_cli_brief_csv(capsys):
    rc, out = _run(["brief", FIXTURE, "--to", "csv"], capsys)
    assert rc == 0 and out.startswith("date,events,fatalities")


def test_cli_brief_kml_to_file(tmp_path, capsys):
    out_path = str(tmp_path / "map.kml")
    rc, _ = _run(["brief", GEO_FIXTURE, "--to", "kml", "-o", out_path], capsys)
    assert rc == 0
    with open(out_path, encoding="utf-8") as fh:
        minidom.parseString(fh.read())


def test_cli_brief_rejects_bad_format(capsys):
    import pytest
    with pytest.raises(SystemExit):
        _run(["brief", FIXTURE, "--to", "docx"], capsys)
