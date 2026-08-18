# pdf-flatten-keep-text

Chrome print-to-PDF files render black or blank in macOS Preview. This flattens
the artwork to one opaque image per page and replays the text on top as real
text, so the page still selects, searches and copies.

```
python3 pdf-flatten-keep-text deck.pdf
```

## Never run this on a redacted document

The tool moves every text block above every piece of artwork. That is the whole
idea, and it is wrong for any page where artwork was drawn **over** text:

- redaction bars and cover boxes
- stamps and watermarks
- highlight and sticker overlays

On those pages the covered text is replayed above the thing that covered it, and
becomes readable. The text was always in the file, so nothing new leaks into the
bytes, but a page that looked safe now shows what it hid.

The verifier catches this and refuses to hand you the file. It renders both
documents and counts new marks that land in areas the original drew flat. Cover
a line of text, and those marks are the line coming back:

```
$ python3 pdf-flatten-keep-text sample-redacted.pdf
  new marks on flat areas: 0.4603% (page 1)   FAIL: over 0.0200%
  verification FAILED - output quarantined at sample-redacted-hybrid.pdf.rejected
```

Treat that as a stop sign, not a threshold to tune. `--force` exists for the case
where you have looked at the rejected file and know why it differs.

## The bug

A Skia print-to-PDF export builds every page out of transparency groups, soft
masks and shading-filled forms, nested several deep. Nothing on the page is a
plain painted object. A viewer has to composite the whole stack on every repaint,
and macOS Preview gives up: black pages, blank pages, or pages that appear only
after you scroll past them and back.

Re-encoding the embedded images does not help, because the images are not the
problem. The compositing is.

The sample in this repository has the same shape. Three pages, 120 form
xobjects, 42 of them soft-mask groups:

```
$ python3 make-sample.py .
$ python3 pdf-flatten-keep-text sample-deck.pdf
3 pages, 120 form xobjects (3 contain text, 42 are mask groups)
```

## What it does

The tool splits each content stream in two.

**The backdrop.** Everything that is not a text block gets rendered to one
opaque JPEG per page. One image, no alpha, no groups, no masks. A viewer walks a
single sequential scan.

**The text.** Every `BT..ET` block is replayed over that image with its original
font, colour, transform and clipping path intact. Real text, not an OCR guess.
It stays selectable and it stays sharp at any zoom, because it is still text.

Two details carry most of the work.

Gradient headlines are not text. Chrome draws them by filling a rectangle
through a luminosity mask whose group contains the glyphs. The fill belongs to
the backdrop, so the glyphs would leave with it and the headline would stop being
searchable. The tool keeps a copy of those glyphs in text rendering mode 3, which
paints nothing, and draws it where the fill used to be.

Scopes that no longer draw anything get removed. Keeping graphics state also
keeps `q /GsN gs Q` blocks whose only painting was artwork the text pass dropped.
They draw nothing, and they still install a soft mask, which is the exact thing
this tool exists to remove.

## What you need

Python 3.8 or newer, Pillow, and poppler-utils for `pdftoppm`, `pdfinfo`,
`pdftotext` and `pdfimages`.

```
pip install pillow
apt install poppler-utils      # or: brew install poppler
```

No PDF library. `ctok.py` is a small content-stream tokenizer and object-graph
reader, and it is the only import.

## Use

```
pdf-flatten-keep-text IN.pdf [OUT.pdf]     default OUT: IN-hybrid.pdf
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

Pass, and you get exit 0 and the file under the name you asked for. Fail, and the
output moves to `NAME.rejected` and the exit code is 1. A rejected file is still
there to look at, it just cannot be mistaken for a good one. With `--force` the
name survives and the exit code is still 1.

The soft-mask and group counts follow what page content executes, not what the
file contains. An unused mask dictionary sitting in a resource entry is never
composited, so counting it would report work no viewer does.

## Why not the ghostscript pipeline

This is the first thing people suggest, and it is a reasonable idea. Rasterise
the page without text, extract the text on its own, put one over the other:

```
gs -sDEVICE=pdfimage24 -r300 -dFILTERTEXT -o backdrop.pdf in.pdf
gs -sDEVICE=pdfwrite -dFILTERIMAGE -dFILTERVECTOR -o textonly.pdf in.pdf
qpdf backdrop.pdf --overlay textonly.pdf -- out.pdf
```

It produces a file. The text is there, it renders, and it extracts. It just does
not fix the bug, because the text-only layer carries the original graphics state
with it, soft masks and transparency groups included, and the overlay puts all of
that straight back on the page.

Measured on `sample-deck.pdf`, on this machine, with `python3 bench.py`:

| candidate                   | MB    | words | img/pg | masks / groups | marks added | marks lost | exit |
|-----------------------------|-------|-------|--------|----------------|-------------|------------|------|
| pdf-flatten-keep-text       | 1.00  | 397   | 1      | 0 / 0          | 0.0000%     | 0.0001%    | 0    |
| gs backdrop + text overlay  | 2.59  | 397   | 1      | 18 / 25        | 0.0230%     | 0.0000%    | 0    |
| gs -dCompatibilityLevel=1.3 | 10.15 | 0     | 1      | 0 / 0          | 0.0000%     | 0.0001%    | 0    |
| pdf-flatten-to-jpeg         | 1.87  | 0     | 1      | 0 / 0          | 0.0000%     | 0.0001%    | 0    |

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
if the file size matters more than the zoom.

## The companion tool

`pdf-flatten-to-jpeg` rasterises everything, text included, and rebuilds the
document from the images. No fonts, no text layer, nothing to select. Use it when
the page must render and you do not care about the text, or when
`pdf-flatten-keep-text` rejects the file and you need something today.

```
python3 pdf-flatten-to-jpeg deck.pdf
```

## Reproduce all of it

```
python3 make-sample.py .                                  # both samples
python3 pdf-flatten-keep-text sample-deck.pdf             # exits 0
python3 pdf-flatten-keep-text sample-redacted.pdf         # exits 1, quarantined
python3 bench.py sample-deck.pdf                          # the table above
```

`make-sample.py` writes plain uncompressed PDF with no library, so you can open
either sample in a text editor and read the operators.

## Limits

- One `/XObject` resource dictionary per page, referenced directly. An indirect
  one raises rather than guessing.
- The backdrop is rendered by poppler, so a page poppler renders wrong flattens
  wrong. The verifier compares against poppler too and will not catch that.
- Encrypted files are not handled. Decrypt first with `qpdf --decrypt`.
- The text pass keeps clipping paths but drops the paint that used them, so a
  page that clips text with a painted shape is worth checking by eye.

## License

MIT. See [LICENSE](LICENSE).
