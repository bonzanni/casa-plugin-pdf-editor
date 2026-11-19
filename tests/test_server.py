import asyncio
import json
import os
import shutil

import pymupdf
import pytest

import casa_handoff
import delivery
from fixtures import flat_form, in_handoff, photo, signed_flag_form

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
