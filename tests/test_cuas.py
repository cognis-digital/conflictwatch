"""Counter-UAS knowledge base: load/query/stats, CLI, and scope guard."""

from __future__ import annotations

import json

from conflictwatch import cuas
from conflictwatch.cli import main


def test_kb_size_and_shape():
    e = cuas.entries()
    assert len(e) >= 80, f"expected a substantial KB, got {len(e)}"
    for x in e[:40]:
        assert x["title"] and x["topic"] in cuas.TOPICS
        assert isinstance(x["key_facts"], list) and isinstance(x["countermeasures"], list)
        assert isinstance(x["sources"], list)


def test_topics_cover_the_key_areas():
    t = cuas.topics()
    for must in ("fiber-optic-drones", "acoustic-detection", "ew-jamming",
                 "interceptor-drones", "counter-shahed"):
        assert t.get(must, 0) >= 1


def test_query_topic_and_keyword():
    fo = cuas.query(topic="fiber-optic-drones")
    assert fo and all(e["topic"] == "fiber-optic-drones" for e in fo)
    assert cuas.query(keyword="acoustic")        # keyword hits something


def test_named_systems_present():
    blob = " ".join(cuas.systems()).lower()
    # well-known 2024-2026 systems should appear somewhere in the corpus
    assert "sky fortress" in blob or "zvook" in blob
    assert "shahed" in json.dumps(cuas.entries()).lower()


def test_stats():
    s = cuas.stats()
    assert s["total"] == len(cuas.entries())
    assert s["unique_sources"] >= 50 and s["named_systems"] >= 30


def test_scope_guard_no_build_instructions():
    """KB is detection/defense awareness - not a build/guidance/targeting guide."""
    blob = json.dumps(cuas.entries()).lower()
    for bad in ("step-by-step build", "how to build a jammer", "wiring diagram",
                "assemble the warhead", "detonator"):
        assert bad not in blob


def test_cli_cuas_stats_and_query(capsys):
    assert main(["cuas", "--stats"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] >= 80
    assert main(["cuas", "--topic", "fiber-optic-drones", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data and all(d["topic"] == "fiber-optic-drones" for d in data)


def test_cli_cuas_systems(capsys):
    assert main(["cuas", "--systems"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) >= 30
