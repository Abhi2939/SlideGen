from app.core.errors import DeckReviewError, SlideWriteError
from app.pipeline.models import ReviewOutput
from app.pipeline.editorial import CopyReview
import run_deck


def test_cli_reports_final_review_findings(monkeypatch, capsys):
    review = ReviewOutput(
        passed=False,
        issues=[
            {
                "category": "unsupported_claim",
                "severity": "medium",
                "detail": "The slide describes the deck's own counts.",
                "slide_indices": [1],
            }
        ],
        regenerate_slide_indices=[1],
    )
    monkeypatch.setattr(
        "sys.argv",
        ["run_deck.py", "Create a presentation"],
    )
    monkeypatch.setattr(
        run_deck,
        "generate_deck",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DeckReviewError(review, 2)),
    )

    result = run_deck.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "Generation failed: deck review did not pass after 2 repair round(s)" in captured.err
    assert "- medium unsupported_claim on slide(s) 2" in captured.err
    assert "The slide describes the deck's own counts." in captured.err


def test_cli_reports_slide_write_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["run_deck.py", "Create a presentation"])
    monkeypatch.setattr(
        run_deck,
        "generate_deck",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SlideWriteError(3, "Supplied Details", "invalid chart values")
        ),
    )

    result = run_deck.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "Generation failed: failed to write slide 4" in captured.err
    assert "invalid chart values" in captured.err


def test_cli_reports_editorial_review_findings_without_traceback(monkeypatch, capsys):
    review = CopyReview(
        passed=False,
        issues=[{"slide_index": 2, "fix": "Remove the unsupported feature."}],
        missing_fact_ids=[0],
    )
    monkeypatch.setattr("sys.argv", ["run_deck.py", "Create a presentation"])
    monkeypatch.setattr(
        run_deck,
        "generate_deck",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DeckReviewError(review, 2)),
    )

    assert run_deck.main() == 1
    captured = capsys.readouterr()
    assert "- Slide 3: Remove the unsupported feature." in captured.err
    assert "- Missing required fact IDs: 0" in captured.err
