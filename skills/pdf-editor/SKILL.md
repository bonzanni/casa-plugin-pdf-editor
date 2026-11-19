---
name: pdf-editor
description: Use when the operator asks you to fill in, complete or sign a PDF or a form (sent in chat or attached to an email), to send a signed PDF on, or to register, replace or remove the signature used for signing.
---

# Filling in and signing PDFs

Get the file into the handoff folder (`share_inbound_file` for a file the operator sent in chat, the Gmail plugin's `download_attachment` for an attachment) and call `open_pdf` with that path.

**You decide what goes where, by looking.** Each page comes as an image with a ruler in PDF points, plus facts: text lines, drawn lines, boxes and form fields, each with coordinates. The plugin never guesses what a blank is for; you read the page and judge. Use `look_at_page` for other pages, and its `region` to zoom in when a detail is too small to judge.

Take the values from what you already know about the operator. Ask only for what is missing, in one message.

Write with `write_on_pdf`. Every write returns the page image: look at it and fix anything misplaced (`undo_last_write`, or try first with `preview_only`) before the operator sees anything.

Then `send_pdf_to_chat` so the operator sees the draft.

## Signing

Signing is a protected call: Casa asks the operator. Say in one line what will be signed and with which look, then call `sign_pdf` with the filename and the version you just showed. Do not also ask in prose and wait. If it is refused because the document changed, show the current draft and ask again.

Check `signature_status` first. If a signature is stored, the look is `handwritten`; if none is, it is `stamp`, a framed "signed electronically" box with the name in a script font, which needs `signer_name`. Tell the operator beforehand which one applies. Try the position first with a `placeholder` mark in `preview_only` mode, the same size as the signature: for handwritten the height is the width times `aspect` from `signature_status`, for the stamp a third of the width. `sign_pdf` takes the bottom-left corner.

After signing, `send_pdf_to_chat` again. To email the file, pass the `handoff_path` it returns to the Gmail plugin's attachment argument; only that tool returns a path.

## Registering a signature

The operator sends a photo of their signature on white paper. Get it into the handoff folder with `share_inbound_file` and call `preview_signature`. The operator sees the cleaned preview; you do not. If they say it is sideways or upside down, call it again with `rotate`. When they are happy, call `store_signature` with the `preview_id` it returned; Casa asks them to approve. `remove_signature` deletes it.

## Practical notes

- Coordinates are PDF points from the top-left of the page as displayed, x right, y down.
- Text is placed by its baseline: to write on a line, use a y a couple of points above the line's y.
- A mark over a form field can be covered by the field; the returned image shows it. Prefer setting the field's value.
- A text field cannot show characters outside Western European encoding (the refusal says so); use a text mark there instead.
- The plugin only adds to the page. It cannot remove printed dots or text.
- A signed version is final and flattened. Writing again starts from the last unsigned version and needs a new signature.
- A refusal is a sentence to relay or act on, not something to retry unchanged.
