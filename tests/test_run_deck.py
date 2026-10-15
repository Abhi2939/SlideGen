from app.core.errors import DeckReviewError, SlideWriteError
from app.pipeline.models import ReviewOutput
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
