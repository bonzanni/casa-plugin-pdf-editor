import os
import time

import pytest

from documents import RETENTION_S, Refused
from fixtures import (cropped_form, cropped_rotated_form, encrypted, flat_form, in_handoff,
                      offset_form, offset_rotated_form, rotated_form, signed_flag_form)


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
    (lambda t: str(t / "elsewhere.pdf"), "handoff"),
])
def test_refusals_store_nothing(store, tmp_path, make, word):
    with pytest.raises(Refused) as e: store.open(make(tmp_path))
    assert word in e.value.reason and not os.listdir(store.root + "/documents")

def test_unrotated_offset_and_cropped_pages_open(store, tmp_path):
    for make in (cropped_form, offset_form, rotated_form):
        assert store.open(in_handoff(tmp_path, make())).pages == 1

@pytest.mark.parametrize("flags, expected", [(1, True), (3, True), (0, False)])
def test_existing_digital_signature_is_noticed(store, tmp_path, flags, expected):
    assert store.open(in_handoff(tmp_path, signed_flag_form(flags))).has_digital_signature is expected
    assert store.open(in_handoff(tmp_path, flat_form())).has_digital_signature is False     # no form at all: get_sigflags() is -1

def test_purge_removes_only_old(store, tmp_path):
    doc = store.open(in_handoff(tmp_path, flat_form()))
    assert store.purge_old(now=time.time() + RETENTION_S - 60) == 0
    assert store.purge_old(now=time.time() + RETENTION_S + 60) == 1
