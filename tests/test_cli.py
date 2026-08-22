from __future__ import annotations

import json
from typing import Any

import pytest

from housemaster import cli
from housemaster.funda import BlockedError
from housemaster.models import SearchPage

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
