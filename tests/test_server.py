import asyncio
import json
import os
import shutil
import threading

import pymupdf
import pytest

import casa_handoff
import delivery
import marks as marks_mod
import signature
from documents import Store
from fixtures import flat_form, in_handoff, photo, signed_flag_form
from processes import WAIT, call_once, child, write_each, write_until_told

REFERENCE = "casa-cap-" + "0" * 32


@pytest.fixture
def server(tmp_path, casa_dirs, monkeypatch):
    import pdf_server
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(delivery, "_deposit_file", lambda *a, **k: REFERENCE)
    return pdf_server


def call(name, **args):
    import pdf_server
    return asyncio.run(pdf_server.mcp.call_tool(name, args))


def js(result):
    return json.loads([c for c in result if c.type == "text"][-1].text)


def text(s):
    return [{"kind": "text", "page": 1, "x": 205, "y": 600, "text": s}]


def page_text(handoff_path):
    _, data = casa_handoff.capture(handoff_path)
    return pymupdf.open(stream=data, filetype="pdf")[0].get_text()


def test_the_whole_flow(server, tmp_path):
    opened = call("open_pdf", path=in_handoff(tmp_path, flat_form()))
    assert [c.type for c in opened] == ["image", "text"]; doc = js(opened)["doc_id"]
    tried = call("write_on_pdf", doc_id=doc, preview_only=True, marks=[{"kind": "placeholder", "page": 1, "x": 72, "y": 720, "width": 150, "height": 50}])
    assert js(tried)["version"] == 0
    wrote = call("write_on_pdf", doc_id=doc, marks=[{"kind": "text", "page": 1, "x": 205, "y": 600, "text": "A. Example"}])
    assert js(wrote)["version"] == 1 and wrote[0].type == "image"
    draft = js(call("send_pdf_to_chat", doc_id=doc)); assert draft["sent_as"] == "form (draft).pdf"
    signed = call("sign_pdf", doc_id=doc, filename="form.pdf", version=1, look="stamp", page=1, x=72, y=720, width=180, signer_name="A. Example")
    assert js(signed)["signed"] is True
    final = js(call("send_pdf_to_chat", doc_id=doc)); assert final["sent_as"] == "form (signed).pdf"
    _, data = casa_handoff.capture(final["handoff_path"])
    assert "SIGNED ELECTRONICALLY" in pymupdf.open(stream=data, filetype="pdf")[0].get_text()
    _, draft_data = casa_handoff.capture(draft["handoff_path"])
    assert "SIGNED ELECTRONICALLY" not in pymupdf.open(stream=draft_data, filetype="pdf")[0].get_text()


def test_open_reports_page_one_facts_and_warns_on_a_digital_signature(server, tmp_path):
    opened = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))
    assert opened["version"] == 0 and opened["signed"] is False and opened["pages"] == 1
    assert opened["facts"]["page"] == 1 and "warning" not in opened
    assert any("Signature" in t["text"] for t in opened["facts"]["text"])
    assert "warning" in js(call("open_pdf", path=in_handoff(tmp_path, signed_flag_form(3), "s.pdf")))


def test_look_at_page_and_region(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    looked = call("look_at_page", doc_id=doc, page=1, region=[50, 550, 300, 650])
    assert [c.type for c in looked] == ["image", "text"] and js(looked)["page"] == 1
    with pytest.raises(Exception, match="does not exist"): call("look_at_page", doc_id=doc, page=2)
    with pytest.raises(Exception, match="region"): call("look_at_page", doc_id=doc, region=[1, 2])


def test_placeholder_cannot_become_a_version(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    box = {"kind": "placeholder", "page": 1, "x": 72, "y": 720, "width": 150, "height": 50}
    with pytest.raises(Exception, match="preview_only"): call("write_on_pdf", doc_id=doc, marks=[box])
    looked = js(call("look_at_page", doc_id=doc))
    assert looked["version"] == 0 and looked["boxes"] == []


def test_preview_only_stores_nothing_and_nothing_applied_adds_no_version(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    tried = call("write_on_pdf", doc_id=doc, marks=text("A. Example"), preview_only=True)
    assert js(tried)["version"] == 0 and tried[0].type == "image"
    assert all(t["text"] != "A. Example" for t in js(call("look_at_page", doc_id=doc))["text"])
    failed = call("write_on_pdf", doc_id=doc, marks=[{"kind": "text", "page": 9, "x": 1, "y": 1, "text": "x"}])
    assert [c.type for c in failed] == ["text"] and js(failed)["version"] == 0
    assert js(failed)["report"][0]["ok"] is False


def test_sign_refusals(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("A. Example"))
    sign = dict(doc_id=doc, filename="form.pdf", version=1, look="stamp", page=1, x=72, y=720, width=180, signer_name="A. Example")
    with pytest.raises(Exception, match="other.pdf"): call("sign_pdf", **{**sign, "filename": "other.pdf"})
    with pytest.raises(Exception, match="no signature is stored; use look 'stamp' or register one"):
        call("sign_pdf", **{**sign, "look": "handwritten"})
    with pytest.raises(Exception, match="signer_name"): call("sign_pdf", **{**sign, "signer_name": ""})
    with pytest.raises(Exception, match="look"): call("sign_pdf", **{**sign, "look": "fancy"})
    assert js(call("look_at_page", doc_id=doc))["version"] == 1        # nothing was signed
    signed = js(call("sign_pdf", **sign))
    with pytest.raises(Exception, match="already signed; write on it again to start a new unsigned version"):
        call("sign_pdf", **{**sign, "version": signed["version"]})


def test_an_approval_for_one_version_cannot_sign_another(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))                   # version 1
    sign = dict(doc_id=doc, filename="form.pdf", version=1, look="stamp", page=1, x=72, y=720, width=180, signer_name="A. Example")
    call("write_on_pdf", doc_id=doc, marks=text("two"))                   # version 2, while the card for version 1 is open
    with pytest.raises(Exception, match="changed since version 1"): call("sign_pdf", **sign)
    # undo back to "one", undo again, write something else: the number 1 is never given to different content
    call("undo_last_write", doc_id=doc); call("undo_last_write", doc_id=doc)
    assert js(call("write_on_pdf", doc_id=doc, marks=text("three")))["version"] == 3
    with pytest.raises(Exception, match="changed since version 1"): call("sign_pdf", **sign)
    assert js(call("sign_pdf", **{**sign, "version": 3}))["signed"] is True


def test_undo_returns_the_current_version_and_page_one(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    undone = call("undo_last_write", doc_id=doc)
    assert [c.type for c in undone] == ["image", "text"] and js(undone)["version"] == 0
    with pytest.raises(Exception, match="nothing to undo"): call("undo_last_write", doc_id=doc)


def test_writing_after_signing_starts_from_unsigned(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    call("sign_pdf", doc_id=doc, filename="form.pdf", version=1, look="stamp", page=1, x=72, y=720, width=180, signer_name="A. Example")
    again = js(call("write_on_pdf", doc_id=doc, marks=[{"kind": "text", "page": 1, "x": 72, "y": 500, "text": "more"}]))
    assert again["signature_removed"] is True and again["version"] == 3
    call("sign_pdf", doc_id=doc, filename="form.pdf", version=3, look="stamp", page=1, x=72, y=720, width=180, signer_name="A. Example")
    tried = js(call("write_on_pdf", doc_id=doc, preview_only=True, marks=text("try")))
    assert tried["signature_removed"] is True and tried["version"] == 4
    call("undo_last_write", doc_id=doc)
    sent_blocks = call("send_pdf_to_chat", doc_id=doc)
    assert [c.type for c in sent_blocks] == ["text"]                     # capability result: JSON only
    sent = js(sent_blocks)
    assert sent["file"] == REFERENCE and sent["sent_as"] == "form (draft).pdf"
    content = page_text(sent["handoff_path"])
    assert "SIGNED ELECTRONICALLY" not in content and "more" in content and "one" in content


def test_signature_registration(server, tmp_path):
    shot = in_handoff(tmp_path, photo(800, 600), "signature.jpg")
    previewed = call("preview_signature", path=shot)
    assert [c.type for c in previewed] == ["text"]                        # capability result: JSON only
    out = js(previewed)
    assert out["file"] == REFERENCE and len(out["preview_id"]) == 16
    assert js(call("signature_status")) == {"stored": False, "pending": True, "aspect": None}
    turned = js(call("preview_signature", path=shot, rotate=90))["preview_id"]
    assert turned != out["preview_id"]
    with pytest.raises(Exception, match="that preview is no longer the pending one; preview again"):
        call("store_signature", preview_id=out["preview_id"])
    stored = js(call("store_signature", preview_id=turned))
    assert stored["stored"] is True and stored["pending"] is False and stored["aspect"] > 1   # turned: taller than wide
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    signed = call("sign_pdf", doc_id=doc, filename="form.pdf", version=0, look="handwritten", page=1, x=72, y=720, width=60)
    assert signed[0].type == "image" and js(signed)["signed"] is True
    _, data = casa_handoff.capture(js(call("send_pdf_to_chat", doc_id=doc))["handoff_path"])
    assert len(pymupdf.open(stream=data, filetype="pdf")[0].get_images()) == 1
    assert js(call("remove_signature")) == {"stored": False, "pending": False, "aspect": None}


def _erase_result(result):
    """The eraser's JSON, checked for exactly what Casa's parse_erase_result reads."""
    out = js(result)
    assert out["erasure"] in ("complete", "incomplete")
    assert isinstance(out["report"], str) and out["report"].strip()
    return out


def test_erase_documents_keeps_signature_and_erase_all_removes_it(server, tmp_path):
    shot = in_handoff(tmp_path, photo(800, 600), "signature.jpg")
    call("store_signature", preview_id=js(call("preview_signature", path=shot))["preview_id"])
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))
    erased = _erase_result(call("erase_pdf_documents"))
    assert erased["erasure"] == "complete" and erased["documents_removed"] == 2
    assert "signature is kept" in erased["report"] and "handoff" in erased["report"]
    assert os.listdir(tmp_path / "data" / "documents") == []
    with pytest.raises(Exception, match="no open document"): call("look_at_page", doc_id=doc)
    assert js(call("signature_status"))["stored"] is True
    call("open_pdf", path=in_handoff(tmp_path, flat_form()))
    leftover = tmp_path / "data" / "signature" / ".write-x.tmp"          # a crash mid-write
    leftover.write_bytes(b"signature bytes")
    erased = _erase_result(call("erase_pdf_editor"))
    assert erased["erasure"] == "complete" and erased["documents_removed"] == 1
    assert erased["signature_removed"] is True and "handoff" in erased["report"]
    assert not leftover.exists() and os.listdir(tmp_path / "data" / "signature") == []
    assert os.listdir(tmp_path / "data" / "documents") == []
    assert js(call("signature_status")) == {"stored": False, "pending": False, "aspect": None}


def test_erasing_nothing_is_complete(server):
    for name in ("erase_pdf_documents", "erase_pdf_editor"):
        assert _erase_result(call(name))["erasure"] == "complete"


def test_an_entry_that_cannot_be_removed_makes_the_erase_incomplete(server, tmp_path, monkeypatch):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    other = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    real = shutil.rmtree

    def stuck(path, *a, **k):
        if doc in str(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(shutil, "rmtree", stuck)
    for name in ("erase_pdf_documents", "erase_pdf_editor"):
        erased = _erase_result(call(name))
        assert erased["erasure"] == "incomplete" and doc in erased["report"], name
        assert erased["documents_removed"] == (1 if name == "erase_pdf_documents" else 0)
        assert "There were no working documents" not in erased["report"]
        assert ("Deleted 1 working document and all its versions." if name == "erase_pdf_documents"
                else "No working document was deleted.") in erased["report"]
    assert os.listdir(tmp_path / "data" / "documents") == [doc]
    with pytest.raises(Exception, match="no open document"): call("look_at_page", doc_id=other)


def test_a_signature_file_that_cannot_be_removed_makes_the_erase_incomplete(server, tmp_path, monkeypatch):
    shot = in_handoff(tmp_path, photo(800, 600), "signature.jpg")
    call("store_signature", preview_id=js(call("preview_signature", path=shot))["preview_id"])
    real = os.unlink

    def stuck(path, *a, **k):
        if str(path).endswith("signature.png"):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(os, "unlink", stuck)
    erased = _erase_result(call("erase_pdf_editor"))
    assert erased["erasure"] == "incomplete" and "signature.png" in erased["report"]
    assert erased["signature_removed"] is False


def test_every_tool_refuses_without_plugin_data(server, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA")
    for name, args in (("signature_status", {}), ("erase_pdf_editor", {}),
                       ("open_pdf", {"path": in_handoff(tmp_path, flat_form())})):
        with pytest.raises(Exception, match="CLAUDE_PLUGIN_DATA is not set; the plugin has nowhere to keep its data"):
            call(name, **args)


def test_write_on_pdf_lets_marks_and_fields_be_omitted(server, tmp_path):
    import pdf_server
    schema = pdf_server.mcp._tool_manager.get_tool("write_on_pdf").parameters
    assert set(schema["required"]) == {"doc_id"}
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    assert js(call("write_on_pdf", doc_id=doc))["version"] == 0
    assert js(call("write_on_pdf", doc_id=doc, marks=None, fields=None))["version"] == 0
    assert js(call("write_on_pdf", doc_id=doc, marks=text("x"), fields={}))["version"] == 1


def every_tool_result(server, tmp_path) -> dict[str, str]:
    out = {}

    def run(name, **args):
        result = call(name, **args)
        out[name] = "\n".join(c.text for c in result if c.type == "text")
        return js(result)

    run("signature_status")
    pid = run("preview_signature", path=in_handoff(tmp_path, photo(800, 600), "signature.jpg"))["preview_id"]
    run("store_signature", preview_id=pid)
    doc = run("open_pdf", path=in_handoff(tmp_path, flat_form()))["doc_id"]
    run("look_at_page", doc_id=doc)
    run("write_on_pdf", doc_id=doc, marks=text("one"))
    run("undo_last_write", doc_id=doc)
    version = run("write_on_pdf", doc_id=doc, marks=text("two"))["version"]
    run("sign_pdf", doc_id=doc, filename="form.pdf", version=version, look="handwritten", page=1, x=72, y=720, width=60)
    run("send_pdf_to_chat", doc_id=doc)
    run("erase_pdf_documents")
    run("remove_signature")
    run("erase_pdf_editor")
    return out


def test_only_the_deliver_tool_returns_a_handoff_path(server, tmp_path):
    # run every tool once on a prepared document; collect all text results
    results = every_tool_result(server, tmp_path)
    assert len(results) == 12
    for name, text in results.items():
        assert ("/handoff" in text) == (name == "send_pdf_to_chat"), name


def test_a_preview_that_cannot_be_delivered_leaves_nothing_pending(server, tmp_path, casa_dirs, monkeypatch):
    import casa_broker

    def refused(*a, **k):
        raise casa_broker.DepositFailed("broker_down")

    monkeypatch.setattr(delivery, "_deposit_file", refused)
    shot = in_handoff(tmp_path, photo(800, 600), "signature.jpg")
    with pytest.raises(Exception, match="nothing is pending, send the photo again"):
        call("preview_signature", path=shot)
    assert js(call("signature_status"))["pending"] is False and os.listdir(casa_dirs.outbox) == []


def test_a_preview_that_cannot_be_held_pending_is_never_sent(server, tmp_path, casa_dirs, monkeypatch):
    deposits = []
    monkeypatch.setattr(delivery, "_deposit_file", lambda *a, **k: deposits.append(a) or REFERENCE)
    shot = in_handoff(tmp_path, photo(800, 600), "signature.jpg")

    real = os.replace

    def broken(*a, **k):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(Exception, match="Input/output error"):
        call("preview_signature", path=shot)
    monkeypatch.setattr(os, "replace", real)
    assert deposits == [] and os.listdir(casa_dirs.outbox) == []
    assert js(call("signature_status"))["pending"] is False


# -- several server processes on one data folder ------------------------------------------

SIGN = dict(filename="form.pdf", look="stamp", page=1, x=72, y=720, width=180, signer_name="A. Example")
MOVED = "the document changed since version 1; show the current draft and ask again"
STAMP = "SIGNED ELECTRONICALLY"


def history(tmp_path, doc) -> tuple[list[int], list[str]]:
    """The version numbers the document records, and the files its folder holds."""
    folder = tmp_path / "data" / "documents" / doc
    with open(folder / "meta.json", encoding="utf-8") as fh:
        return [v["n"] for v in json.load(fh)["versions"]], sorted(os.listdir(folder))


def pdf_text(data: bytes) -> str:
    return pymupdf.open(stream=data, filetype="pdf")[0].get_text()


def test_two_processes_writing_one_document_both_get_a_version(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    words = {side: [f"{side}{i:02d}" for i in range(20)] for side in "ab"}
    marks = {side: [{"kind": "text", "page": 1, "x": x, "y": 60 + 25 * i, "text": word}
                    for i, word in enumerate(words[side])] for side, x in (("a", 72), ("b", 330))}
    with child(write_each, doc, marks["a"]) as one, child(write_each, doc, marks["b"]) as two:
        assert (one.told(), two.told()) == ("ready", "ready")
        one.tell(); two.tell()
        outcomes = one.told() + two.told()
    assert [o for o in outcomes if isinstance(o, str)] == []             # no call failed
    assert sorted(o["version"] for o in outcomes) == list(range(1, 41))  # 40 distinct numbers
    versions, files = history(tmp_path, doc)
    assert versions == list(range(41))
    assert files == sorted(["meta.json", *(f"v{n}.pdf" for n in range(41))])
    last = pdf_text(Store(str(tmp_path / "data")).latest_bytes(doc))
    assert [w for w in words["a"] + words["b"] if w not in last] == []


def test_a_reader_does_not_delete_a_version_being_written(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    failed = []
    with child(write_until_told, doc) as writer:
        assert writer.told() == "ready"
        writer.tell()
        assert writer.told() == "writing"
        for _ in range(300):
            try:
                call("look_at_page", doc_id=doc)
            except Exception as exc:
                failed.append(str(exc))
        writer.tell("stop")
        outcomes = writer.told()
    assert failed == [] and [o for o in outcomes if isinstance(o, str)] == []
    versions, files = history(tmp_path, doc)
    assert [o["version"] for o in outcomes] == versions[1:]
    assert [f"v{n}.pdf" for n in versions if f"v{n}.pdf" not in files] == []


def test_a_write_after_signing_starts_from_the_unsigned_version(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    assert js(call("sign_pdf", doc_id=doc, version=1, **SIGN))["signed"] is True
    again = js(call("write_on_pdf", doc_id=doc, marks=[{"kind": "text", "page": 1, "x": 72, "y": 500, "text": "more"}]))
    assert again["signature_removed"] is True and again["version"] == 3
    store = Store(str(tmp_path / "data"))
    content = pdf_text(store.latest_bytes(doc))
    assert STAMP not in content and "one" in content and "more" in content
    assert store.get(doc).signed is False


def test_signing_a_version_that_moved_is_refused(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    shown = js(call("look_at_page", doc_id=doc))["version"]             # the draft the approval is for
    with child(call_once, "write_on_pdf", {"doc_id": doc, "marks": text("two")}) as writer:
        assert writer.told() == "ready"
        writer.tell()
        assert writer.told() == "attempting" and writer.told()["version"] == 2
    with pytest.raises(Exception, match=MOVED):
        call("sign_pdf", doc_id=doc, version=shown, **SIGN)
    assert history(tmp_path, doc)[0] == [0, 1, 2] and Store(str(tmp_path / "data")).get(doc).signed is False


def test_a_write_waits_while_a_version_is_being_signed(server, tmp_path, monkeypatch):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    draw, seen = signature.draw_stamp, {}
    with child(call_once, "write_on_pdf", {"doc_id": doc, "marks": text("two")}) as writer:
        assert writer.told() == "ready"

        def drawing(*args, **kwargs):      # the version was checked; the signed copy is not stored yet
            writer.tell()
            seen["writer"] = writer.told()
            seen["waits"] = writer.silent_for(0.5)
            return draw(*args, **kwargs)

        monkeypatch.setattr(signature, "draw_stamp", drawing)
        signed = js(call("sign_pdf", doc_id=doc, version=1, **SIGN))
        wrote = writer.told()
    assert seen == {"writer": "attempting", "waits": True}
    assert signed["version"] == 2 and wrote["version"] == 3 and wrote["signature_removed"] is True
    assert history(tmp_path, doc)[0] == [0, 1, 2, 3]


def test_what_is_sent_is_the_version_it_is_named_for(server, tmp_path, monkeypatch):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    call("sign_pdf", doc_id=doc, version=1, **SIGN)
    signed_bytes = Store(str(tmp_path / "data")).latest_bytes(doc)
    deposited, seen = [], {}

    def deposit(slot, path, *, kind, caption, filename):
        with open(path, "rb") as fh:
            deposited.append((filename, fh.read()))
        return REFERENCE

    monkeypatch.setattr(delivery, "_deposit_file", deposit)
    read = Store.latest_bytes
    with child(call_once, "undo_last_write", {"doc_id": doc}) as undo:
        assert undo.told() == "ready"

        def reading(self, doc_id):         # the sender has read the label, not yet the bytes
            undo.tell()
            seen["undo"] = undo.told()
            seen["waits"] = undo.silent_for(0.5)
            return read(self, doc_id)

        monkeypatch.setattr(Store, "latest_bytes", reading)
        sent = js(call("send_pdf_to_chat", doc_id=doc))
        undone = undo.told()
    assert seen == {"undo": "attempting", "waits": True}
    assert undone == {"version": 1, "signed": False}
    assert deposited == [("form (signed).pdf", signed_bytes)] and sent["sent_as"] == "form (signed).pdf"
    assert STAMP in pdf_text(signed_bytes) and casa_handoff.capture(sent["handoff_path"])[1] == signed_bytes


@pytest.mark.parametrize("tool, step, other", [("open_pdf", "open", "erase_pdf_documents"),
                                               ("look_at_page", "get", "undo_last_write"),
                                               ("undo_last_write", "undo", "write_on_pdf")])
def test_a_tool_reads_the_state_and_the_bytes_it_reports_in_one_locked_section(server, tmp_path, monkeypatch,
                                                                               tool, step, other):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    folder, fresh = tmp_path / "data" / "documents" / doc, flat_form()
    versions = {n: (folder / f"v{n}.pdf").read_bytes() for n in (0, 1)}
    args = {"path": in_handoff(tmp_path, fresh, "next.pdf")} if tool == "open_pdf" else {"doc_id": doc}
    other_args = {"erase_pdf_documents": {}, "undo_last_write": {"doc_id": doc},
                  "write_on_pdf": {"doc_id": doc, "marks": text("two")}}[other]
    first_step, read, seen = getattr(Store, step), Store.latest_bytes, {}
    with child(call_once, other, other_args) as second:
        assert second.told() == "ready"

        def stepping(self, *a, **k):       # the tool's first step on the store is done; it has no bytes yet
            result = first_step(self, *a, **k)
            if not seen:
                second.tell()
                seen["second"] = second.told()
                seen["waits"] = second.silent_for(0.5)
            return result

        def reading(self, doc_id):         # and the other call is still waiting once the bytes are read
            data = read(self, doc_id)
            seen["bytes"], seen["still waits"] = data, second.silent_for(0)
            return data

        monkeypatch.setattr(Store, step, stepping)
        monkeypatch.setattr(Store, "latest_bytes", reading)
        first = js(call(tool, **args))
        after = second.told()
    assert (seen["second"], seen["waits"], seen["still waits"]) == ("attempting", True, True)
    if tool == "open_pdf":
        assert first["version"] == 0 and seen["bytes"] == fresh
        assert (after["erasure"], after["documents_removed"]) == ("complete", 2)
    elif tool == "look_at_page":
        assert first["version"] == 1 and seen["bytes"] == versions[1]
        assert after == {"version": 0, "signed": False}
    else:
        assert first == {"version": 0, "signed": False} and seen["bytes"] == versions[0]
        assert after["version"] == 2


def test_a_refused_call_lets_go_of_the_lock(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    untouched = js(call("open_pdf", path=in_handoff(tmp_path, flat_form(), "other.pdf")))["doc_id"]
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    box = {"kind": "placeholder", "page": 1, "x": 72, "y": 720, "width": 150, "height": 50}
    for name, args, reason in (("sign_pdf", dict(doc_id=doc, version=0, **SIGN), "changed since version 0"),
                               ("write_on_pdf", dict(doc_id=doc, marks=[box]), "preview_only"),
                               ("look_at_page", dict(doc_id="0" * 16), "no open document"),
                               ("undo_last_write", dict(doc_id=untouched), "nothing to undo")):
        with pytest.raises(Exception, match=reason):       # each one refused inside its locked section
            call(name, **args)
    with child(call_once, "write_on_pdf", {"doc_id": doc, "marks": text("two")}) as second:
        assert second.told() == "ready"                    # this process stays alive: nothing frees the lock for it
        second.tell()
        assert second.told() == "attempting" and second.told()["version"] == 2


def test_a_preview_of_a_large_form_does_not_hold_the_lock(server, tmp_path, monkeypatch):
    form = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    other = js(call("open_pdf", path=in_handoff(tmp_path, flat_form(), "other.pdf")))["doc_id"]
    apply, seen = marks_mod.apply, {}
    with child(call_once, "look_at_page", {"doc_id": other}) as looker:
        assert looker.told() == "ready"

        def computing(*args):              # held here for the seconds a large form takes
            looker.tell()
            seen["looker"] = looker.told()
            seen["looked"] = looker.told()
            return apply(*args)

        monkeypatch.setattr(marks_mod, "apply", computing)
        tried = js(call("write_on_pdf", doc_id=form, marks=text("A. Example"), preview_only=True))
    assert seen["looker"] == "attempting" and seen["looked"]["version"] == 0 and seen["looked"]["page"] == 1
    assert tried["version"] == 0 and tried["preview_only"] is True
    assert history(tmp_path, form) == ([0], ["meta.json", "v0.pdf"])


def test_an_edit_that_marks_nothing_adds_no_version_and_a_preview_adds_none(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    untouched = ([0], ["meta.json", "v0.pdf"])
    tried = call("write_on_pdf", doc_id=doc, marks=text("A. Example"), preview_only=True)
    assert js(tried)["version"] == 0 and tried[0].type == "image" and history(tmp_path, doc) == untouched
    failed = call("write_on_pdf", doc_id=doc, marks=[{"kind": "text", "page": 9, "x": 1, "y": 1, "text": "x"}])
    assert [c.type for c in failed] == ["text"] and js(failed)["version"] == 0
    assert js(failed)["note"] == "Nothing was applied; the document is unchanged."
    assert history(tmp_path, doc) == untouched
    call("write_on_pdf", doc_id=doc, marks=text("one"))
    call("sign_pdf", doc_id=doc, version=1, **SIGN)
    tried = js(call("write_on_pdf", doc_id=doc, marks=text("try"), preview_only=True))
    assert tried["signature_removed"] is True and tried["version"] == 2    # a signed document's too
    assert history(tmp_path, doc)[0] == [0, 1, 2]


def test_erase_while_another_process_writes_leaves_nothing_behind(server, tmp_path):
    doc = js(call("open_pdf", path=in_handoff(tmp_path, flat_form())))["doc_id"]
    erased = []
    eraser = threading.Thread(target=lambda: erased.append(_erase_result(call("erase_pdf_documents"))), daemon=True)
    with child(write_until_told, doc, True) as writer:
        assert writer.told() == "ready"
        writer.tell()
        assert writer.told() == "inside"       # a write is under way: its base read, its version not stored
        eraser.start()
        eraser.join(0.5)
        waited = eraser.is_alive()
        writer.tell()
        assert writer.told() == "writing"
        eraser.join(WAIT)
        writer.tell("stop")
        outcomes = writer.told()
    assert waited and outcomes[0]["version"] == 1                # the erase came after that write, whole
    assert [(e["erasure"], e["documents_removed"]) for e in erased] == [("complete", 1)]
    assert sorted(os.listdir(tmp_path / "data")) == ["documents", "documents.lock"]
    assert os.listdir(tmp_path / "data" / "documents") == []
    refused = [o for o in outcomes if isinstance(o, str)]
    assert refused and outcomes[-len(refused):] == refused       # after the erase, never a version again
    assert [r for r in refused if "There is no open document with id" not in r] == []
