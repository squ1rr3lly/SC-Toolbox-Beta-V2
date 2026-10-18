"""Game.log facts for a bug report. No live log, no keys."""
from __future__ import annotations

import json
from pathlib import Path

from core.session import parse_log, write_context

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "game.log"


def _log(tmp_path: Path) -> Path:
    dest = tmp_path / "StarCitizen" / "LIVE" / "Game.log"
    dest.parent.mkdir(parents=True)
    dest.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_parse_log_keeps_the_latest_facts(tmp_path: Path) -> None:
    ctx = parse_log(str(_log(tmp_path)))
    assert ctx.channel == "LIVE"
    assert ctx.build == "12913470"
    assert ctx.version == "4.10.2-live.12913470"
    assert ctx.player_name == "ProjectGegnome"
    assert ctx.player_geid == "204715025323"
    assert ctx.shard == "shard-alpha"
    assert ctx.location_raw == "RR_HUR_L2"
    assert ctx.location_name == "HUR-L2"
    assert ctx.star_system == "Stanton"
    assert ctx.ship == "RSI Constellation Andromeda"
    assert ctx.aboard is True
    assert ctx.jurisdiction == "Hurston"
    assert ctx.objective == "Defend the ship"


def test_context_json_round_trip(tmp_path: Path) -> None:
    ctx = parse_log(str(_log(tmp_path)))
    folder = tmp_path / "issue"
    folder.mkdir()
    dest = write_context(folder, ctx)
    saved = json.loads(Path(dest).read_text(encoding="utf-8"))
    assert saved["build"] == "12913470"
    assert saved["location_name"] == "HUR-L2"
    assert saved["aboard"] is True
