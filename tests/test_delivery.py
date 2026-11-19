import os

import pytest

import casa_broker
import casa_handoff
import delivery
from delivery import CHAT_MAX_BYTES
from fixtures import files_under, flat_form


def test_send_pdf_publishes_stages_then_deposits(casa_dirs, monkeypatch):
    seen = {}
    def deposit(slot, path, **kw):
        seen.update(slot=slot, path=path, published=os.path.isdir(casa_dirs.handoff + "/pdf-editor"), **kw)
        return "casa-cap-" + "0" * 32
    monkeypatch.setattr(delivery, "_deposit_file", deposit)
    src = flat_form(); out = delivery.send_pdf(src, "form.pdf", signed=False)
    assert seen["kind"] == "document" and seen["filename"] == "form (draft).pdf" and "dedup" not in "".join(seen)
    assert seen["published"] is True                                    # handoff file existed before the deposit
    assert open(seen["path"], "rb").read() == src
    name, data = casa_handoff.capture(out["handoff_path"])
    assert (name, data) == ("form (draft).pdf", src) and out["sent_as"] == name

def test_signed_and_draft_names_differ():
    assert delivery.chat_name("form.pdf", signed=True) == "form (signed).pdf" != delivery.chat_name("form.pdf", signed=False)

def test_failed_deposit_publishes_nothing_and_cleans_up(casa_dirs, monkeypatch):
    def boom(*a, **k): raise casa_broker.DepositFailed("unavailable")
    monkeypatch.setattr(delivery, "_deposit_file", boom)
    with pytest.raises(delivery.DeliveryFailed) as e: delivery.send_pdf(flat_form(), "form.pdf", signed=True)
    assert "nothing was sent" in e.value.reason
    assert os.listdir(casa_dirs.outbox) == [] and files_under(casa_dirs.handoff) == []
    assert os.listdir(os.path.join(casa_dirs.handoff, "pdf-editor")) == []    # no directory left behind

def test_failed_image_deposit_leaves_nothing_staged(casa_dirs, monkeypatch):
    def boom(*a, **k): raise casa_broker.DepositFailed("unavailable")
    monkeypatch.setattr(delivery, "_deposit_file", boom)
    with pytest.raises(delivery.DeliveryFailed): delivery.send_image(b"\x89PNG\r\n\x1a\n", "signature preview.png", "")
    assert os.listdir(casa_dirs.outbox) == [] and files_under(casa_dirs.handoff) == []

def test_missing_outbox_is_a_plain_refusal(casa_dirs, monkeypatch):
    monkeypatch.setenv("CASA_PLUGIN_OUTBOX_DIR", "/nonexistent/outbox")
    with pytest.raises(delivery.DeliveryFailed) as e: delivery.send_pdf(flat_form(), "form.pdf", signed=False)
    assert "outbox" in e.value.reason and files_under(casa_dirs.handoff) == []

def test_over_20_mb_is_refused_before_anything_is_written(casa_dirs):
    with pytest.raises(delivery.DeliveryFailed): delivery.send_pdf(b"%PDF-" + b"0" * CHAT_MAX_BYTES, "big.pdf", signed=False)
    assert os.listdir(casa_dirs.outbox) == []

@pytest.mark.parametrize("stem", ["x" * 200, "\u00e9" * 95 + ".pdf"])
def test_label_survives_a_long_file_name(stem):
    signed, draft = delivery.chat_name(stem, signed=True), delivery.chat_name(stem, signed=False)
    assert signed.endswith(" (signed).pdf") and draft.endswith(" (draft).pdf") and signed != draft
    for name in (signed, draft):
        assert casa_handoff.clean_filename(name, "fallback.pdf") == name
