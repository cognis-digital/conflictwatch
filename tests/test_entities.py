"""Tests for conflictwatch.entities — actor/entity resolution and the registry.
Deterministic, offline; events built in-memory."""

from __future__ import annotations

from conflictwatch import entities
from conflictwatch.events import ConflictEvent


def _ev(**kw):
    kw.setdefault("country", "Ukraine")
    kw.setdefault("event_type", "drone/uas")
    return ConflictEvent(**kw)


# --- clean_surface -----------------------------------------------------------
def test_clean_surface_collapses_whitespace():
    assert entities.clean_surface("  Russian   Armed  Forces ") == "Russian Armed Forces"


def test_clean_surface_trims_edge_noise():
    assert entities.clean_surface("the Wagner Group") == "Wagner Group"


def test_clean_surface_trailing_noise():
    assert entities.clean_surface("Wagner Group of") == "Wagner Group"


def test_clean_surface_strips_punctuation():
    assert entities.clean_surface("  IDF. ") == "IDF"


def test_clean_surface_empty():
    assert entities.clean_surface("") == ""


def test_clean_surface_all_noise():
    assert entities.clean_surface("the unknown suspected") == ""


def test_clean_surface_preserves_internal_words():
    assert entities.clean_surface("Forces of Russia") == "Forces of Russia"


# --- canonical_actor ---------------------------------------------------------
def test_canonical_alias_russian_forces():
    assert entities.canonical_actor("russian forces") == "Russian Armed Forces"


def test_canonical_alias_case_insensitive():
    assert entities.canonical_actor("RUSSIAN TROOPS") == "Russian Armed Forces"


def test_canonical_alias_idf():
    assert entities.canonical_actor("idf") == "Israel Defense Forces"


def test_canonical_alias_isis():
    assert entities.canonical_actor("ISIS") == "Islamic State"


def test_canonical_alias_houthis():
    assert entities.canonical_actor("Houthis") == "Houthi Movement"


def test_canonical_alias_with_edge_noise():
    assert entities.canonical_actor("the russian forces") == "Russian Armed Forces"


def test_canonical_unknown_titlecased():
    assert entities.canonical_actor("azov regiment") == "Azov Regiment"


def test_canonical_keeps_acronym():
    # short all-caps tokens are preserved as acronyms
    out = entities.canonical_actor("SDF militia")
    assert "SDF" in out


def test_canonical_small_words_lowercased():
    assert entities.canonical_actor("army of the north") == "Army of the North"


def test_canonical_empty():
    assert entities.canonical_actor("") == ""


def test_canonical_idempotent():
    once = entities.canonical_actor("russian forces")
    assert entities.canonical_actor(once) == once


def test_canonical_known_value_passthrough():
    assert entities.canonical_actor("Russian Armed Forces") == "Russian Armed Forces"


def test_canonical_custom_aliases():
    al = {"xyz": "Canonical XYZ"}
    assert entities.canonical_actor("XYZ", aliases=al) == "Canonical XYZ"


# --- resolve -----------------------------------------------------------------
def test_resolve_prefers_known_exact():
    known = ["Azov Regiment", "Wagner Group"]
    assert entities.resolve("azov regiment", known) == "Azov Regiment"


def test_resolve_fuzzy_token_overlap():
    known = ["Azov Regiment"]
    # "Azov Brigade" shares the discriminating token "azov"
    assert entities.resolve("Azov Brigade", known) == "Azov Regiment"


def test_resolve_below_threshold_keeps_canonical():
    known = ["Wagner Group"]
    out = entities.resolve("Azov Regiment", known, threshold=0.9)
    assert out == "Azov Regiment"


def test_resolve_alias_wins_first():
    known = ["Some Other Force"]
    assert entities.resolve("russian forces", known) == "Russian Armed Forces"


def test_resolve_empty():
    assert entities.resolve("", ["Wagner Group"]) == ""


def test_resolve_no_known():
    assert entities.resolve("Azov Regiment", []) == "Azov Regiment"


def test_resolve_generic_only_no_false_merge():
    # "Armed Forces" is all generic tokens -> should not fuzzily swallow a specific name
    known = ["Azov Regiment"]
    assert entities.resolve("armed forces", known) != "Azov Regiment"


def test_resolve_tie_breaks_alphabetically():
    known = ["Alpha Brigade", "Zeta Brigade"]
    # a mention sharing nothing discriminating stays canonical
    out = entities.resolve("Omega Battalion", known)
    assert out == "Omega Battalion"


# --- EntityRegistry basics ---------------------------------------------------
def test_registry_len_and_names():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="russian forces"),
        _ev(actor1="ukrainian forces"),
    ])
    assert len(reg) == 2
    assert reg.names() == ["Armed Forces of Ukraine", "Russian Armed Forces"]


def test_registry_folds_surface_forms():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="russian forces"),
        _ev(actor1="RU forces"),
        _ev(actor1="Russian troops"),
    ])
    assert len(reg) == 1
    prof = reg.profile("Russian Armed Forces")
    assert prof["mentions"] == 3
    assert len(prof["surface_forms"]) >= 2


def test_registry_mentions_count():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="Wagner Group"), _ev(actor1="wagner"),
    ])
    assert reg.profile("Wagner Group")["mentions"] == 2


def test_registry_first_last_seen():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="Wagner Group", date="2026-06-10"),
        _ev(actor1="Wagner Group", date="2026-06-01"),
        _ev(actor1="Wagner Group", date="2026-06-20"),
    ])
    p = reg.profile("Wagner Group")
    assert p["first_seen"] == "2026-06-01" and p["last_seen"] == "2026-06-20"


def test_registry_event_types():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="Wagner Group", event_type="battle"),
        _ev(actor1="Wagner Group", event_type="battle"),
        _ev(actor1="Wagner Group", event_type="drone/uas"),
    ])
    et = reg.profile("Wagner Group")["event_types"]
    assert et["battle"] == 2 and et["drone/uas"] == 1


def test_registry_platforms_from_tags():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="Wagner Group", tags=["artillery", "extracted:high"]),
    ])
    assert reg.profile("Wagner Group")["platforms"].get("artillery") == 1


def test_registry_ignores_meta_tags():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="Wagner Group", tags=["extracted:high", "merged:2", "wounded:3"]),
    ])
    assert reg.profile("Wagner Group")["platforms"] == {}


def test_registry_countries():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="Islamic State", country="Iraq"),
        _ev(actor1="Islamic State", country="Syria"),
        _ev(actor1="Islamic State", country="Iraq"),
    ])
    c = reg.profile("Islamic State")["countries"]
    assert c["Iraq"] == 2 and c["Syria"] == 1


def test_registry_coactors():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="russian forces", actor2="ukrainian forces"),
    ])
    p = reg.profile("Russian Armed Forces")
    assert "Armed Forces of Ukraine" in p["co_actors"]


def test_registry_coactor_weight():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="russian forces", actor2="ukrainian forces"),
        _ev(actor1="ru forces", actor2="afu"),
    ])
    p = reg.profile("Russian Armed Forces")
    assert p["co_actors"]["Armed Forces of Ukraine"] == 2


def test_registry_no_self_coactor():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="russian forces", actor2="russian troops"),
    ])
    p = reg.profile("Russian Armed Forces")
    assert "Russian Armed Forces" not in p["co_actors"]


def test_registry_contains():
    reg = entities.EntityRegistry.from_events([_ev(actor1="idf")])
    assert "idf" in reg and "Israel Defense Forces" in reg
    assert "nonexistent force" not in reg


def test_registry_profile_unknown_none():
    reg = entities.EntityRegistry.from_events([_ev(actor1="idf")])
    assert reg.profile("nobody") is None


def test_registry_empty_actors_skipped():
    reg = entities.EntityRegistry.from_events([_ev(actor1="", actor2="")])
    assert len(reg) == 0


def test_registry_add_returns_names():
    reg = entities.EntityRegistry()
    names = reg.add(_ev(actor1="idf", actor2="hamas"))
    assert names == ["Israel Defense Forces", "Hamas"]


# --- top / cooccurrence / summary --------------------------------------------
def test_top_orders_by_mentions():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="idf"), _ev(actor1="idf"), _ev(actor1="hamas"),
    ])
    top = reg.top(2)
    assert top[0]["name"] == "Israel Defense Forces" and top[0]["mentions"] == 2


def test_top_limit():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="idf"), _ev(actor1="hamas"), _ev(actor1="wagner"),
    ])
    assert len(reg.top(2)) == 2


def test_top_includes_countries():
    reg = entities.EntityRegistry.from_events([_ev(actor1="idf", country="Israel")])
    assert reg.top(1)[0]["countries"] == ["Israel"]


def test_cooccurrence_edges():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="idf", actor2="hamas"),
        _ev(actor1="idf", actor2="hamas"),
    ])
    edges = reg.cooccurrence()
    assert edges[0]["weight"] == 2
    assert edges[0]["a"] < edges[0]["b"]


def test_cooccurrence_min_weight():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="idf", actor2="hamas"),
    ])
    assert reg.cooccurrence(min_weight=2) == []


def test_cooccurrence_empty_when_no_pairs():
    reg = entities.EntityRegistry.from_events([_ev(actor1="idf")])
    assert reg.cooccurrence() == []


def test_summary_counts():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="idf"), _ev(actor1="idf"), _ev(actor1="hamas"),
    ])
    s = reg.summary()
    assert s["entities"] == 2 and s["mentions"] == 3
    assert len(s["top"]) <= 5


def test_summary_distinct_surface_forms():
    reg = entities.EntityRegistry.from_events([
        _ev(actor1="russian forces"), _ev(actor1="ru forces"),
    ])
    assert reg.summary()["distinct_surface_forms"] == 2


# --- fuzzy off ---------------------------------------------------------------
def test_registry_fuzzy_off_keeps_distinct():
    reg = entities.EntityRegistry(fuzzy=False)
    reg.add(_ev(actor1="Azov Regiment"))
    reg.add(_ev(actor1="Azov Brigade"))
    assert len(reg) == 2


def test_registry_fuzzy_on_merges_near():
    reg = entities.EntityRegistry(fuzzy=True, threshold=0.4)
    reg.add(_ev(actor1="Azov Regiment"))
    reg.add(_ev(actor1="Azov Brigade"))
    assert len(reg) == 1


# --- canonicalize_events (non-destructive) -----------------------------------
def test_canonicalize_events_rewrites_actors():
    src = [_ev(actor1="russian forces", actor2="afu")]
    out = entities.canonicalize_events(src)
    assert out[0].actor1 == "Russian Armed Forces"
    assert out[0].actor2 == "Armed Forces of Ukraine"


def test_canonicalize_events_non_destructive():
    src = [_ev(actor1="russian forces")]
    entities.canonicalize_events(src)
    assert src[0].actor1 == "russian forces"


def test_canonicalize_events_preserves_other_fields():
    src = [_ev(actor1="idf", country="Israel", fatalities=3, notes="strike")]
    out = entities.canonicalize_events(src)
    assert out[0].country == "Israel" and out[0].fatalities == 3
    assert out[0].notes == "strike"


def test_canonicalize_events_empty():
    assert entities.canonicalize_events([]) == []


# --- build_registry convenience ----------------------------------------------
def test_build_registry_convenience():
    reg = entities.build_registry([_ev(actor1="idf")])
    assert isinstance(reg, entities.EntityRegistry) and len(reg) == 1


def test_registry_determinism_regardless_of_order():
    evs = [_ev(actor1="idf"), _ev(actor1="hamas"), _ev(actor1="idf")]
    a = entities.EntityRegistry.from_events(evs).summary()
    b = entities.EntityRegistry.from_events(list(reversed(evs))).summary()
    assert a["entities"] == b["entities"] and a["mentions"] == b["mentions"]
