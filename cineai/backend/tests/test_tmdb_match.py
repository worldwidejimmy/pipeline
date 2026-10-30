"""T0: picking the search result the user meant (tmdb_client.best_match)."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("httpx",):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = types.ModuleType(name)
for name, attrs in (("dotenv", {"load_dotenv": lambda *a, **k: None}),):
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

# Load the real file: other tests stub `src.tools.tmdb_client` in sys.modules.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "tmdb_client_real", Path(__file__).resolve().parents[1] / "src" / "tools" / "tmdb_client.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
best_match = _mod.best_match

HEAT = [  # real /search/multi order for "Heat", 2026-10-30
    {"id": 30188, "title": "WWE Heat", "year": "1998", "vote_count": 40, "media_type": "tv"},
    {"id": 949, "title": "Heat", "year": "1995", "vote_count": 7800, "media_type": "movie"},
    {"id": 3180, "title": "Night Heat", "year": "1985", "vote_count": 12, "media_type": "tv"},
]


def test_exact_title_beats_popularity_order():
    assert best_match(HEAT, "Heat")["id"] == 949


def test_year_disambiguates_remakes():
    rs = [{"id": 1, "title": "Dune", "year": "2021", "vote_count": 12000},
          {"id": 2, "title": "Dune", "year": "1984", "vote_count": 3000}]
    assert best_match(rs, "Dune")["id"] == 1                     # most votes when no year
    assert best_match(rs, "Dune", 1984)["id"] == 2


def test_punctuation_and_case_insensitive():
    rs = [{"id": 5, "title": "Spider-Man: No Way Home", "year": "2021", "vote_count": 20000},
          {"id": 6, "title": "Spider-Man", "year": "2002", "vote_count": 18000}]
    assert best_match(rs, "spider man")["id"] == 6


def test_no_exact_match_keeps_tmdb_order_on_ties():
    rs = [{"id": 7, "title": "The Dark Knight"}, {"id": 8, "title": "Batman Begins"}]
    assert best_match(rs, "batman movies")["id"] == 7
    assert best_match([], "x") is None
