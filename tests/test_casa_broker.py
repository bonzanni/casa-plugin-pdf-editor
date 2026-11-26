"""The id of the tool call being served, and the deposits that name it.

Claude Code sends a tool call's id in the request's metadata
(`params._meta["claudecode/toolUseId"]`). The deposits below go to a fake
broker on a real Unix socket that speaks Casa's deposit route the way Casa's
aiohttp handler does (one HTTP POST, a JSON body in, a JSON object out), so
the test sees the exact body Casa would receive.
"""
import asyncio
import http.server
import json
import os
import socketserver
import tempfile
import threading

import anyio
import pytest
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

import casa_broker
from fixtures import flat_form, in_handoff

REFERENCE = "casa-cap-" + "0123456789abcdef" * 2
CLIENT = "0f" * 16


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        pass                  # a client that timed out and left is the test


class FakeBroker:
    """A Unix-socket HTTP server that answers every deposit with a reference."""

    def __init__(self, path):
        self.requests = []            # (method, path, parsed body)
        broker = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def address_string(self):          # a Unix peer has no host
                return "unix"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                broker.requests.append((self.command, self.path, body))
                data = json.dumps({"reference": REFERENCE}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = _Server(path, Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02},
                                       daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def broker(monkeypatch):
    """A fake broker, with Casa's two broker env vars pointing at it. The
    socket lives in a short temp directory: a Unix socket path is limited to
    about a hundred bytes."""
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "internal.sock")
        monkeypatch.setenv(casa_broker.ENV_SOCKET, sock)
        monkeypatch.setenv(casa_broker.ENV_CLIENT, CLIENT)
        fake = FakeBroker(sock)
        try:
            yield fake
        finally:
            fake.close()


def _call_through_server(server, name, arguments=None, meta=None):
    """Call tool `name` of the FastMCP `server` in process the way Claude Code
    does, with `meta` as the request's `_meta`; return the text the tool
    returned. `ClientSession.call_tool` cannot send `_meta`, so the request is
    built by hand."""
    params = {"name": name, "arguments": arguments or {}}
    if meta is not None:
        params["_meta"] = meta
    request = types.ClientRequest(types.CallToolRequest(
        method="tools/call", params=types.CallToolRequestParams(**params)))

    async def call():
        async with create_connected_server_and_client_session(
                server._mcp_server) as session:
            return await session.send_request(request, types.CallToolResult)

    result = anyio.run(call)
    if result.isError:
        raise AssertionError(result.content)
    [content] = result.content
    return content.text


def _report_call_id():
    return json.dumps(casa_broker.call_id())


def _call_id_seen(meta=None):
    server = FastMCP("scratch")
    server.tool()(_report_call_id)
    return json.loads(_call_through_server(server, "_report_call_id", meta=meta))


def test_the_helper_sees_the_calls_id_through_the_real_server():
    assert _call_id_seen({"claudecode/toolUseId": "toolu_test"}) == "toolu_test"


def test_without_meta_there_is_no_id():
    assert _call_id_seen() is None
    assert _call_id_seen({"progressToken": 1}) is None
    assert casa_broker.call_id() is None                # outside any request


@pytest.mark.parametrize("value", [7, ["toolu_test"], {"id": "toolu_test"}, None, "", "t" * 129])
def test_an_id_that_is_not_a_short_string_is_not_an_id(value):
    assert _call_id_seen({"claudecode/toolUseId": value}) is None


def test_an_id_of_128_characters_is_an_id():
    assert _call_id_seen({"claudecode/toolUseId": "t" * 128}) == "t" * 128


@pytest.fixture
def opened(tmp_path, casa_dirs, monkeypatch, broker):
    """The real PDF server with one document open, its deposits going to the
    fake broker."""
    import pdf_server
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    result = asyncio.run(pdf_server.mcp.call_tool(
        "open_pdf", {"path": in_handoff(tmp_path, flat_form())}))
    doc_id = json.loads(result[-1].text)["doc_id"]
    return pdf_server, doc_id


def test_a_deposit_carries_the_id_when_there_is_one(opened, broker):
    pdf_server, doc_id = opened
    sent = json.loads(_call_through_server(
        pdf_server.mcp, "send_pdf_to_chat", {"doc_id": doc_id},
        meta={"claudecode/toolUseId": "toolu_test"}))
    assert sent["file"] == REFERENCE
    [(method, path, body)] = broker.requests
    assert (method, path) == ("POST", "/internal/broker/deposit")
    assert sorted(body) == ["client", "filename", "kind", "slot", "tool_use_id", "value"]
    assert body["tool_use_id"] == "toolu_test"


def test_a_deposit_without_an_id_is_todays_request(opened, broker, casa_dirs):
    pdf_server, doc_id = opened
    sent = json.loads(_call_through_server(
        pdf_server.mcp, "send_pdf_to_chat", {"doc_id": doc_id}))
    assert sent["file"] == REFERENCE
    [(_, _, body)] = broker.requests
    assert body == {"client": CLIENT, "slot": "file", "value": body["value"],
                    "kind": "document", "filename": "form (draft).pdf"}
    assert os.path.dirname(body["value"]) == casa_dirs.outbox
