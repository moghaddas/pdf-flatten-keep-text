# Why your Chrome PDF renders black in Preview

A deck exported from Chrome opens black in macOS Preview. Same file, same page,
fine in Chrome, fine in Acrobat, black in Preview. The usual advice is to
re-encode the images. That does nothing, because the images were never the
problem.

## What is actually in the file

Open the page content stream of a Skia print-to-PDF export and there are almost
no painted objects. Everything is a form xobject with its own transparency
group, most of them drawn through a luminosity soft mask, and the masks are
themselves groups that draw axial shadings. Three pages of a small sample come
out as 120 form xobjects, 42 of them mask groups.

Every one of those has to be composited into an offscreen buffer and blended
back, in order, on every repaint. Chrome wrote it, so Chrome is happy to draw
it. Preview walks the same tree and runs out of patience. You get a black page,
a blank page, or a page that shows up only after you scroll away and back.

So the fix is not to shrink the file. The fix is to delete the compositing.

## Rasterise the art, keep the text

The trick is that a slide deck has exactly two kinds of content, and they need
opposite treatment.

The artwork is where all the compositing lives, and nobody selects a gradient.
Render it. One opaque JPEG per page, no alpha, no groups, no masks. A viewer
walks a single sequential scan, which is the cheapest thing a PDF page can be.

The text is the part people select, search and copy, and it composites nothing.
Keep it as text.

So split the content stream. Filter one copy to drop every `BT..ET` block and
render that to the backdrop. Filter a second copy to keep only the text blocks
along with the graphics state, transforms and clipping paths they depend on.
Then write a page that draws the JPEG and replays the text over it.

```
q 960 0 0 540 0 0 cm /ZgBackdrop Do Q
BT /F1 13 Tf 0.1 0.11 0.16 rg 1 0 0 1 74 164 Tm (Pipeline) Tj ... ET
```

That is the whole idea. One image per page, zero transparency groups, and the
glyphs are still glyphs, so they stay sharp at 800% zoom.

## The two things that make it hard

**Gradient headlines are not text.** Chrome renders them by filling a rectangle
through a soft mask whose group contains the glyphs. Strip text from that group
and the mask goes empty, so the headline vanishes. Leave the group alone and the
headline is safe, but it is artwork now, so it belongs to the backdrop, and its
glyphs leave the file with the fill. Search for your own slide title and get
nothing.

The fix is to keep a copy of those glyphs in text rendering mode 3, which paints
nothing at all, and draw it where the fill used to be. The headline is a picture
you can still search.

**Scopes that draw nothing still cost you.** The text pass keeps graphics state
so the surviving glyphs paint the way they did. It therefore also keeps blocks
like this, whose only painting was artwork the pass dropped:

```
q
/GsHead gs
Q
```

That draws nothing. `Q` restores everything `gs` set. And it still installs a
soft mask, so a viewer builds the mask group anyway, for a shape that is never
painted. I only found it because the tool failed its own check: six soft masks
left on the drawing path of a file that should have had none.

## Why not the pipeline everyone suggests

Ghostscript can do this in three commands and it costs nothing:

```
gs -sDEVICE=pdfimage24 -r300 -dFILTERTEXT -o backdrop.pdf in.pdf
gs -sDEVICE=pdfwrite -dFILTERIMAGE -dFILTERVECTOR -o textonly.pdf in.pdf
qpdf backdrop.pdf --overlay textonly.pdf -- out.pdf
```

It works better than I expected. The text renders, and `pdftotext` gets all 397
words out of the sample, the same as the purpose-built tool. If you only wanted
a text layer over a picture, that is a fine way to get one.

It just does not fix the bug. The text-only layer keeps the original graphics
state, soft masks and transparency groups included, and the overlay puts every
bit of it back on the page:

| candidate                   | MB    | words | masks / groups |
|-----------------------------|-------|-------|----------------|
| pdf-flatten-keep-text       | 1.00  | 397   | 0 / 0          |
| gs backdrop + text overlay  | 2.59  | 397   | 18 / 25        |
| gs -dCompatibilityLevel=1.3 | 10.15 | 0     | 0 / 0          |

The third row is Ghostscript's transparency flattening, which does remove the
compositing. It removes the text with it, and the file gets ten times bigger.

The masks and groups column counts what a viewer executes while drawing the
page, not what the file contains. An orphan mask dictionary in a resource entry
is never composited, so counting it would report work nobody does.

## Where it must refuse

The tool assumes text is the topmost layer. That is true of slide decks. It is
false the moment artwork was drawn over text: a redaction bar, a cover box, a
stamp, a watermark. On those pages the covered text is replayed above the thing
that covered it, and a page that looked safe starts showing what it hid.

Being right about that in the README is not enough. Somebody will run it on a
statement with white boxes over the numbers, see a file appear, and send it.

So the check has to be a real one. Render both documents, then count changed
pixels whose neighbourhood in the original is featureless. Rasterising and
re-encoding a page moves almost every pixel a little and edge pixels a lot, so a
plain image diff cannot tell a good flatten from a broken one. But re-encoding
noise sits on edges. Text that used to be covered lands in the middle of an area
the original drew flat.

A good page scores zero on that. The sample with cover boxes scores 0.46%, and
every real deck I have run scores zero, so the bar sits a long way from both.

When it trips, the output moves to `NAME.rejected` and the exit code is 1. Not a
warning on stderr. People act on the file, and a file named like a success gets
sent like one.

---

`pdf-flatten-keep-text`, MIT, at
[github.com/moghaddas/pdf-flatten-keep-text](https://github.com/moghaddas/pdf-flatten-keep-text).
The repository generates its own samples, including the one it refuses.
