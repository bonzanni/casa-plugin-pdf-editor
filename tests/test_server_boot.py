def test_vendored_stack_imports_and_gmail_patch_present():
    import asyncio

    import pymupdf
    import pdf_server
    from mcp.server.fastmcp import FastMCP

    assert pymupdf.VersionBind == "1.28.2"
    assert pdf_server.mcp.name == "pdf-editor"

    # The mcp 1.3.0 local patch (see server/VENDORED.md): a str-annotated
    # argument is never JSON-decoded, so "1" must stay the string "1".
    probe = FastMCP("patch-probe")

    @probe.tool()
    def echo(value: str) -> str:
        return type(value).__name__

    out = asyncio.run(probe.call_tool("echo", {"value": "1"}))
    assert out[0].text == "str"


def test_look_probe_returns_an_image_block():
    import asyncio

    import pdf_server

    out = asyncio.run(pdf_server.mcp.call_tool("image_probe", {}))
    assert [c.type for c in out] == ["image", "text"]
