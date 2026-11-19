import io
import os

import pymupdf
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

PLUGIN_DATA = os.environ.get("CLAUDE_PLUGIN_DATA", "/tmp/pdf-editor-plugin-data")

mcp = FastMCP("pdf-editor")


@mcp.tool()
def image_probe() -> list:
    """Temporary probe: returns a PNG image block followed by a text block."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((60, 120), "HERON", fontsize=48, fontname="helv")
    png = page.get_pixmap().tobytes("png")
    doc.close()
    return [Image(data=png, format="png"), "probe"]


if __name__ == "__main__":
    mcp.run()
