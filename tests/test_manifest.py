import asyncio
import json
import os
import string

import pdf_server

MANIFEST = os.path.join(os.path.dirname(__file__), "..", ".claude-plugin", "plugin.json")


def _manifest():
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def test_manifest_agrees_with_the_server():
    m = _manifest()["casa"]
    tools = {t.name for t in asyncio.run(pdf_server.mcp.list_tools())}
    assert {t.rsplit("__", 1)[1] for t in m["provides_tools"]} == tools == set(m["resultContract"]["tools"])
    assert all(t.startswith("mcp__plugin_pdf-editor_pdf-editor__") for t in m["provides_tools"])
    assert {p["name"] for p in m["protectedTools"]} == {"sign_pdf", "store_signature", "remove_signature", "erase_pdf_editor", "erase_pdf_documents"}
    assert set(m["profiles"]["fill"]) <= tools and not set(m["profiles"]["fill"]) & {p["name"] for p in m["protectedTools"]}
    caps = {n for n, e in m["resultContract"]["tools"].items() if e["result"] == "capability"}
    assert caps == {"send_pdf_to_chat", "preview_signature"}
    for name in caps:
        assert m["resultContract"]["tools"][name] == {"result": "capability", "provides": ["file"],
                                                      "delivers": {"file": "operator_file"}, "filename": True}
    assert m["eraseTool"] == "erase_pdf_editor" and m["eraseDataOnlyTool"] == "erase_pdf_documents"
    assert "callbacks" not in m and "setupTool" not in m


def test_protected_summaries_use_only_scalar_arguments_of_their_tool():
    # every {placeholder} in a summary is a str/int/float parameter of that tool; summary <= 200 chars
    schemas = {t.name: t.inputSchema for t in asyncio.run(pdf_server.mcp.list_tools())}
    for entry in _manifest()["casa"]["protectedTools"]:
        summary, props = entry["summary"], schemas[entry["name"]]["properties"]
        assert len(summary) <= 200, entry["name"]
        for _, field, _, _ in string.Formatter().parse(summary):
            if field is not None:
                assert props[field].get("type") in ("string", "integer", "number"), (entry["name"], field)

