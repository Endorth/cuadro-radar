"""
Generador del radar de lluvia para los cubitos del cuadro (se ejecuta en GitHub Actions cada 10 min).

Para cada radio (10, 25, 50, 100, 150, 200 km) escribe  <data>/radar_<km>.bin  con las 5 últimas
imágenes de radar alrededor de CENTER, listas para que el cubito las anime como un GIF:

    "RN1" | N (1 byte) | km (2, big endian) | n_imágenes (1) | flags (1)
    y por cada imagen:  hora unix (4, big endian) | N*N/2 bytes (casillas de 4 bits, 2 por byte)

Casillas: 0 nada · 1-3 nubes · 4-9 lluvia (muy débil → extrema) · 10-12 nieve.
flags: bit0 = hay radar, bit1 = hay nubes.

  - Lluvia y nieve: radar real de RainViewer (gratis, sin clave). El nivel gratuito solo da
    zoom 7 (~0,9 km/píxel aquí), imágenes pasadas cada 10 min y una escala de color fija.
  - Nubes: cloud_cover de Open-Meteo en 5×5 puntos por radio, renovado cada hora
    (6 radios × 25 puntos × 24 h = 3.600 consultas/día, por debajo del límite gratuito de 10.000).

Para no repetir trabajo, guarda en <data>/cache/ cada imagen ya procesada y las nubes;
el workflow conserva esa carpeta entre ejecuciones (rama "data").

Uso:
    python generate.py --data data                 # lo que hace el workflow
    python generate.py --data data --preview 50    # además dibuja en texto el radio de 50 km
"""

import argparse
import glob
import json
import math
import os
import struct
import sys
import time
import urllib.request

import tinypng

CENTER = (41.4911, 2.1408)       # Cerdanyola del Vallès
RADII = (10, 25, 50, 100, 150, 200)
FRAMES = 5
N = 48                           # casillas por lado (igual que RAIN_N en el firmware)
ZOOM = 7
CLOUD_POINTS = 5
CLOUD_EVERY = 3600               # s
MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"
METEO_URL = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "cuadro-radar/1.0 (+github actions)"}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def get(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


# ───────────────────────── geometría ─────────────────────────

def offset(dy_km, dx_km):
    """Punto desplazado dy km al norte y dx km al este del centro."""
    lat0, lon0 = CENTER
    return lat0 + dy_km / 111.32, lon0 + dx_km / (111.32 * math.cos(math.radians(lat0)))


def merc_px(lat, lon, z=ZOOM):
    n = 256 * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(max(-85.0, min(85.0, lat))))) / math.pi) / 2.0 * n
    return x, y


# ───────────────────────── RainViewer ─────────────────────────

def classify(px):
    """Color de RainViewer (r, g, b, a) -> categoría 0 ó 4-12."""
    r, g, b, a = px
    if a == 0:
        return 0
    if g >= 250 and b >= 250 and r < 230:          # nieve: cian-blanco
        return 10 if a < 90 else (11 if a < 170 else 12)
    if a < 255:                                     # beige semitransparente: muy débil
        return 4 if a >= 40 else 0
    if b > r and b >= g:                            # azules: débil / moderada
        return 5 if g >= 180 else 6
    if r >= 180 and b >= 150:                       # magenta: extrema
        return 9
    if r >= 180 and g >= 150:                       # amarillo: fuerte
        return 7
    if r >= 180:                                    # rojo: muy fuerte
        return 8
    return 6


class Frame:
    """Una imagen de radar de RainViewer: descarga bajo demanda las teselas que hagan falta."""

    def __init__(self, host, path, t):
        self.host, self.path, self.time = host, path, t
        self.tiles = {}

    def tile(self, tx, ty):
        if (tx, ty) not in self.tiles:
            url = "%s%s/256/%d/%d/%d/2/1_1.png" % (self.host, self.path, ZOOM, tx, ty)
            self.tiles[(tx, ty)] = tinypng.decode(get(url))[2]
        return self.tiles[(tx, ty)]

    def cat(self, lat, lon):
        x, y = merc_px(lat, lon)
        rows = self.tile(int(x // 256), int(y // 256))
        return classify(rows[int(y) % 256][int(x) % 256])

    def rain_cells(self, km):
        """Lista de N*N categorías de lluvia/nieve (0 donde no hay)."""
        cell_km = 2.0 * km / N
        k = max(1, min(4, int(round(cell_km / 0.9))))   # varias muestras por casilla si es grande
        cats = []
        for y in range(N):
            for x in range(N):
                best = 0
                for sy in range(k):
                    for sx in range(k):
                        dy = km - (y + (sy + 0.5) / k) * cell_km
                        dx = -km + (x + (sx + 0.5) / k) * cell_km
                        best = max(best, self.cat(*offset(dy, dx)))
                cats.append(best)
        return cats


# ───────────────────────── Open-Meteo ─────────────────────────

def fetch_clouds(km):
    """Rejilla CLOUD_POINTS×CLOUD_POINTS de nubosidad (%), de norte a sur y de oeste a este."""
    lats, lons = [], []
    m = CLOUD_POINTS - 1
    for j in range(CLOUD_POINTS):
        for i in range(CLOUD_POINTS):
            lat, lon = offset(km * (1 - 2 * j / m), km * (2 * i / m - 1))
            lats.append("%.4f" % lat)
            lons.append("%.4f" % lon)
    url = "%s?latitude=%s&longitude=%s&current=cloud_cover" % (METEO_URL, ",".join(lats), ",".join(lons))
    data = json.loads(get(url))
    if isinstance(data, dict):
        data = [data]
    v = [float(d["current"]["cloud_cover"]) for d in data]
    return [v[j * CLOUD_POINTS:(j + 1) * CLOUD_POINTS] for j in range(CLOUD_POINTS)]


def cloud_cat(grid, fy, fx):
    """Nubosidad interpolada (bilineal) en la posición relativa fy, fx (0..1) -> categoría 0-3."""
    m = CLOUD_POINTS - 1
    gy, gx = fy * m, fx * m
    j0, i0 = min(int(gy), m - 1), min(int(gx), m - 1)
    ty, tx = gy - j0, gx - i0
    a = grid[j0][i0] * (1 - tx) + grid[j0][i0 + 1] * tx
    b = grid[j0 + 1][i0] * (1 - tx) + grid[j0 + 1][i0 + 1] * tx
    cc = a * (1 - ty) + b * ty
    return 0 if cc < 30 else (1 if cc < 60 else (2 if cc < 85 else 3))


# ───────────────────────── caché ─────────────────────────

def pack(cats):
    return bytes((cats[i] << 4) | cats[i + 1] for i in range(0, len(cats), 2))


def unpack(data):
    out = []
    for b in data:
        out += [b >> 4, b & 0x0F]
    return out


def load_clouds(cache, km):
    path = os.path.join(cache, "clouds_%d.json" % km)
    old = None
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)
        if time.time() - old["t"] < CLOUD_EVERY:
            return old["grid"]
    try:
        grid = fetch_clouds(km)
        with open(path, "w") as f:
            json.dump({"t": int(time.time()), "grid": grid}, f)
        log("Nubes %d km: renovadas" % km)
        return grid
    except Exception as e:
        log("Nubes %d km: error (%s), uso las anteriores" % (km, e))
        return old["grid"] if old else None


def rain_for(cache, frame, km):
    path = os.path.join(cache, "rain_%d_%d.bin" % (frame.time, km))
    if os.path.exists(path):
        with open(path, "rb") as f:
            return unpack(f.read())
    cats = frame.rain_cells(km)
    with open(path, "wb") as f:
        f.write(pack(cats))
    return cats


# ───────────────────────── principal ─────────────────────────

def ascii_map(cats):
    chars = " .:-~=+*#%@&AB?"
    return "\n".join("".join(chars[min(c, len(chars) - 1)] for c in cats[y * N:(y + 1) * N]) for y in range(N))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="carpeta de salida (y de caché en data/cache)")
    ap.add_argument("--preview", type=int, help="dibujar en texto la última imagen de este radio")
    args = ap.parse_args()
    cache = os.path.join(args.data, "cache")
    os.makedirs(cache, exist_ok=True)

    try:
        maps = json.loads(get(MAPS_URL))
        past = maps["radar"]["past"][-FRAMES:]
        frames = [Frame(maps["host"], p["path"], int(p["time"])) for p in past]
    except Exception as e:
        log("RainViewer no responde (%s): no cambio nada" % e)
        return 1
    log("Imágenes: " + ", ".join(time.strftime("%H:%M", time.gmtime(f.time)) + "Z" for f in frames))

    for km in RADII:
        clouds = load_clouds(cache, km)
        blobs, radar_ok = [], True
        for fr in frames:
            try:
                cats = rain_for(cache, fr, km)
            except Exception as e:
                log("Radar %d km, imagen %d: error (%s)" % (km, fr.time, e))
                cats, radar_ok = [0] * (N * N), False
            if clouds:
                cats = [c if c else cloud_cat(clouds, (i // N + 0.5) / N, (i % N + 0.5) / N)
                        for i, c in enumerate(cats)]
            blobs.append(struct.pack(">I", fr.time) + pack(cats))
            if args.preview == km and fr is frames[-1]:
                print(ascii_map(cats))
        flags = (1 if radar_ok else 0) | (2 if clouds else 0)
        out = b"RN1" + struct.pack(">BHBB", N, km, len(blobs), flags) + b"".join(blobs)
        tmp = os.path.join(args.data, "radar_%d.bin.tmp" % km)
        with open(tmp, "wb") as f:
            f.write(out)
        os.replace(tmp, os.path.join(args.data, "radar_%d.bin" % km))
        log("radar_%d.bin: %d imágenes, %d bytes, flags=%d" % (km, len(blobs), len(out), flags))

    # borrar de la caché las imágenes que ya no se usan
    keep = {fr.time for fr in frames}
    for p in glob.glob(os.path.join(cache, "rain_*.bin")):
        if int(os.path.basename(p).split("_")[1]) not in keep:
            os.remove(p)
    with open(os.path.join(args.data, "index.json"), "w") as f:
        json.dump({"generated": int(time.time()), "frames": [fr.time for fr in frames],
                   "radii": list(RADII), "center": CENTER}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
