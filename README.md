# casa-plugin-pdf-editor

Lets the assistant (Casa's resident) fill in and sign PDFs in chat. The assistant looks at each page (an image with a ruler, plus the text, drawn lines, boxes and form fields with their positions), decides itself what to write and where, and the plugin draws exactly that: text, ticks, crosses, strike-throughs and form-field values. Every write returns the page image, so the assistant checks and corrects its own work before you see anything. It then shows you the draft in chat and, once you approve, signs it with your stored signature and sends you the signed file. Another plugin (Gmail) can take the same file to attach it to an email.

The plugin never guesses what a blank is for or where something should go: it reports facts and draws marks. It works on flat PDFs with no form fields, on scans, and on PDFs with real form fields.

No setup: no credentials, no sign-in, no environment variables to configure.

## Prerequisites

- **Casa 0.344.0 or later.** An older Casa lacks the delivery of a file into the chat that this plugin's results rely on.
- **An `amd64` Home Assistant host.** `server/vendor/` holds compiled modules for CPython 3.11 on linux x86_64 only, so on `aarch64` the plugin's server cannot start (see Licence).
- An assistant that can see images returned by a tool: placement depends on it.

## Install

Ask the assistant, for example: *"Install the PDF editor plugin from bonzanni/casa-plugin-pdf-editor for the assistant."* The configurator installs the latest release and assigns it.

Assign it to the main assistant: getting a file sent in chat into the plugin needs Casa's `share_inbound_file`, which only the resident has, and the values that go in a form come from its memory.

## How it is used

1. You send a PDF in chat, or point at an email attachment (Gmail's `download_attachment` puts it in Casa's handoff folder).
2. The assistant opens it, looks at the pages and fills them in, asking you only for what it does not know.
3. It sends you the draft, named `<file> (draft).pdf`.
4. If the form needs a signature, it tells you what it will sign and with which look; Casa asks you to approve.
5. It sends you the signed file, named `<file> (signed).pdf`. To email it, it hands the file to Gmail, which asks for your approval again before sending.

## Tools

| Tool | Description |
|---|---|
| `open_pdf` | Open a PDF from the handoff folder. Returns the document id, page count, version, page 1's image and facts. Warns if the file carries a digital signature that any change invalidates |
| `look_at_page` | One page's image (with a ruler in PDF points) and facts; `region` zooms in on an area at double resolution |
| `write_on_pdf` | Add marks (text, tick, cross, strike, and a grey `placeholder` box for trying a position) and form-field values, making a new version. Returns a report per item and images of the pages touched. `preview_only` draws without storing |
| `undo_last_write` | Drop the current version and go back to the one before (a signing is undone too) |
| `send_pdf_to_chat` | Send the current version to your chat as `(draft)` or `(signed)`, and return `handoff_path`, the same PDF for another plugin. The only tool that returns a path |
| `sign_pdf` ⚠️ | Sign the current version with the `handwritten` or `stamp` look. Names the file and version; a version other than the current one is refused |
| `preview_signature` | Clean a photo of a handwritten signature and show you the result in chat; returns a `preview_id` |
| `store_signature` ⚠️ | Store the previewed signature (named by its `preview_id`) as the one used for signing, replacing any earlier one |
| `signature_status` | Whether a signature is stored (and its height-to-width `aspect`), and whether a preview is pending |
| `remove_signature` ⚠️ | Delete the stored signature and any pending preview |
| `erase_pdf_editor` ⚠️ | Casa's `eraseTool`: deletes every working document and the stored signature |
| `erase_pdf_documents` ⚠️ | Casa's `eraseDataOnlyTool`: deletes every working document; keeps the signature |

⚠️ Protected tools: Casa asks you to approve before they run.

`send_pdf_to_chat` and `preview_signature` are declared `capability` tools in `casa.resultContract` (they deliver an `operator_file`, with its file name); every other tool is `safe`.

The manifest declares one access profile, `fill`: `open_pdf`, `look_at_page`, `write_on_pdf`, `undo_last_write`, `send_pdf_to_chat` and `signature_status`. An agent assigned the plugin with that profile can fill in and show documents but cannot sign, manage the signature or erase. An assignment without a profile gets every tool.

## The signature

What signing produces is a **visual signature**: an image or a drawn stamp on the page. It is **not a qualified electronic signature**, and nothing is signed cryptographically. Whether a visual signature is enough is up to whoever asked for the form.

There are two looks:

- **handwritten**, when a signature is stored: your signature, background removed, placed where the assistant decides.
- **stamp**, when none is stored: a framed box captioned "signed electronically" with the signer's name in a handwriting-style font, and the name and time in small print (for example `A. Example`).

The assistant says beforehand which look applies. The signed version is final: form fields are flattened, and writing on it again starts from the last unsigned version and needs a new signature.

**Register** a signature by signing a sheet of white paper, photographing it and sending the photo to the assistant, saying it is your signature. You get a cleaned preview in chat; if it is sideways, say so and the assistant rotates it. When you are happy, the assistant stores it and Casa asks you to approve. **Replace** it the same way. **Remove** it by asking the assistant (Casa asks you to approve). The signature is stored only in the plugin's data directory.

## Known limits

- A visual signature only, as above. One signature per install.
- A document with a rotated page whose visible area is offset or scaled (rotated and cropped, rotated with an offset page box, or rotated with a scaled page unit) is refused when opened. Encrypted files and XFA forms are refused too.
- Add-only editing: the plugin cannot remove or change what is printed on the page, and does no page operations.
- Form text fields take only Western European characters; for other characters the assistant writes a text mark instead, which covers what the bundled DejaVu Sans font has.
- The signature photo you send also sits in Casa's inbox and handoff folder for their normal retention (seven days in the handoff folder), which the plugin does not control.
- Working documents are kept 7 days, then removed.
- If Casa itself freezes for more than 60 seconds while sending a file to your chat, the assistant can receive the file's handoff path without the file having reached you. Signing and emailing still each need your approval.

## Uninstalling

When you uninstall the plugin, Casa asks what to do with its data:

- **Keep data**: the plugin is removed and its data directory stays.
- **Erase data, keep the signature**: `erase_pdf_documents` deletes every working document.
- **Erase everything**: `erase_pdf_editor` deletes every working document and the stored signature.

Neither eraser touches files already in Casa's handoff folder or chat, or Home Assistant backups taken earlier.

## Env vars

| Variable | Required | Description |
|---|---|---|
| `CLAUDE_PLUGIN_DATA` | Provided by Casa | Plugin data directory (working documents and the stored signature) |
| `CASA_HANDOFF_DIR` | Casa (default `/data/handoff`) | Casa's shared file handoff folder: PDFs and photos come from here, and `send_pdf_to_chat` publishes here |
| `CASA_PLUGIN_OUTBOX_DIR` | Casa (default `/data/plugin-outbox`) | Casa's plugin outbox: a file bound for the chat is staged here and Casa takes it |

## Licence

[AGPL-3.0-or-later](LICENSE) for this plugin's own code (`server/*.py`, `skills/`, `scripts/`, `tests/` and the manifests). The AGPL is needed because the plugin uses PyMuPDF.

`server/vendor/` vendors the plugin's Python dependencies so it runs without a network install step:

- **PyMuPDF 1.28.2**, under the GNU AGPL (dual-licensed by Artifex with a commercial licence). Its licence text is in `server/vendor/pymupdf-1.28.2.dist-info/COPYING`; its source is at https://github.com/pymupdf/PyMuPDF/tree/1.28.2.
- The other vendored packages (`mcp` 1.3.0 and its dependencies) remain under their own licences (MIT, BSD, Apache-2.0 and others); each ships its licence text in its `*.dist-info/` directory.

The bundled fonts keep their own licences: DejaVu Sans (`server/fonts/DejaVuSans-LICENSE.txt`, Bitstream Vera licence) and Caveat (`server/fonts/Caveat-OFL.txt`, SIL Open Font License 1.1). `server/VENDORED.md` records where each vendored file came from.

**Platform constraint:** `server/vendor/` contains compiled modules built for **CPython 3.11 on linux x86_64** (`pymupdf`, `pydantic_core`). The vendored tree will not import on another Python version or architecture, so a deployment or fork targeting anything else must re-vendor it from `server/requirements.txt`.

## Development

Tests run against the vendored tree with CPython 3.11:

```
uv run --python 3.11 --with pytest==8.3.4 --no-project python -m pytest tests -q
python scripts/check_tracked_files.py
```

The second command fails if a PDF or image file is tracked anywhere outside `server/vendor/` and `server/fonts/` (by extension or by its first bytes). Test documents and photos are generated in `tests/fixtures.py`, so a tracked one is most likely a real document committed by accident. It does not check text files for personal data. CI runs both on every pull request and push to main, and publishes `v<version>` from `plugin.json` when a new version reaches main.
