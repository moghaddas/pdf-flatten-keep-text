#!/usr/bin/env python3
"""Generate the two sample PDFs this repository tests against.

sample-deck.pdf reproduces the shape of a Skia print-to-PDF export: every
visible element sits inside its own transparency group, several groups are
painted through a luminosity soft mask, the backdrops are axial shadings, and
one headline per page is gradient text (a shading rectangle filled through a
mask group whose content is the glyphs). Page text sits on top, last in the
content stream. Nothing is embedded that a viewer can skip, so a repaint has
to composite the whole stack.

sample-redacted.pdf is the counter-example: cover boxes drawn OVER text, the
one layout skia-pdf-flatten-keep-text must never touch.

Written with no PDF library on purpose. The output is a plain uncompressed
PDF 1.4 file, so `strings` and a text editor both work on it.

Usage:  make-sample.py [OUTDIR]        (default: the current directory)
"""
import os
import sys

W, H = 960.0, 540.0
PAGES = 3
CARDS = 12

HEADLINES = ['Quarterly rollup', 'Where the time goes', 'What we ship next']
BODY = [
    ('Pipeline', 'Every stage moved right. The middle is the bottleneck.'),
    ('Latency', 'p95 fell to 180 ms after the read replica landed.'),
    ('Cost', 'Storage doubled. Egress stayed flat.'),
    ('Support', 'Ticket volume per account is down for the fourth month.'),
]
FOOT = 'Draft. Numbers are illustrative and change weekly.'


class Writer:
    """Collects object bodies and emits a PDF with a classic xref table."""

    def __init__(self):
        self.bodies = {}
        self.next = 1

    def reserve(self):
        n = self.next
        self.next += 1
        return n

    def put(self, num, body):
        self.bodies[num] = body if isinstance(body, bytes) else body.encode('latin-1')
        return num

    def add(self, body):
        return self.put(self.reserve(), body)

    def stream(self, dict_head, payload):
        payload = payload.encode('latin-1') if isinstance(payload, str) else payload
        head = dict_head.rstrip()
        assert head.endswith('>>')
        head = head[:-2] + ' /Length %d >>' % len(payload)
        return self.add(head.encode('latin-1') + b'\nstream\n' + payload + b'\nendstream')

    def write(self, root, dst):
        out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
        offsets = {}
        hi = max(self.bodies)
        for num in sorted(self.bodies):
            offsets[num] = len(out)
            out += b'%d 0 obj\n' % num + self.bodies[num] + b'\nendobj\n'
        xref = len(out)
        out += b'xref\n0 %d\n0000000000 65535 f \n' % (hi + 1)
        for i in range(1, hi + 1):
            out += (b'%010d 00000 n \n' % offsets[i]) if i in offsets \
                else b'0000000000 65535 f \n'
        out += b'trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n' \
            % (hi + 1, root, xref)
        open(dst, 'wb').write(bytes(out))
        return len(out)


def esc(s):
    return s.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')


def axial(w, c0, c1, coords, gray=False):
    """An axial shading, the backdrop Skia emits for every gradient fill."""
    space = '/DeviceGray' if gray else '/DeviceRGB'
    fn = ('<< /FunctionType 2 /Domain [0 1] /C0 [%s] /C1 [%s] /N 1 >>'
          % (' '.join('%.3f' % v for v in c0), ' '.join('%.3f' % v for v in c1)))
    return w.add('<< /ShadingType 2 /ColorSpace %s /Coords [%s] /Function %s '
                 '/Extend [true true] >>'
                 % (space, ' '.join('%.2f' % v for v in coords), fn))


def group_form(w, content, resources, bbox=(0, 0, W, H), gray=False):
    """A Form XObject with its own transparency group, one per element."""
    cs = '/DeviceGray' if gray else '/DeviceRGB'
    head = ('<< /Type /XObject /Subtype /Form /FormType 1 /BBox [%s] '
            '/Group << /S /Transparency /CS %s /I false /K false >> '
            '/Resources %s >>'
            % (' '.join('%.2f' % v for v in bbox), cs, resources))
    return w.stream(head, content)


def mask_gs(w, mask_form, alpha=1.0):
    """An ExtGState that paints through a luminosity soft mask."""
    return w.add('<< /Type /ExtGState /BM /Normal /CA %.2f /ca %.2f '
                 '/SMask << /S /Luminosity /G %d 0 R /BC [0] >> >>'
                 % (alpha, alpha, mask_form))


def rect(x, y, w_, h_):
    return '%.2f %.2f %.2f %.2f re' % (x, y, w_, h_)


def build_deck(dst):
    w = Writer()
    catalog = w.reserve()
    pages_num = w.reserve()
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica '
                 '/Encoding /WinAnsiEncoding >>')
    bold = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold '
                 '/Encoding /WinAnsiEncoding >>')

    page_nums = []
    for p in range(PAGES):
        xobjects, extgstates, shadings = {}, {}, {}
        tint = 0.10 + 0.25 * p

        # --- page backdrop: a shading inside its own group, behind a mask ---
        sh_bg = axial(w, (0.06, 0.07, 0.14), (0.55, tint, 0.75), (0, 0, W, H))
        bg = group_form(w, 'q 0 0 %.0f %.0f re W n /Sh0 sh Q' % (W, H),
                        '<< /Shading << /Sh0 %d 0 R >> >>' % sh_bg)
        sh_mask = axial(w, (0.35,), (1.0,), (0, H, 0, 0), gray=True)
        bg_mask = group_form(w, 'q 0 0 %.0f %.0f re W n /Sh0 sh Q' % (W, H),
                             '<< /Shading << /Sh0 %d 0 R >> >>' % sh_mask,
                             gray=True)
        xobjects['Bg'] = bg
        extgstates['GsBg'] = mask_gs(w, bg_mask)

        # --- cards: nested groups, each faded by its own soft mask ---
        for i in range(CARDS):
            col, row = i % 4, i // 4
            x, y = 60 + col * 220, 90 + row * 130
            sh_card = axial(w, (1.0, 1.0, 1.0), (0.75, 0.80, 0.95),
                            (x, y + 100, x, y))
            inner = group_form(
                w, 'q 1 1 1 rg %s f Q q %s W n /Sh0 sh Q'
                   % (rect(x, y, 190, 100), rect(x, y, 190, 100)),
                '<< /Shading << /Sh0 %d 0 R >> >>' % sh_card,
                bbox=(x, y, x + 190, y + 100))
            sh_fade = axial(w, (0.15,), (0.95,), (x, y, x + 190, y), gray=True)
            fade = group_form(
                w, 'q %s W n /Sh0 sh Q' % rect(x, y, 190, 100),
                '<< /Shading << /Sh0 %d 0 R >> >>' % sh_fade,
                bbox=(x, y, x + 190, y + 100), gray=True)
            outer = group_form(
                w, 'q /GsInner gs /Inner Do Q',
                '<< /XObject << /Inner %d 0 R >> /ExtGState << /GsInner %d 0 R >> >>'
                % (inner, mask_gs(w, fade, 0.9)),
                bbox=(x, y, x + 190, y + 100))
            xobjects['Card%d' % i] = outer
            extgstates['GsCard%d' % i] = w.add(
                '<< /Type /ExtGState /BM /Multiply /CA 0.92 /ca 0.92 >>')

        # --- gradient headline: a shading rectangle filled through the glyphs ---
        glyph_mask = group_form(
            w, 'q BT /F1 46 Tf 1 1 1 rg 60 %.0f Td (%s) Tj ET Q'
               % (H - 90, esc(HEADLINES[p % len(HEADLINES)])),
            '<< /Font << /F1 %d 0 R >> >>' % bold, gray=True)
        sh_head = axial(w, (1.0, 0.85, 0.35), (1.0, 0.35, 0.45),
                        (60, 0, 900, 0))
        head_fill = group_form(
            w, 'q 0 %.0f %.0f 70 re W n /Sh0 sh Q' % (H - 100, W),
            '<< /Shading << /Sh0 %d 0 R >> >>' % sh_head)
        xobjects['Head'] = head_fill
        extgstates['GsHead'] = mask_gs(w, glyph_mask)

        # --- page content: art first, real text last ---
        c = ['q /GsBg gs /Bg Do Q']
        for i in range(CARDS):
            c.append('q /GsCard%d gs /Card%d Do Q' % (i, i))
        c.append('q /GsHead gs /Head Do Q')
        c.append('BT /F1 13 Tf 0.10 0.11 0.16 rg')
        for i in range(CARDS):
            col, row = i % 4, i // 4
            x, y = 60 + col * 220, 90 + row * 130
            label, line = BODY[i % len(BODY)]
            c.append('1 0 0 1 %.0f %.0f Tm (%s) Tj' % (x + 14, y + 74, esc(label)))
            c.append('1 0 0 1 %.0f %.0f Tm (%s) Tj'
                     % (x + 14, y + 54, esc(line[:34])))
            c.append('1 0 0 1 %.0f %.0f Tm (%s) Tj'
                     % (x + 14, y + 38, esc(line[34:])))
        c.append('1 0 0 1 60 40 Tm (%s  Page %d) Tj' % (esc(FOOT), p + 1))
        c.append('ET')

        res = ('<< /Font << /F1 %d 0 R /F2 %d 0 R >> /XObject << %s >> '
               '/ExtGState << %s >> >>'
               % (font, bold,
                  ' '.join('/%s %d 0 R' % (k, v) for k, v in xobjects.items()),
                  ' '.join('/%s %d 0 R' % (k, v) for k, v in extgstates.items())))
        content = w.stream('<< >>', '\n'.join(c))
        page = w.add('<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.0f %.0f] '
                     '/Resources %s /Contents %d 0 R >>'
                     % (pages_num, W, H, res, content))
        page_nums.append(page)

    w.put(pages_num, '<< /Type /Pages /Count %d /Kids [%s] >>'
          % (len(page_nums), ' '.join('%d 0 R' % n for n in page_nums)))
    w.put(catalog, '<< /Type /Catalog /Pages %d 0 R >>' % pages_num)
    return w.write(catalog, dst)


def build_redacted(dst):
    """Text with opaque cover boxes painted over it. The layout the tool refuses."""
    w = Writer()
    catalog = w.reserve()
    pages_num = w.reserve()
    font = w.add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica '
                 '/Encoding /WinAnsiEncoding >>')
    secret = ['Account holder: A. Nonymous', 'IBAN: XX00 1111 2222 3333 4444',
              'Balance carried forward: 41,900.00']
    lines = ['Statement of account, second quarter'] + secret + \
            ['Prepared for internal review only.']

    c = ['BT /F1 15 Tf 0 0 0 rg']
    for i, line in enumerate(lines):
        c.append('1 0 0 1 60 %.0f Tm (%s) Tj' % (640 - i * 40, esc(line)))
    c.append('ET')
    # The cover boxes, drawn AFTER the text. Opaque white with a hairline,
    # which is how a person redacts a PDF in a viewer that has a rectangle
    # tool. The text under them is untouched and still extracts.
    c.append('1 1 1 rg 0.6 0.6 0.6 RG 0.5 w')
    for i in range(1, 4):
        c.append('%s B' % rect(52, 630 - i * 40, 420, 26))

    content = w.stream('<< >>', '\n'.join(c))
    page = w.add('<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] '
                 '/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>'
                 % (pages_num, font, content))
    w.put(pages_num, '<< /Type /Pages /Count 1 /Kids [%d 0 R] >>' % page)
    w.put(catalog, '<< /Type /Catalog /Pages %d 0 R >>' % pages_num)
    return w.write(catalog, dst)


if __name__ == '__main__':
    outdir = sys.argv[1] if len(sys.argv) > 1 else '.'
    for name, fn in (('sample-deck.pdf', build_deck),
                     ('sample-redacted.pdf', build_redacted)):
        path = os.path.join(outdir, name)
        print('%s: %d bytes' % (path, fn(path)))
