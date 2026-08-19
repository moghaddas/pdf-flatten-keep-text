"""Minimal PDF content-stream tokenizer and object-graph helpers."""
import re, zlib

DELIM = b'()<>[]{}/%'
WS = b'\x00\t\n\x0c\r '


def tokenize(buf):
    """Yield (kind, bytes) where kind is 'op' or 'obj' (an operand)."""
    i, n = 0, len(buf)
    while i < n:
        c = buf[i:i + 1]
        if c in WS:
            i += 1
            continue
        if c == b'%':                                   # comment
            j = i
            while j < n and buf[j:j + 1] not in b'\r\n':
                j += 1
            i = j
            continue
        if c == b'(':                                   # literal string
            j, depth = i + 1, 1
            while j < n and depth:
                ch = buf[j:j + 1]
                if ch == b'\\':
                    j += 2
                    continue
                if ch == b'(':
                    depth += 1
                elif ch == b')':
                    depth -= 1
                j += 1
            yield 'obj', buf[i:j]
            i = j
            continue
        if buf[i:i + 2] == b'<<':
            depth, j = 0, i
            while j < n:
                if buf[j:j + 2] == b'<<':
                    depth += 1; j += 2
                elif buf[j:j + 2] == b'>>':
                    depth -= 1; j += 2
                    if depth == 0:
                        break
                elif buf[j:j + 1] == b'(':
                    k, d2 = j + 1, 1
                    while k < n and d2:
                        ch = buf[k:k + 1]
                        if ch == b'\\':
                            k += 2; continue
                        if ch == b'(':
                            d2 += 1
                        elif ch == b')':
                            d2 -= 1
                        k += 1
                    j = k
                else:
                    j += 1
            yield 'obj', buf[i:j]
            i = j
            continue
        if c == b'<':                                   # hex string
            j = buf.find(b'>', i)
            j = n if j == -1 else j + 1
            yield 'obj', buf[i:j]
            i = j
            continue
        if c == b'[' or c == b']' or c == b'{' or c == b'}':
            yield 'obj', c
            i += 1
            continue
        if c == b'/':                                   # name
            j = i + 1
            while j < n and buf[j:j + 1] not in DELIM and buf[j:j + 1] not in WS:
                j += 1
            yield 'obj', buf[i:j]
            i = j
            continue
        j = i                                           # number or operator
        while j < n and buf[j:j + 1] not in DELIM and buf[j:j + 1] not in WS:
            j += 1
        tok = buf[i:j]
        i = j if j > i else i + 1
        if not tok:
            continue
        if re.fullmatch(rb'[+-]?(\d+\.?\d*|\.\d+)', tok):
            yield 'obj', tok
        else:
            yield 'op', tok


def tokenize_with_inline_images(buf):
    """Like tokenize(), but emits inline images (BI..EI) as one 'inline' token."""
    out = []
    i = 0
    while True:
        bi = buf.find(b'BI', i)
        # only treat BI as inline-image start when it stands alone as an operator
        while bi != -1:
            before = buf[bi - 1:bi]
            after = buf[bi + 2:bi + 3]
            if (not before or before in WS or before in DELIM) and \
               (not after or after in WS or after in DELIM):
                break
            bi = buf.find(b'BI', bi + 2)
        if bi == -1:
            out.extend(tokenize(buf[i:]))
            return out
        out.extend(tokenize(buf[i:bi]))
        ei = buf.find(b'EI', buf.find(b'ID', bi))
        while ei != -1 and buf[ei - 1:ei] not in WS:
            ei = buf.find(b'EI', ei + 2)
        ei = len(buf) if ei == -1 else ei + 2
        out.append(('inline', buf[bi:ei]))
        i = ei


# ------------------------------------------------------------ object graph --

def find_objects(data):
    objs = {}
    for m in re.finditer(rb'(?<![0-9])(\d+)\s+(\d+)\s+obj\b', data):
        end = data.find(b'endobj', m.end())
        if end != -1:
            objs[int(m.group(1))] = (m.start(), m.end(), end + 6)
    return objs


def dict_span(data, pos):
    i = data.find(b'<<', pos)
    if i == -1:
        return None, pos
    depth, j = 0, i
    while j < len(data) - 1:
        two = data[j:j + 2]
        if two == b'<<':
            depth += 1; j += 2
        elif two == b'>>':
            depth -= 1; j += 2
            if depth == 0:
                return data[i:j], j
        elif data[j:j + 1] == b'(':
            k, d2 = j + 1, 1
            while k < len(data) and d2:
                ch = data[k:k + 1]
                if ch == b'\\':
                    k += 2; continue
                if ch == b'(':
                    d2 += 1
                elif ch == b')':
                    d2 -= 1
                k += 1
            j = k
        else:
            j += 1
    return None, pos


class Pdf:
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        self.objs = find_objects(self.data)

    def raw(self, num):
        """(dict_bytes, stream_bytes_or_None) for an object number."""
        if num not in self.objs:
            return None, None
        _, body, end = self.objs[num]
        d, dend = dict_span(self.data, body)
        if d is None:
            return self.data[body:end - 6].strip(), None
        s = self.data.find(b'stream', dend)
        if s == -1 or s > dend + 20:
            return d, None
        p = s + 6
        if self.data[p:p + 2] == b'\r\n':
            p += 2
        elif self.data[p:p + 1] in (b'\n', b'\r'):
            p += 1
        m = re.search(rb'/Length\s+(\d+)(?!\s+\d+\s+R)', d)
        if not m:
            m2 = re.search(rb'/Length\s+(\d+)\s+\d+\s+R', d)
            if m2:
                ln = self.get(int(m2.group(1)))
                n = int(re.search(rb'(\d+)', ln).group(1)) if ln else 0
            else:
                n = 0
        else:
            n = int(m.group(1))
        return d, self.data[p:p + n]

    def get(self, num):
        _, body, end = self.objs.get(num, (0, 0, 0))
        return self.data[body:end - 6].strip() if num in self.objs else None

    def stream_data(self, num):
        d, s = self.raw(num)
        if s is None:
            return None
        if d and b'/FlateDecode' in d:
            try:
                return zlib.decompress(s)
            except zlib.error:
                try:
                    return zlib.decompressobj().decompress(s)
                except zlib.error:
                    return None
        return s

    def pages(self):
        """Page object numbers in document order."""
        root = re.search(rb'/Root\s+(\d+)\s+\d+\s+R', self.data)
        cat = self.get(int(root.group(1)))
        pref = re.search(rb'/Pages\s+(\d+)\s+\d+\s+R', cat)
        order, seen = [], set()

        def walk(num):
            if num in seen:
                return
            seen.add(num)
            body = self.get(num)
            if body is None:
                return
            if b'/Type' in body and re.search(rb'/Type\s*/Page\b', body):
                order.append(num)
                return
            kids = re.search(rb'/Kids\s*\[(.*?)\]', body, re.S)
            if kids:
                for k in re.finditer(rb'(\d+)\s+\d+\s+R', kids.group(1)):
                    walk(int(k.group(1)))

        walk(int(pref.group(1)))
        return order
