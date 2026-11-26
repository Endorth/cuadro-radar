"""
Decodificador PNG mínimo en Python puro (solo zlib de la librería estándar).
Soporta 8 bits en gris, gris+alfa, RGB y RGBA, y 1/2/4/8 bits en paleta (con tRNS) y gris.
Suficiente para las imágenes de radar de RainViewer (256×256).
"""

import struct
import zlib

_SIG = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data):
    """Devuelve (ancho, alto, píxeles) con píxeles = lista de filas de tuplas (r, g, b, a)."""
    if data[:8] != _SIG:
        raise ValueError("no es un PNG")
    pos = 8
    idat = bytearray()
    palette, trns = None, None
    width = height = depth = ctype = None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if interlace or (depth != 8 and not (ctype in (0, 3) and depth in (1, 2, 4))):
                raise ValueError("PNG no soportado (profundidad %d, entrelazado %d)" % (depth, interlace))
        elif kind == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break

    ch = _CHANNELS[ctype]
    bits = depth * ch
    stride = (width * bits + 7) // 8
    ch = max(1, bits // 8)  # bytes por píxel para los filtros (1 si hay varios píxeles por byte)
    raw = zlib.decompress(bytes(idat))
    rows = []
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 1:
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                left = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                left = line[x - ch] if x >= ch else 0
                upleft = prev[x - ch] if x >= ch else 0
                line[x] = (line[x] + _paeth(left, prev[x], upleft)) & 0xFF
        prev = line

        if depth < 8:  # desempaquetar varios píxeles por byte
            per, mask = 8 // depth, (1 << depth) - 1
            vals = [(line[x // per] >> (8 - depth * (x % per + 1))) & mask for x in range(width)]
            if ctype == 0:
                k = 255 // mask
                px = [(v * k, v * k, v * k, 255) for v in vals]
            else:
                px = []
                for v in vals:
                    r, g, b = palette[v]
                    a = trns[v] if trns is not None and v < len(trns) else 255
                    px.append((r, g, b, a))
            rows.append(px)
            continue

        if ctype == 6:
            px = [tuple(line[x:x + 4]) for x in range(0, stride, 4)]
        elif ctype == 2:
            px = [tuple(line[x:x + 3]) + (255,) for x in range(0, stride, 3)]
        elif ctype == 0:
            px = [(v, v, v, 255) for v in line]
        elif ctype == 4:
            px = [(line[x], line[x], line[x], line[x + 1]) for x in range(0, stride, 2)]
        else:  # paleta
            px = []
            for v in line:
                r, g, b = palette[v]
                a = trns[v] if trns is not None and v < len(trns) else 255
                px.append((r, g, b, a))
        rows.append(px)
    return width, height, rows
