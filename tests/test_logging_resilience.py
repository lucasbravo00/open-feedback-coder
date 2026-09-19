"""Tests that a broken stderr cannot throw away a run that was paid for.

Piping the tool's own progress output into something that exits early - `head`,
a closed terminal - used to raise BrokenPipeError out of the progress callback
and out of main(), which writes its output files only after labelling returns.
Every model call had been made and billed; nothing was written.
"""

import sys

import pytest

from open_feedback_coder.cli import _log


class BrokenStream:
    """A stderr that has gone away, as a closed pipe behaves."""

    def __init__(self, error):
        self.error = error

    def write(self, _text):
        raise self.error

    def flush(self):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [BrokenPipeError(32, "Broken pipe"), OSError("stream is gone"), ValueError("closed file")],
)
def test_logging_to_a_broken_stream_is_survivable(monkeypatch, error):
    monkeypatch.setattr(sys, "stderr", BrokenStream(error))

    _log("this message goes nowhere")
    _log()
    _log("partial line", end="\r")


def test_logging_still_writes_when_the_stream_works(monkeypatch, capsys):
    _log("a message")
    _log("same line", end="\r")

    captured = capsys.readouterr()
    assert "a message\n" in captured.err
    assert "same line\r" in captured.err
