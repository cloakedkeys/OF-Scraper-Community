import sys


class NoConsoleError(Exception):
    """A prompt was needed, but there's no interactive console to show it on."""


def is_interactive():
    """
    Whether someone can answer a prompt: stdin and stdout are both a terminal.

    Not when output is redirected, the run is scheduled, or another program
    started it. prompt_toolkit can't open a prompt then (on Windows it raised
    NoConsoleScreenBufferError).
    """
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is None or not stream.isatty():
                return False
        except (AttributeError, ValueError):  # no isatty, or a closed stream
            return False
    return True


def require_console(question):
    """Raise NoConsoleError, naming the question, when no one can answer it."""
    if not is_interactive():
        raise NoConsoleError(
            f"The run needs an answer to {question!r}, but there's no interactive "
            "console to ask it on. Run it in a terminal, or pass the options that "
            "answer it on the command line."
        )
