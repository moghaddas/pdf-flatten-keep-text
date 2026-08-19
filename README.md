# skia-pdf-flatten-keep-text

**Makes an artwork-heavy Chrome print-to-PDF export open in macOS Preview.**
Chrome's print-to-PDF backend stamps `Producer: Skia/PDF`, and a deck out of it
can open black: pages paint only after a scroll, then un-paint when you scroll
away. It reads like a viewer bug. It is the file. This tool flattens each page's
artwork to one opaque image and replays the text over it, so the page opens
anywhere and still selects, searches and copies.

Only heavy artwork does this. A plain Chrome printout carries almost none of the
same structure and opens fine, so the target is the export, not the browser.
Mine came out of Claude Design. Re-encoding the images does not help: decoded
weight falls, the pages stay black. Removing the compositing is what fixes it.

```
pipx install git+https://github.com/moghaddas/skia-pdf-flatten-keep-text
skia-pdf-flatten-keep-text deck.pdf
```

`pipx` also needs poppler-utils, which is not a Python package. See
[What you need](#what-you-need).

## Never run this on a redacted document

The tool moves every text block above every piece of artwork. That is the whole
idea, and it is wrong for any page where artwork was drawn **over** text:

- redaction bars and cover boxes
- stamps and watermarks
- highlight and sticker overlays

On those pages the hidden text is replayed above whatever covered it, and
becomes readable. Nothing new enters the bytes, the words were always in the
file, but a page that looked safe now shows what it hid.

The verifier catches this and refuses to hand you the file. It renders both
documents and counts new marks landing where the original drew flat. Cover a
line of text, and those marks are the line coming back:

```
$ skia-pdf-flatten-keep-text sample-redacted.pdf
  new marks on flat areas: 0.4603% (page 1)   FAIL: over 0.0200%
  verification FAILED - output quarantined at sample-redacted-hybrid.pdf.rejected
```

Treat that as a stop sign, not a threshold to tune. `--force` exists for when you
have looked at the rejected file and know why it differs.

## The bug

A Skia print-to-PDF export builds every page out of transparency groups, soft
masks and shading-filled forms, nested several deep. Nothing in there is a
plain painted object. A viewer has to composite the whole stack on every repaint,
and macOS Preview gives up: black pages, blank pages, or pages that appear only
after you scroll past them and back.

Re-encoding the embedded images does not help, because the images are not the
problem. The compositing is.

The sample in this repository has the same shape. Three pages, 120 form
xobjects, 42 of them soft-mask groups:

```
$ python3 make-sample.py .
$ skia-pdf-flatten-keep-text sample-deck.pdf
3 pages, 120 form xobjects (3 contain text, 42 are mask groups)
```

## What it does

The tool splits each content stream in two.

**The backdrop.** Everything that is not a text block renders to one opaque
JPEG per page. One image, no alpha, no groups, no masks. A viewer walks a
single sequential scan.

**The text.** Every `BT..ET` block is replayed over that image with its original
font, colour, transform and clipping path intact. Real glyphs, not an OCR guess,
so they stay selectable and sharp at any zoom.

Gradient headlines are not text. Chrome draws them by filling a rectangle
through a luminosity mask whose group contains the glyphs. The fill belongs to
the backdrop, so they would leave with it and the headline would stop being
searchable. The tool keeps a copy in text rendering mode 3, which paints
nothing, and draws it where the fill used to be.

Scopes that no longer draw anything come out. Keeping graphics state also keeps
`q /GsN gs Q` blocks whose only painting was artwork the text pass dropped. They
paint nothing and still install a soft mask, the exact thing this tool exists to
delete.

## What you need

Python 3.8 or newer, and poppler-utils for `pdftoppm`, `pdfinfo`, `pdftotext`
and `pdfimages`. Pillow installs automatically with the package below; poppler
does not, since it is not a Python package:

```
GH=git+https://github.com/moghaddas/skia-pdf-flatten-keep-text
pipx install $GH                     # or: uvx --from $GH skia-pdf-flatten-keep-text deck.pdf
apt install poppler-utils            # or: brew install poppler
```

It is not on PyPI, so install from the repository. `pipx` and `uvx` both take a
`git+https://` source directly.

Running either command without poppler-utils installed exits with the exact
package name to install, rather than a raw subprocess traceback.

No PDF library beyond Pillow. `ctok.py` is a small content-stream tokenizer
and object-graph reader, and it is the only PDF-parsing import.

The backdrop render has no memory ceiling: `Image.MAX_IMAGE_PIXELS` is unset
so a legitimate large-format page still renders at 300 dpi, but the same
setting means a hostile PDF can drive memory use arbitrarily high. Do not
point this at a PDF from an untrusted source.

## Use

```
skia-pdf-flatten-keep-text IN.pdf [OUT.pdf]     default OUT: IN-hybrid.pdf
  --dpi N          backdrop render resolution   (default 300)
  --quality N      backdrop JPEG quality        (default 90)
  --keep-temp      leave the intermediate files in place
  --force          keep a failed output under its normal name
```

Every run verifies itself and prints what it checked:

```
verifying:
  pages: 3
  text layer: identical
  images per page: 1 (flat)
  worst page decodes to 27.0 MB
  soft masks on the drawing path: 0
  transparency groups drawn:      0
  new marks on flat areas: 0.0000%
```

Pass, and you get exit 0 and the file under the name you asked for. Fail, and
the output moves to `NAME.rejected` with exit 1. A rejected file is still there
to look at, but cannot be mistaken for a good one. `--force` keeps the name. The
exit code is still 1.

The soft-mask and group counts follow what page content executes, not what the
file contains. An unused mask dictionary in a resource entry is never
composited, so counting it would report work no viewer does.

## Why not the ghostscript pipeline

This is the first thing people suggest, and it is reasonable. Rasterise the page
without text, extract the text on its own, put one over the other:

```
gs -sDEVICE=pdfimage24 -r300 -dFILTERTEXT -o backdrop.pdf in.pdf
gs -sDEVICE=pdfwrite -dFILTERIMAGE -dFILTERVECTOR -o textonly.pdf in.pdf
qpdf backdrop.pdf --overlay textonly.pdf -- out.pdf
```

It produces a file. On the sample the text is there, it renders, and it extracts.
It does not fix the bug: the text-only layer carries the original graphics state,
soft masks and transparency groups included, and the overlay puts all of it back
on the page.

On real Chrome exports I have also watched the overlay drop text off the page
while `pdftotext` still returns every word, the worst way for a conversion to
fail. The generated sample does not reproduce that, so read the table as the
smallest gap between the two, not the usual one. Check your own file before you
trust either.

Measured on `sample-deck.pdf`, on this machine, with `python3 bench.py`:

| candidate                   | MB    | words | img/pg | masks / groups | marks added | marks lost | exit |
|-----------------------------|-------|-------|--------|----------------|-------------|------------|------|
| skia-pdf-flatten-keep-text       | 1.00  | 397   | 1      | 0 / 0          | 0.0000%     | 0.0001%    | 0    |
| gs backdrop + text overlay  | 2.59  | 397   | 1      | 18 / 25        | 0.0230%     | 0.0000%    | 0    |
| gs -dCompatibilityLevel=1.3 | 10.15 | 0     | 1      | 0 / 0          | 0.0000%     | 0.0001%    | 0    |
| skia-pdf-flatten-to-jpeg         | 1.87  | 0     | 1      | 0 / 0          | 0.0000%     | 0.0001%    | 0    |

Input: 0.07 MB, 3 pages, 397 words.

- **masks / groups** counts the soft masks and transparency groups a viewer
  executes while drawing the pages. This is the number the whole exercise is
  about. The overlay pipeline leaves 18 and 25 of them.
- **words** is `pdftotext out.pdf - | wc -w`. Ghostscript's transparency
  flattening rasterises the text along with the art, so there is nothing left to
  select.
- **marks added** is the share of pixels that appear where the original page was
  flat, and **marks lost** is the same measurement run backwards. Both come from
  the verifier, at 100 dpi grayscale.

The sample grows from 0.07 MB to 1.00 MB because its artwork is pure vector, and
a 300 dpi JPEG of a gradient costs more bytes than the gradient. Exports that
carry photographs go the other way. `--dpi 150` takes the same sample to 0.40 MB
if file size matters more than zoom.

## The companion tool

`skia-pdf-flatten-to-jpeg` rasterises everything, text included, and rebuilds the
document from the images. No fonts, no layer to select. Use it when the page must
render and nobody needs to copy from it, or when `skia-pdf-flatten-keep-text` rejects
the file and you need something today.

```
skia-pdf-flatten-to-jpeg deck.pdf
```

## Reproduce all of it

`make-sample.py` and `bench.py` are dev-only and not part of the installed
package, so run these from a checkout with an editable install:

```
pip install -e .
python3 make-sample.py .                                  # both samples
skia-pdf-flatten-keep-text sample-deck.pdf                      # exits 0
skia-pdf-flatten-keep-text sample-redacted.pdf                  # exits 1, quarantined
python3 bench.py sample-deck.pdf                           # the table above
```

`make-sample.py` writes plain uncompressed PDF with no library, so you can open
either sample in a text editor and read the operators.

## Limits

- One `/XObject` resource dictionary per page, referenced directly. An indirect
  one raises rather than guessing.
- The backdrop is rendered by poppler, so a page poppler renders wrong flattens
  wrong. The verifier compares against poppler too and will not catch that.
- Encrypted files are not handled. The streams do not decode and the output
  comes out wrong, though verification does reject it. The sample encrypted
  with qpdf scores 70.98%. Decrypt first with `qpdf --decrypt`.
- The text pass keeps clipping paths but drops the paint that used them, so a
  page that clips text with a painted shape is worth checking by eye.

## License

MIT. See [LICENSE](LICENSE).
