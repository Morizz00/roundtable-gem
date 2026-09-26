from app.config import api_key
from app.keys import ROLES, KeyRing

POOL = "kA,kB,kC,kD,kE,kF"


def ring(**env):
    return KeyRing.from_env(env)


# ---- assignment ---------------------------------------------------------------

def test_pool_fills_orchestrator_coder_critic_in_order_and_the_rest_are_spares():
    r = ring(GEMINI_API_KEYS=POOL)
    assert r.by_role == {"orchestrator": "kA", "coder": "kB", "critic": "kC"}
    assert r.spares == ["kD", "kE", "kF"]


def test_explicit_role_keys_win_and_the_pool_fills_the_gaps_without_duplicates():
    r = ring(GEMINI_API_KEY_CODER="kC", GEMINI_API_KEYS=POOL)
    assert r.by_role == {"coder": "kC", "orchestrator": "kA", "critic": "kB"}
    assert r.spares == ["kD", "kE", "kF"] and "kC" not in r.spares


def test_a_single_key_serves_every_role_and_has_nothing_to_rotate_to():
    r = ring(GEMINI_API_KEY="only")
    assert r.by_role == {role: "only" for role in ROLES} and r.spares == []
    assert r.rotate("coder") is None and r.key_for("coder") == "only"


def test_single_key_becomes_a_spare_when_the_pool_already_covers_every_role():
    r = ring(GEMINI_API_KEYS="kA,kB,kC", GEMINI_API_KEY="legacy")
    assert r.by_role["coder"] == "kB" and r.spares == ["legacy"]


def test_pool_shorter_than_the_roles_falls_back_to_the_single_key():
    r = ring(GEMINI_API_KEYS="kA,kB", GEMINI_API_KEY="legacy")
    assert r.by_role == {"orchestrator": "kA", "coder": "kB", "critic": "legacy"}


def test_whitespace_blank_entries_and_duplicates_are_tolerated():
    r = ring(GEMINI_API_KEYS=" kA , ,kB,,kC ,kA")
    assert r.by_role == {"orchestrator": "kA", "coder": "kB", "critic": "kC"} and r.spares == []
    assert r.all_keys() == ["kA", "kB", "kC"]


def test_no_keys_at_all():
    r = ring()
    assert r.by_role == {} and r.any_key() is None and r.key_for("coder") is None and not r.has_spare()


# ---- rotation -------------------------------------------------------------------

def test_rotate_gives_the_role_the_next_spare_and_recycles_the_old_key_to_the_back():
    r = ring(GEMINI_API_KEYS=POOL)
    assert r.rotate("coder") == "kD"
    assert r.by_role["coder"] == "kD" and r.spares == ["kE", "kF", "kB"]
    assert r.key_for("orchestrator") == "kA" and r.key_for("critic") == "kC"  # other roles untouched


def test_rotation_never_hands_two_roles_the_same_key():
    r = ring(GEMINI_API_KEYS="kA,kB,kC,kD")
    r.rotate("coder")
    r.rotate("critic")
    assert len(set(r.by_role.values())) == 3


# ---- labels and scrubbing: key material must never leave through a display path ------

def test_labels_are_stable_and_contain_no_key_material():
    r = ring(GEMINI_API_KEYS="AQ.secretA,AQ.secretB,AQ.secretC,AQ.secretD")
    assert [r.label_of(r.key_for(role)) for role in ROLES] == ["k1", "k2", "k3"]
    assert r.label_of("AQ.secretD") == "k4" and r.label_of("unknown") is None and r.label_of(None) is None
    r.rotate("coder")
    assert r.label_of(r.key_for("coder")) == "k4"  # the label follows the key, not the role
    assert all("AQ" not in (r.label_of(k) or "") for k in r.all_keys())


def test_scrub_removes_every_key_including_overlapping_prefixes():
    r = ring(GEMINI_API_KEYS="AQ.abc,AQ.abcdef,AQ.zzz")
    text = "bad key AQ.abcdef and AQ.abc and AQ.zzz"
    assert r.scrub(text) == "bad key *** and *** and ***"


# ---- config.api_key -------------------------------------------------------------

def test_api_key_helper_sees_a_pool_or_a_single_key_or_nothing(monkeypatch):
    assert api_key() is None
    monkeypatch.setenv("GEMINI_API_KEYS", "kA,kB")
    assert api_key() == "kA"
    monkeypatch.delenv("GEMINI_API_KEYS")
    monkeypatch.setenv("GEMINI_API_KEY", "solo")
    assert api_key() == "solo"
