"""NCAA <-> ESPN game crosswalk.

League binding for :mod:`sportsdataverse.scrape.ncaa.espn_game_xwalk` (sdv-py #328).

There is no logic here. The engine takes ``league`` as a *required* keyword on
every entry point -- a shared engine that defaults one is how a run silently
reads the other league's tree -- so this module re-exports the engine's surface
with this repo's league already bound.

The re-export is dynamic on purpose: an explicit name list is a second thing to
maintain, and the failure mode when it drifts (a name silently missing after an
engine change) is worse than the loss of grep-ability. ``dir(_engine)`` is the
contract.

``_engine`` is the module the names come from -- patch THERE in tests
(``monkeypatch.setattr(mod._engine, ...)``), because the engine resolves its
own globals; patching this shim's namespace would have no effect.
"""

from functools import partial
import inspect as _inspect
import pathlib as _pathlib

from sportsdataverse.scrape.ncaa import espn_game_xwalk as _engine

#: This repo's league. The engine keys every league-specific rule off it.
LEAGUE = "wbb"


def _needs_league(obj: object) -> bool:
    """True for an engine callable whose ``league`` keyword has no default."""
    if not _inspect.isfunction(obj):
        return False
    param = _inspect.signature(obj).parameters.get("league")
    # KEYWORD-ONLY and undefaulted -- that exact shape is what the extraction
    # created for the entry points a caller may omit. Functions that take
    # `league` POSITIONALLY (e.g. write_parsed(root, league, ...)) always
    # required it explicitly; binding those would break every positional call.
    return (
        param is not None
        and param.default is _inspect.Parameter.empty
        and param.kind is _inspect.Parameter.KEYWORD_ONLY
    )


for _name in dir(_engine):
    if _name.startswith("__"):
        continue
    _obj = getattr(_engine, _name)
    globals()[_name] = partial(_obj, league=LEAGUE) if _needs_league(_obj) else _obj
del _name, _obj


#: This repo's root -- the engine never infers it from its own location
#: (it lives in sdv-py, not here), so every CLI is handed this explicitly.
REPO_ROOT = str(_pathlib.Path(__file__).resolve().parents[1])


def _main() -> None:
    """CLI entry point -- ``--league`` and ``--root`` default to this repo."""
    _engine._main(LEAGUE, REPO_ROOT)


if __name__ == "__main__":
    _main()
