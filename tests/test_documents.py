import fcntl
import os
import stat
import threading
import time

import pytest

from documents import RETENTION_S, Refused, Store
from fixtures import (cropped_form, cropped_rotated_form, encrypted, flat_form, in_handoff,
                      offset_form, offset_rotated_form, rotated_form, signed_flag_form, userunit_form)
from processes import WAIT, child, hold_the_lock


def test_open_copies_and_never_touches_the_source(store, tmp_path):
    path = in_handoff(tmp_path, flat_form()); before = open(path, "rb").read()
    doc = store.open(path)
    assert (doc.version, doc.signed, doc.pages, doc.filename) == (0, False, 1, "form.pdf")
    assert open(path, "rb").read() == before and store.latest_bytes(doc.id) == before

def test_versions_and_undo(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    assert store.add_version(doc.id, b"%PDF-1") == 1 and store.add_version(doc.id, b"%PDF-2") == 2
    assert store.undo(doc.id) == 1 and store.latest_bytes(doc.id) == b"%PDF-1"
    assert store.add_version(doc.id, b"%PDF-3") == 3          # a number is never reused for different content
    assert store.undo(doc.id) == 1 and store.undo(doc.id) == 0
    with pytest.raises(Refused): store.undo(doc.id)
    assert store.add_version(doc.id, b"%PDF-4") == 4 and store.get(doc.id).version == 4

def test_signed_version_and_latest_unsigned(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    store.add_version(doc.id, b"%PDF-draft"); store.add_version(doc.id, b"%PDF-signed", signed=True)
    assert store.get(doc.id).signed is True
    assert store.latest_unsigned_bytes(doc.id) == b"%PDF-draft"

@pytest.mark.parametrize("make, word", [
    (lambda t: in_handoff(t, encrypted()), "password"),
    (lambda t: in_handoff(t, b"\xff\xd8\xff not a pdf"), "not a PDF"),
    (lambda t: in_handoff(t, cropped_rotated_form()), "rotated"),
    (lambda t: in_handoff(t, offset_rotated_form()), "rotated"),
    (lambda t: in_handoff(t, userunit_form(90)), "rotated"),
    (lambda t: in_handoff(t, userunit_form(180)), "rotated"),
    (lambda t: in_handoff(t, userunit_form(270)), "rotated"),
    (lambda t: str(t / "elsewhere.pdf"), "handoff"),
])
def test_refusals_store_nothing(store, tmp_path, make, word):
    with pytest.raises(Refused) as e: store.open(make(tmp_path))
    assert word in e.value.reason and not os.listdir(store.root + "/documents")

def test_unrotated_offset_and_cropped_pages_open(store, tmp_path):
    for make in (cropped_form, offset_form, rotated_form, userunit_form):
        assert store.open(in_handoff(tmp_path, make())).pages == 1

@pytest.mark.parametrize("flags, expected", [(1, True), (3, True), (0, False)])
def test_existing_digital_signature_is_noticed(store, tmp_path, flags, expected):
    assert store.open(in_handoff(tmp_path, signed_flag_form(flags))).has_digital_signature is expected
    assert store.open(in_handoff(tmp_path, flat_form())).has_digital_signature is False     # no form at all: get_sigflags() is -1

def test_purge_removes_only_old(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    assert store.purge_old(now=time.time() + RETENTION_S - 60) == 0
    assert store.purge_old(now=time.time() + RETENTION_S + 60) == 1

def test_erase_documents_removes_everything_and_reports_what_is_left(store, tmp_path, monkeypatch):
    import shutil
    keep, gone = store.open(in_handoff(tmp_path, flat_form())), store.open(in_handoff(tmp_path, flat_form()))
    docs = tmp_path / "data" / "documents"
    (docs / ".staging-abc").mkdir(); (docs / "stray.tmp").write_bytes(b"x")
    real = shutil.rmtree

    def stuck(path, *a, **k):
        if keep.id in str(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(shutil, "rmtree", stuck)
    removed, left = store.erase_documents()
    assert removed == 1 and left == [keep.id] and sorted(os.listdir(docs)) == [keep.id]
    monkeypatch.setattr(shutil, "rmtree", real)
    assert store.erase_documents() == (1, []) and os.listdir(docs) == []


def test_a_failed_metadata_save_leaves_no_version_file(store, tmp_path, monkeypatch):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    folder = os.path.join(str(tmp_path / "data"), "documents", doc.id)
    before = sorted(os.listdir(folder))
    real = type(store)._save_meta

    def broken(path, meta):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(type(store), "_save_meta", staticmethod(broken))
    with pytest.raises(OSError):
        store.add_version(doc.id, b"%PDF-signed", signed=True)
    assert sorted(os.listdir(folder)) == before
    monkeypatch.setattr(type(store), "_save_meta", staticmethod(real))
    assert store.add_version(doc.id, b"%PDF-next") == 1 and store.latest_bytes(doc.id) == b"%PDF-next"


@pytest.mark.parametrize("call", ["replace", "fsync"])
def test_a_failed_version_write_leaves_the_folder_as_it_was(store, tmp_path, monkeypatch, call):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    store.add_version(doc.id, b"%PDF-1")
    folder = os.path.join(str(tmp_path / "data"), "documents", doc.id)
    before = {n: open(os.path.join(folder, n), "rb").read() for n in os.listdir(folder)}

    def broken(*a, **k):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, call, broken)
    with pytest.raises(OSError):
        store.add_version(doc.id, b"%PDF-signed", signed=True)
    with pytest.raises(OSError):
        store.undo(doc.id)
    monkeypatch.undo()
    assert {n: open(os.path.join(folder, n), "rb").read() for n in os.listdir(folder)} == before


def test_files_a_crash_left_outside_the_history_are_removed_on_load(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    folder = os.path.join(str(tmp_path / "data"), "documents", doc.id)
    for name in ("v1.pdf", "v2.pdf.tmp", "meta.json.tmp"):    # a crash between a write and its record
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(b"%PDF-signed")
    assert store.get(doc.id).version == 0
    assert sorted(os.listdir(folder)) == ["meta.json", "v0.pdf"]
    assert store.add_version(doc.id, b"%PDF-next") == 1 and store.latest_bytes(doc.id) == b"%PDF-next"
    store.add_version(doc.id, b"%PDF-2"); store.undo(doc.id)
    with open(os.path.join(folder, "v2.pdf"), "wb") as fh:  # an undo that crashed before removing its file
        fh.write(b"%PDF-2")
    assert store.get(doc.id).version == 1 and sorted(os.listdir(folder)) == ["meta.json", "v0.pdf", "v1.pdf"]
    assert store.add_version(doc.id, b"%PDF-3") == 3          # highest-ever + 1: 2 is never reused


def test_an_entry_that_cannot_be_removed_on_load_is_an_error(store, tmp_path, monkeypatch):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    folder = os.path.join(str(tmp_path / "data"), "documents", doc.id)
    open(os.path.join(folder, "v1.pdf"), "wb").close()

    def stuck(path, *a, **k):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "remove", stuck)
    with pytest.raises(PermissionError):
        store.get(doc.id)


def _descriptors_on(path: str) -> int:
    """How many of this process's open descriptors are on ``path``."""
    count = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            count += os.readlink(f"/proc/self/fd/{fd}") == path
        except OSError:      # the descriptor that listed the folder is gone by now
            pass
    return count


def test_the_lock_is_one_private_file_beside_the_documents_folder(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    lock = os.path.realpath(os.path.join(store.root, "documents.lock"))
    assert sorted(os.listdir(store.root)) == ["documents", "documents.lock"]
    assert stat.S_IMODE(os.stat(lock).st_mode) == 0o600
    with store.locked():
        with store.locked():                             # the holder's own re-entry does not block
            assert store.add_version(doc.id, b"%PDF-1") == 1
        assert _descriptors_on(lock) == 1                # and leaving it does not let go
    assert _descriptors_on(lock) == 0
    assert store.purge_old(now=time.time() + RETENTION_S + 60) == 1
    assert store.erase_documents() == (0, []) and os.path.isfile(lock)


def test_a_second_thread_of_one_process_waits_for_the_lock(store):
    order = []

    def second():
        with Store(store.root).locked():
            order.append("second")

    thread = threading.Thread(target=second, daemon=True)
    with store.locked():
        thread.start()
        thread.join(0.3)
        assert thread.is_alive() and order == []
        order.append("first")
    thread.join(WAIT)
    assert order == ["first", "second"]


def test_every_use_of_the_store_waits_for_the_lock(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    another = in_handoff(tmp_path, flat_form(), "another.pdf")
    uses = {
        "open": lambda: store.open(another),
        "get": lambda: store.get(doc.id),
        "add_version": lambda: store.add_version(doc.id, b"%PDF-1"),
        "undo": lambda: store.undo(doc.id),
        "latest_bytes": lambda: store.latest_bytes(doc.id),
        "latest_unsigned_bytes": lambda: store.latest_unsigned_bytes(doc.id),
        "purge_old": store.purge_old,
        "erase_documents": store.erase_documents,
    }
    returned = []

    def use(name):
        try:
            uses[name]()
        except Refused:      # the order is free once the lock is: the erase or the undo may come first
            pass
        returned.append(name)

    threads = [threading.Thread(target=use, args=(name,), daemon=True) for name in uses]
    with child(hold_the_lock, store.root) as holder:
        assert holder.told() == "held"
        for thread in threads:
            thread.start()
        threads[-1].join(0.3)
        assert returned == []
        holder.kill()
        for thread in threads:
            thread.join(WAIT)
    assert sorted(returned) == sorted(uses)


def test_a_dead_holder_frees_the_lock(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    got = []
    with child(hold_the_lock, store.root) as holder:
        assert holder.told() == "held"
        probe = os.open(os.path.join(store.root, "documents.lock"), os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):         # the child does hold it
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)
        holder.kill()
        writer = threading.Thread(target=lambda: got.append(store.add_version(doc.id, b"%PDF-1")), daemon=True)
        writer.start()
        writer.join(WAIT)
    assert got == [1]
