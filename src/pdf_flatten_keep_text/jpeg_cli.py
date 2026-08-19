#!/usr/bin/env python3
"""Rasterise every page of a PDF to a JPEG and rebuild a PDF from those images.

The result is one opaque full-bleed image per page: no soft masks, no
transparency groups, no blend modes, no shadings, no fonts. Every viewer walks
a single sequential JPEG scan per page, which is the cheapest thing a PDF page
can be. Use it when a deck renders black, blank, or only-after-scrolling and
re-encoding the embedded images has not fixed it.

The trade is total: the text layer is gone, so the output is not selectable,
searchable, or copyable, and it no longer scales past the render resolution.
Keep the original as the source of truth and ship this as the viewing copy.

Usage:  pdf-flatten-to-jpeg IN.pdf [OUT.pdf]     (default OUT: IN-flat.pdf)
        --dpi N          render resolution           (default 300)
        --quality N      JPEG quality                (default 90)
        --subsampling N  0 = 4:4:4, 2 = 4:2:0        (default 0)
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

POPPLER_BINARIES = ('pdftoppm', 'pdfinfo', 'pdfimages')


def check_input(path):
    """Exit with a message when the input is absent, a directory, or not a PDF."""
    if not os.path.exists(path):
        sys.exit('no such file: ' + path)
    if os.path.isdir(path):
        sys.exit('input is a directory, not a file: ' + path)
    with open(path, 'rb') as fh:
        if fh.read(5) != b'%PDF-':
            sys.exit('not a PDF (no %PDF- header): ' + path)


def check_poppler():
    """Exit with an install command if a required poppler-utils binary is missing."""
    missing = [b for b in POPPLER_BINARIES if shutil.which(b) is None]
    if missing:
        sys.exit(
            'missing poppler-utils binaries: ' + ', '.join(missing) + '\n'
            'install them first:\n'
            '  apt install poppler-utils      # Debian/Ubuntu\n'
            '  brew install poppler           # macOS'
        )


def page_boxes(path):
    """Per-page (width, height) in PostScript points, in page order."""
    out = subprocess.run(['pdfinfo', '-l', '100000', path],
                         capture_output=True, text=True).stdout
    sizes = {}
    for m in re.finditer(r'Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts', out):
        sizes[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    if not sizes:                       # uniform-size document
        m = re.search(r'Page size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts', out)
        n = int(re.search(r'Pages:\s+(\d+)', out).group(1))
        sizes = {i: (float(m.group(1)), float(m.group(2))) for i in range(1, n + 1)}
    return [sizes[i] for i in sorted(sizes)]


def render(path, dpi, workdir):
    """Rasterise to lossless PNGs so JPEG encoding happens exactly once."""
    subprocess.run(['pdftoppm', '-r', str(dpi), '-png', '-cropbox',
                    path, os.path.join(workdir, 'pg')], check=True)
    return sorted(f for f in os.listdir(workdir) if f.endswith('.png'))


def pdf_escape(s):
    return s.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')


def build(jpegs, boxes, title, dst):
    """Assemble a minimal PDF: one page per JPEG, drawn edge to edge."""
    objs = {}          # number -> bytes (body, without the `N 0 obj` wrapper)
    n_pages = len(jpegs)
    # 1 catalog, 2 pages tree, then 3 objects per page, then info
    page_ids = [3 + i * 3 for i in range(n_pages)]

    objs[1] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objs[2] = (b'<< /Type /Pages /Count ' + str(n_pages).encode() + b' /Kids ['
               + b' '.join(b'%d 0 R' % p for p in page_ids) + b'] >>')

    for i, (jpg, (w_pt, h_pt)) in enumerate(zip(jpegs, boxes)):
        pid = page_ids[i]
        cid, iid = pid + 1, pid + 2
        px_w, px_h = jpg['size']
        objs[pid] = (
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 '
            + f'{w_pt:.4f} {h_pt:.4f}'.encode() + b']'
            b' /Resources << /XObject << /Im0 ' + b'%d 0 R' % iid + b' >> >>'
            b' /Contents ' + b'%d 0 R' % cid + b' >>')
        content = (f'q {w_pt:.4f} 0 0 {h_pt:.4f} 0 0 cm /Im0 Do Q').encode()
        objs[cid] = (b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n'
                     + content + b'\nendstream')
        objs[iid] = (
            b'<< /Type /XObject /Subtype /Image /Width ' + str(px_w).encode()
            + b' /Height ' + str(px_h).encode()
            + b' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode'
              b' /Length ' + str(len(jpg['data'])).encode() + b' >>\nstream\n'
            + jpg['data'] + b'\nendstream')

    info_id = 3 + n_pages * 3
    objs[info_id] = (b'<< /Title (' + pdf_escape(title).encode('latin-1', 'replace')
                     + b') /Producer (pdf-flatten-to-jpeg) >>')

    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += str(num).encode() + b' 0 obj\n' + objs[num] + b'\nendobj\n'

    xref_pos = len(out)
    hi = max(offsets)
    out += b'xref\n0 ' + str(hi + 1).encode() + b'\n0000000000 65535 f \n'
    for i in range(1, hi + 1):
        out += (b'%010d 00000 n \n' % offsets[i]) if i in offsets \
            else b'0000000000 65535 f \n'
    out += (b'trailer\n<< /Size ' + str(hi + 1).encode()
            + b' /Root 1 0 R /Info ' + str(info_id).encode() + b' 0 R >>\n'
            b'startxref\n' + str(xref_pos).encode() + b'\n%%EOF\n')
    open(dst, 'wb').write(bytes(out))
    return len(out)


def flatten(src, dst, dpi, quality, subsampling):
    title = ''
    info = subprocess.run(['pdfinfo', src], capture_output=True, text=True).stdout
    m = re.search(r'^Title:\s+(.*)$', info, re.M)
    if m:
        title = m.group(1).strip()

    boxes = page_boxes(src)
    work = tempfile.mkdtemp(prefix='pdfflat-')
    try:
        pngs = render(src, dpi, work)
        if len(pngs) != len(boxes):
            boxes = boxes[:len(pngs)] or boxes
        jpegs = []
        for i, name in enumerate(pngs):
            im = Image.open(os.path.join(work, name)).convert('RGB')
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=quality, optimize=True,
                    progressive=False, subsampling=subsampling)
            jpegs.append({'data': buf.getvalue(), 'size': im.size})
            print(f'  page {i + 1:>3}: {im.size[0]}x{im.size[1]} px '
                  f'-> {len(jpegs[-1]["data"]) / 1e6:.2f} MB')
        size = build(jpegs, boxes, title, dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    before = os.path.getsize(src)
    print(f'\npages: {len(jpegs)} at {dpi} dpi, JPEG q{quality}, '
          f'{"4:4:4" if subsampling == 0 else "4:2:0"}')
    print(f'file: {before / 1e6:.1f} MB -> {size / 1e6:.1f} MB')
    return dst


def verify(src, dst, dpi):
    """Check the output parses, keeps page count, and holds one image per page."""
    ok = True
    a = subprocess.run(['pdfinfo', src], capture_output=True, text=True).stdout
    b = subprocess.run(['pdfinfo', dst], capture_output=True, text=True)
    if b.returncode != 0:
        print('  FAIL: output does not parse')
        return False
    pa = int(re.search(r'Pages:\s+(\d+)', a).group(1))
    pb = int(re.search(r'Pages:\s+(\d+)', b.stdout).group(1))
    print(f'  pages: {pb}' + ('' if pa == pb else f'  FAIL: was {pa}'))
    ok &= pa == pb

    lst = subprocess.run(['pdfimages', '-list', dst],
                         capture_output=True, text=True).stdout
    rows = [r.split() for r in lst.splitlines()[2:] if r.strip()]
    per_page = {}
    encs, kinds = set(), set()
    for r in rows:
        if len(r) < 14:
            continue
        per_page[int(r[0])] = per_page.get(int(r[0]), 0) + 1
        kinds.add(r[2])
        encs.add(r[9])
    bad = [p for p, c in per_page.items() if c != 1]
    print(f'  images per page: {"1 (flat)" if not bad else f"FAIL on pages {bad}"}')
    print(f'  encoding: {",".join(sorted(encs))}  kinds: {",".join(sorted(kinds))}')
    ok &= not bad and kinds == {'image'}

    raw = open(dst, 'rb').read()
    for marker in (b'/SMask', b'/Transparency', b'/Shading', b'/Group'):
        hits = raw.count(marker)
        print(f'  {marker.decode():<14} {hits}' + ('' if hits == 0 else '  FAIL'))
        ok &= hits == 0
    worst = max((int(r[3]) * int(r[4]) * int(r[6]) for r in rows if len(r) >= 14),
                default=0)
    print(f'  worst page decodes to {worst / 1e6:.1f} MB (flat, no compositing)')
    return ok


def main():
    import argparse
    check_poppler()
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('input')
    ap.add_argument('output', nargs='?')
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--quality', type=int, default=90)
    ap.add_argument('--subsampling', type=int, default=0, choices=(0, 1, 2))
    a = ap.parse_args()
    check_input(a.input)
    out = a.output or re.sub(r'\.pdf$', '', a.input, flags=re.I) + '-flat.pdf'
    flatten(a.input, out, a.dpi, a.quality, a.subsampling)
    print('verifying:')
    sys.exit(0 if verify(a.input, out, a.dpi) else 1)


if __name__ == '__main__':
    main()
