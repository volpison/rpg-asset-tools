"""Tiny helper: long operations report progress through the `log` callable they already get."""


def report(log, done: int, total: int, text: str = "") -> None:
    """log may carry a .progress(done, total, text) method (the GUI's worker does); plain print does not."""
    fn = getattr(log, "progress", None)
    if fn is not None:
        fn(done, total, text)
