"""menu-ingest CLI tests: subcommand dispatch over the pipeline,
the reports and the metrics snapshot."""

from typing import TYPE_CHECKING
from unittest.mock import ANY, MagicMock, patch

import pytest

from menu.ingest.errors import BlockedError
from menu.ingest.sites.bbc_good_food import BBC_GOOD_FOOD
from menu.scripts import ingest as ingest_cli
from menu.scripts.ingest import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the shared preamble (initialise, seeding, sessions) so the
    dispatch tests never touch a database."""
    monkeypatch.setattr(ingest_cli, "initialise", MagicMock())
    monkeypatch.setattr(ingest_cli, "get_session", MagicMock())
    monkeypatch.setattr(ingest_cli, "seed_ingredient_data", MagicMock())


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.crawl_sitemap")
@patch("menu.scripts.ingest.import_sitemap")
def test_main_crawl_imports_and_crawls(
    mock_import: MagicMock, mock_crawl: MagicMock
) -> None:
    main(["crawl", "bbc_good_food"])

    mock_import.assert_called_once_with(BBC_GOOD_FOOD)
    mock_crawl.assert_called_once_with(BBC_GOOD_FOOD)


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.crawl_sitemap")
@patch("menu.scripts.ingest.import_sitemap")
def test_main_crawl_blocked_exits_cleanly(
    mock_import: MagicMock, mock_crawl: MagicMock
) -> None:
    mock_import.side_effect = BlockedError("Blocked with HTTP 402", status_code=402)

    with pytest.raises(SystemExit) as excinfo:
        main(["crawl", "bbc_good_food"])

    assert "bbc_good_food: blocked: Blocked with HTTP 402" in str(excinfo.value)
    mock_crawl.assert_not_called()


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.reparse_site")
def test_main_reparse_dispatches(mock_reparse: MagicMock) -> None:
    main(["reparse", "bbc_good_food"])

    mock_reparse.assert_called_once_with(BBC_GOOD_FOOD)


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.re_resolve", return_value=(1, 2, 3))
def test_main_re_resolve_prints_the_counts(
    mock_re_resolve: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["re-resolve"])

    mock_re_resolve.assert_called_once_with(ANY)
    assert (
        "Re-resolved: 1 terms changed, 2 lines regrammed, 3 concepts pruned"
        in capsys.readouterr().out
    )


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.suggest_command")
def test_main_suggest_dispatches_the_sets(mock_suggest: MagicMock) -> None:
    main(["suggest", "--sets", "queue,audit", "--limit", "5", "--dry-run"])

    mock_suggest.assert_called_once_with(["queue", "audit"], limit=5, dry_run=True)


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.suggest_command")
def test_main_suggest_defaults_to_all_four_sets(mock_suggest: MagicMock) -> None:
    main(["suggest"])

    mock_suggest.assert_called_once_with(
        ["queue", "audit", "pair", "food_ref"], limit=None, dry_run=False
    )


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.suggest_command")
def test_main_suggest_rejects_an_unknown_set(
    mock_suggest: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["suggest", "--sets", "queue,bogus"])

    assert excinfo.value.code == 2
    assert "unknown input set(s): bogus" in capsys.readouterr().err
    mock_suggest.assert_not_called()


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.suggest_command")
def test_main_suggest_rejects_a_zero_limit(
    mock_suggest: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["suggest", "--limit", "0"])

    assert excinfo.value.code == 2
    assert "must be at least 1" in capsys.readouterr().err
    mock_suggest.assert_not_called()


@pytest.mark.usefixtures("no_db")
def test_main_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.derive_all_nutrition", return_value=7)
def test_main_derive_nutrition_prints_the_count(
    mock_derive: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["derive-nutrition"])

    mock_derive.assert_called_once_with(ANY)
    assert "Derived nutrition for 7 recipes" in capsys.readouterr().out


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.fdc_id_conflicts")
def test_main_report_fdc_conflicts_prints_each_food(
    mock_conflicts: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_conflicts.return_value = [("Butter", ["butter", "sweet butter"])]

    main(["report", "fdc-conflicts"])

    assert "Butter: butter, sweet butter" in capsys.readouterr().out


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.suggest_embedder")
@patch("menu.scripts.ingest.ref_review")
def test_main_report_ref_review_prints_the_rows(
    mock_review: MagicMock,
    mock_embedder: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_review.return_value = [
        (5, "milk", "whole milk", ["mlk"], ["milk ref", "cream"]),
        (3, "eggs", None, [], []),
    ]

    main(["report", "ref-review"])

    mock_embedder.assert_called_once_with()
    out = capsys.readouterr().out
    assert "    5  milk: parser candidate: whole milk | mlk" in out
    assert "    milk ref | cream" in out
    assert "    3  eggs: no parser candidate" in out


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.validation_report")
def test_main_report_validate_nutrition_dispatches(mock_report: MagicMock) -> None:
    main(["report", "validate-nutrition"])

    mock_report.assert_called_once_with(ANY)


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.coverage_report")
def test_main_report_coverage_dispatches(mock_report: MagicMock) -> None:
    main(["report", "coverage"])

    mock_report.assert_called_once_with(ANY)


@pytest.mark.usefixtures("no_db")
@patch("menu.scripts.ingest.collect_metrics", return_value={"recipes": 3})
def test_main_metrics_writes_the_snapshot(
    mock_metrics: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "snapshots" / "metrics.json"

    main(["metrics", str(path)])

    mock_metrics.assert_called_once_with(ANY)
    assert path.read_text() == '{\n  "recipes": 3\n}\n'
    assert f"Metrics snapshot written to {path}" in capsys.readouterr().out
