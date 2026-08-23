from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from housemaster import cli, db
from housemaster.funda import BlockedError
from housemaster.models import FetchReport, SearchPage

from .test_models import make_listing


@pytest.fixture
def stub_page(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Serve one canned search page, recording the arguments it was asked for."""
    calls: dict[str, Any] = {}

    def fake_fetch(url: str, page: int = 1) -> SearchPage:
        calls["url"] = url
        calls["page"] = page
        return SearchPage(page=page, total_results=521, listings=[make_listing()])

    monkeypatch.setattr(cli, "fetch_search_page", fake_fetch)
    return calls


def test_search_defaults_to_the_project_target(stub_page: dict[str, Any]) -> None:
    assert cli.main(["search"]) == cli.EXIT_OK
    assert "funda.nl/zoeken/koop" in stub_page["url"]
    assert stub_page["page"] == 1


def test_page_argument_is_forwarded(stub_page: dict[str, Any]) -> None:
    cli.main(["search", "--page", "4"])
    assert stub_page["page"] == 4


def test_text_output_numbers_listings_by_absolute_position(
    stub_page: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["search", "--page", "3"])
    # Page 3 starts at listing 31, not at 1.
    assert "[ 31]" in capsys.readouterr().out


def test_json_output_is_machine_readable(
    stub_page: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["search", "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["city"] == "Den Haag"


def test_csv_output_has_a_header(
    stub_page: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["search", "--format", "csv"])
    assert capsys.readouterr().out.splitlines()[0].startswith("listing_id,")


def test_all_flag_walks_pages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "iter_all_listings", lambda *_args, **_kwargs: iter([make_listing()] * 3)
    )
    assert cli.main(["search", "--all", "--format", "json"]) == cli.EXIT_OK
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_max_pages_is_forwarded_to_the_walker(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_iter(url: str, max_pages: int | None = None) -> Any:
        seen["max_pages"] = max_pages
        return iter([])

    monkeypatch.setattr(cli, "iter_all_listings", fake_iter)
    cli.main(["search", "--all", "--max-pages", "2"])
    assert seen["max_pages"] == 2


def test_a_block_exits_nonzero_with_a_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def blocked(url: str, page: int = 1) -> SearchPage:
        raise BlockedError("no __NUXT_DATA__ -- likely the Akamai interstitial")

    monkeypatch.setattr(cli, "fetch_search_page", blocked)
    assert cli.main(["search"]) == cli.EXIT_ERROR
    assert "Akamai" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [["search", "--page", "0"], ["search", "--all", "--max-pages", "0"]],
)
def test_out_of_range_arguments_are_rejected(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == cli.EXIT_ERROR
    assert "must be 1 or greater" in capsys.readouterr().err


def test_a_missing_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_version_flag_reports_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"housemaster {cli.__version__}"


def test_bad_format_choice_is_rejected() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["search", "--format", "xml"])
    assert excinfo.value.code == 2


# --- tracking searches -----------------------------------------------------

FUNDA_URL = "https://www.funda.nl/zoeken/koop?selected_area=den-haag&floor_area=50-"


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "t.db"
    monkeypatch.setenv("HOUSEMASTER_DB", str(path))
    return path


def test_add_tracks_a_search(
    cache: Path, stub_page: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["add", FUNDA_URL]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "[1]" in out
    assert FUNDA_URL in out
    conn = db.connect(cache, read_only=True)
    try:
        assert [r["url"] for r in db.list_searches(conn)] == [FUNDA_URL]
    finally:
        conn.close()


def test_add_checks_the_url_resolves(
    cache: Path, stub_page: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    # The check is one request and it reports what the search holds.
    cli.main(["add", FUNDA_URL])
    assert "521 listings" in capsys.readouterr().out
    assert stub_page["url"] == FUNDA_URL


def test_add_refuses_a_non_funda_url(cache: Path) -> None:
    assert cli.main(["add", "https://example.com/houses"]) == cli.EXIT_ERROR


def test_add_can_skip_the_check(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("should not have been fetched")

    monkeypatch.setattr(cli, "fetch_search_page", refuse)
    assert cli.main(["add", FUNDA_URL, "--no-check"]) == cli.EXIT_OK


def test_adding_twice_does_not_duplicate(cache: Path, stub_page: dict[str, Any]) -> None:
    cli.main(["add", FUNDA_URL])
    cli.main(["add", FUNDA_URL])
    conn = db.connect(cache, read_only=True)
    try:
        assert len(db.list_searches(conn)) == 1
    finally:
        conn.close()


def test_rm_removes_by_the_id_status_shows(
    cache: Path, stub_page: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["add", FUNDA_URL])
    capsys.readouterr()
    assert cli.main(["rm", "1"]) == cli.EXIT_OK
    assert "removed" in capsys.readouterr().out
    conn = db.connect(cache, read_only=True)
    try:
        assert db.list_searches(conn) == []
    finally:
        conn.close()


def test_rm_on_an_unknown_id_is_an_error(cache: Path, stub_page: dict[str, Any]) -> None:
    cli.main(["add", FUNDA_URL])
    assert cli.main(["rm", "42"]) == cli.EXIT_ERROR


def test_status_lists_the_tracked_searches(
    cache: Path, stub_page: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["add", FUNDA_URL, "--name", "Den Haag 50m2+"])
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "[1]" in out
    assert "Den Haag 50m2+" in out
    assert FUNDA_URL in out


def test_fetch_without_any_tracked_search_explains_itself(
    cache: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["fetch"]) == cli.EXIT_ERROR
    assert "housemaster add" in capsys.readouterr().err


def test_fetch_walks_every_tracked_search(
    cache: Path, stub_page: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.main(["add", FUNDA_URL])
    cli.main(["add", FUNDA_URL + "&rooms=3"])

    seen: list[tuple[str, ...]] = []

    def fake_run(_conn: object, options: Any, **_kw: object) -> FetchReport:
        seen.append(options.search_urls)
        return FetchReport(search_url="x")

    monkeypatch.setattr(cli, "run_pipeline", fake_run)
    assert cli.main(["fetch"]) == cli.EXIT_OK
    assert seen == [(FUNDA_URL, FUNDA_URL + "&rooms=3")]


def test_fetch_url_overrides_the_tracked_set_without_saving_it(
    cache: Path, stub_page: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.main(["add", FUNDA_URL])
    seen: list[tuple[str, ...]] = []

    def fake_run(_conn: object, options: Any, **_kw: object) -> FetchReport:
        seen.append(options.search_urls)
        return FetchReport(search_url="x")

    monkeypatch.setattr(cli, "run_pipeline", fake_run)
    cli.main(["fetch", "--url", "https://example.test/adhoc"])
    assert seen == [("https://example.test/adhoc",)]

    conn = db.connect(cache, read_only=True)
    try:
        assert [r["url"] for r in db.list_searches(conn)] == [FUNDA_URL]
    finally:
        conn.close()


# --- the fetch lock --------------------------------------------------------


def _stub_pipeline(_conn: object, _options: Any, **_kw: object) -> FetchReport:
    return FetchReport(search_url="x")


def test_a_second_fetch_refuses_while_one_is_running(
    cache: Path, stub_page: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent fetches do not corrupt anything -- SQLite serialises the
    writes -- but they contend for the writer lock until one gives up mid-run,
    and they ask funda for the same pages twice."""
    cli.main(["add", FUNDA_URL])

    seen: list[int] = []

    def fake_run(_conn: object, _options: Any, **_kw: object) -> FetchReport:
        # Re-entering while the lock is held is what a manual run during the
        # scheduled one looks like.
        seen.append(cli.main(["fetch"]))
        return FetchReport(search_url="x")

    monkeypatch.setattr(cli, "run_pipeline", fake_run)
    assert cli.main(["fetch"]) == cli.EXIT_OK
    assert seen == [cli.EXIT_ERROR]


def test_the_lock_is_released_afterwards(
    cache: Path, stub_page: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.main(["add", FUNDA_URL])
    monkeypatch.setattr(cli, "run_pipeline", _stub_pipeline)
    assert cli.main(["fetch"]) == cli.EXIT_OK
    assert cli.main(["fetch"]) == cli.EXIT_OK
    assert list(cache.parent.glob("*.fetch-lock")) == []


def test_a_stale_lock_does_not_wedge_the_schedule(
    cache: Path, stub_page: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A killed run leaves the file behind; without expiry every nightly fetch
    # from then on would refuse.
    cli.main(["add", FUNDA_URL])
    lock = cache.parent / f"{cache.name}.fetch-lock"
    lock.write_text("stale", encoding="utf-8")
    old = time.time() - cli.STALE_LOCK_AFTER - 60
    os.utime(lock, (old, old))

    monkeypatch.setattr(cli, "run_pipeline", _stub_pipeline)
    assert cli.main(["fetch"]) == cli.EXIT_OK


# --- the daily schedule ----------------------------------------------------


def test_schedule_rejects_a_bad_time(cache: Path) -> None:
    assert cli.main(["schedule", "--at", "half six"]) == cli.EXIT_ERROR


def test_schedule_rejects_an_unknown_zone(cache: Path) -> None:
    assert cli.main(["schedule", "--tz", "Mars/Olympus"]) == cli.EXIT_ERROR


def test_a_failing_fetch_does_not_kill_the_schedule(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nightly job has to still be there tomorrow."""

    def boom(_args: object) -> int:
        raise RuntimeError("funda fell over")

    monkeypatch.setattr(cli, "run_fetch", boom)
    args = cli.build_parser().parse_args(["schedule"])
    assert cli._fetch_once(args) == cli.EXIT_ERROR
