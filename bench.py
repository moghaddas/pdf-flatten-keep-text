#!/usr/bin/env python3
"""Measure this tool against the free pipelines people reach for first.

Every candidate takes the same input and is scored the same way, with the same
code the tool uses on itself. Two of the numbers matter most:

  marks added   share of pixels that appear in a flat area of the original.
                Something is on the page that was not. Covered text on show.
  marks lost    the same measurement run backwards. Something the original
                drew is gone. Text that no longer renders scores here.

Both come from flat_area_change() in pdf-flatten-keep-text, at 100 dpi
grayscale, counting per-pixel moves above 96 of 255 inside a 5 pixel
neighbourhood the original renders featureless.

Usage:  bench.py [sample-deck.pdf] [--keep]
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'src')


def load_tool():
    # cli.py imports ctok with a package-relative import, so it must load as
    # part of the pdf_flatten_keep_text package rather than as a bare file.
    sys.path.insert(0, SRC)
    import pdf_flatten_keep_text.cli as mod
    return mod


def run(cmd, **kw):
    env = {**os.environ, 'PYTHONPATH': SRC}
    return subprocess.run(cmd, capture_output=True, text=True, env=env, **kw)


def words(path):
    out = subprocess.run(['pdftotext', path, '-'], capture_output=True).stdout
    return len(out.split())


def images_per_page(path):
    out = run(['pdfimages', '-list', path]).stdout
    rows = [r.split() for r in out.splitlines()[2:] if len(r.split()) >= 14]
    per = {}
    for r in rows:
        per[int(r[0])] = per.get(int(r[0]), 0) + 1
    return max(per.values()) if per else 0


def pages(path):
    m = re.search(r'Pages:\s+(\d+)', run(['pdfinfo', path]).stdout)
    return int(m.group(1)) if m else 0


# ------------------------------------------------------------- candidates --

def build_tool(src, out):
    """This repository's tool. Exit 1 means it quarantined its own output."""
    r = run([sys.executable, '-m', 'pdf_flatten_keep_text.cli', src, out])
    return out if os.path.exists(out) else out + '.rejected', r.returncode


def build_gs_overlay(src, out, work):
    """The pipeline everyone suggests: rasterise, extract text, overlay."""
    backdrop = os.path.join(work, 'backdrop.pdf')
    textonly = os.path.join(work, 'textonly.pdf')
    run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-sDEVICE=pdfimage24', '-r300',
         '-dFILTERTEXT', '-o', backdrop, src])
    run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-sDEVICE=pdfwrite',
         '-dFILTERIMAGE', '-dFILTERVECTOR', '-o', textonly, src])
    run(['qpdf', backdrop, '--overlay', textonly, '--', out])
    return out, 0


def build_gs_flatten(src, out, work):
    """Ghostscript transparency flattening. Rasterises the text with the art."""
    run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-sDEVICE=pdfwrite',
         '-dCompatibilityLevel=1.3', '-o', out, src])
    return out, 0


def build_jpeg(src, out, work):
    """The companion tool. Total rasterisation, no text layer at all."""
    r = run([sys.executable, '-m', 'pdf_flatten_keep_text.jpeg_cli', src, out])
    return out, r.returncode


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    src = args[0] if args else os.path.join(HERE, 'sample-deck.pdf')
    keep = '--keep' in sys.argv
    if not os.path.exists(src):
        raise SystemExit(f'{src} not found. Run make-sample.py first.')

    tool = load_tool()
    work = tempfile.mkdtemp(prefix='pdfbench-')
    print(f'input: {src}  {os.path.getsize(src) / 1e6:.2f} MB, '
          f'{pages(src)} pages, {words(src)} words\n')

    builders = [
        ('pdf-flatten-keep-text', build_tool),
        ('gs backdrop + text overlay', build_gs_overlay),
        ('gs -dCompatibilityLevel=1.3', build_gs_flatten),
        ('pdf-flatten-to-jpeg', build_jpeg),
    ]
    rows = []
    for name, fn in builders:
        out = os.path.join(work, re.sub(r'\W+', '-', name) + '.pdf')
        path, code = (fn(src, out) if fn is build_tool else fn(src, out, work))
        if not os.path.exists(path):
            rows.append((name, 'did not produce a file', '', '', '', '', '', ''))
            continue
        sm, grp = tool.compositing_on_drawing_path(path)
        added, _ = tool.flat_area_change(src, path)
        lost, _ = tool.flat_area_change(path, src)
        rows.append((name,
                     f'{os.path.getsize(path) / 1e6:.2f}',
                     str(words(path)),
                     str(images_per_page(path)),
                     f'{sm} / {grp}',
                     f'{added * 100:.4f}%',
                     f'{lost * 100:.4f}%',
                     str(code)))

    head = ('candidate', 'MB', 'words', 'img/pg', 'masks / groups',
            'marks added', 'marks lost', 'exit')
    width = [max(len(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    line = lambda r: '| ' + ' | '.join(str(v).ljust(width[i])
                                       for i, v in enumerate(r)) + ' |'
    print(line(head))
    print('|' + '|'.join('-' * (w + 2) for w in width) + '|')
    for r in rows:
        print(line(r))

    if keep:
        print(f'\noutputs kept in {work}')
    else:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
