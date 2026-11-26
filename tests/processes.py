"""A second server process for the tests.

Every server process of one install works in the same data folder, so the tests of that
run real tool calls in a child process. Children are started with ``spawn``: they share
nothing with the test but the environment (which names the data, outbox and handoff
folders) and one pipe. A child never outlives its test.
"""
from __future__ import annotations

import contextlib
import json
import multiprocessing

WAIT = 30.0     # the longest a test waits for a child's word; never slept in full
TICK = {"kind": "tick", "page": 1, "x": 100, "y": 100}

_SPAWN = multiprocessing.get_context("spawn")


class Child:
    def __init__(self, target, args) -> None:
        self._conn, theirs = _SPAWN.Pipe()
        self._proc = _SPAWN.Process(target=target, args=(theirs, *args), daemon=True)
        self._proc.start()
        theirs.close()

    def tell(self, word: str = "go") -> None:
        self._conn.send(word)

    def told(self):
        """The child's next word; the test fails when none comes in time."""
        assert self._conn.poll(WAIT), "the child process said nothing in time"
        return self._conn.recv()

    def silent_for(self, seconds: float) -> bool:
        """Whether the child says nothing for ``seconds``; a word it does say stays unread."""
        return not self._conn.poll(seconds)

    def kill(self) -> None:
        if self._proc.is_alive():
            self._proc.kill()
        self._proc.join(WAIT)
        assert not self._proc.is_alive()


@contextlib.contextmanager
def child(target, *args):
    """Run ``target(conn, *args)`` in a child process for the length of the block."""
    proc = Child(target, args)
    try:
        yield proc
    finally:
        proc.kill()
        proc._conn.close()


# -- what a child runs ------------------------------------------------------

def _outcome(tool, **args):
    """The tool's JSON answer (the last block when it also returns images), or the error it
    raised as ``"<type>: <message>"``."""
    try:
        result = tool(**args)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return json.loads(result if isinstance(result, str) else result[-1])


def _start(conn):
    """Load the server, say so, and wait for the test's word."""
    import pdf_server
    conn.send("ready")
    conn.recv()
    return pdf_server


def call_once(conn, name: str, args: dict) -> None:
    """Say ``attempting``, call one tool, say its outcome."""
    tool = getattr(_start(conn), name)
    conn.send("attempting")
    conn.send(_outcome(tool, **args))


def write_each(conn, doc_id: str, marks: list[dict]) -> None:
    """Write each mark as its own version; say the list of outcomes."""
    server = _start(conn)
    conn.send([_outcome(server.write_on_pdf, doc_id=doc_id, marks=[mark]) for mark in marks])


def write_until_told(conn, doc_id: str, pause_inside_first: bool = False) -> None:
    """Write version after version, saying ``writing`` after the first, until the test says
    anything; the write after that word is the last. Then say the list of outcomes.

    With ``pause_inside_first`` the first write stops where it has read its base and not yet
    stored its version, says ``inside``, and goes on at the test's word."""
    server = _start(conn)
    if pause_inside_first:
        import marks
        apply = marks.apply

        def inside(*args):
            marks.apply = apply
            conn.send("inside")
            conn.recv()
            return apply(*args)

        marks.apply = inside
    outcomes: list = []
    last = False
    while not last:
        last = conn.poll()
        outcomes.append(_outcome(server.write_on_pdf, doc_id=doc_id, marks=[TICK]))
        if len(outcomes) == 1:
            conn.send("writing")
    conn.send(outcomes)


def hold_the_lock(conn, root: str) -> None:
    """Take the store's lock, say ``held``, and keep it until killed."""
    from documents import Store
    with Store(root).locked():
        conn.send("held")
        conn.recv()
