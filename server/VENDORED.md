<!-- Source: github.com/bonzanni/casa-plugin-gmail @ cbf628b (server/) -->
# Vendored modules

These files are verbatim copies. Never edit them here; update them by copying
the new upstream version and recording its SHA above and below.

| File | Upstream path | Commit | sha256 |
|---|---|---|---|
| `casa_handoff.py` | `casa-plugin-gmail/server/casa_handoff.py` | `cbf628b` | `8996c30b703a29a9bc5d716d85faf59c98375a82b239c9afd8fafd0a565d85bb` |
| `casa_broker.py` | `casa-plugin-gmail/server/casa_broker.py` | `cbf628b` | `bdaeaa0786995ba5fe9b98125ed8e84120d64b8066cce794801d4ecec2dc5ff3` |

# Vendored dependencies

`server/vendor/` holds `mcp==1.3.0` and `PyMuPDF==1.28.2` with their
dependencies (cp311 / abi3, manylinux_2_28 x86_64 wheels). `requirements.txt`
pins the two; the rest resolve from them.

**Local patch:** `vendor/mcp/server/fastmcp/utilities/func_metadata.py` is copied
from `casa-plugin-gmail` @ `cbf628b` (one-line backport from mcp 1.30: a
`str`-annotated argument is never JSON-decoded). A re-vendor must re-apply it;
`tests/test_server_boot.py` fails if it is missing.

# Fonts

| File | Source | Licence |
|---|---|---|
| `fonts/DejaVuSans.ttf` | dejavu-fonts 2.37 release (`dejavu-fonts-ttf-2.37.zip`, `ttf/DejaVuSans.ttf`) | `fonts/DejaVuSans-LICENSE.txt` (Bitstream Vera / public domain changes) |
| `fonts/Caveat-Regular.ttf` | `googlefonts/caveat` @ `fc2a58c`, `fonts/ttf/Caveat-Regular.ttf` (google/fonts ships only the variable font) | `fonts/Caveat-OFL.txt` (SIL OFL 1.1, from `google/fonts` `ofl/caveat/OFL.txt`) |
