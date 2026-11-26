# cuadro-radar

Radar de lluvia, nieve y nubes alrededor de Cerdanyola del Vallès para los cubitos del proyecto
**cuadro** (GeekMagic SmallTV, ESP8266). GitHub Actions lo regenera cada 10 minutos y lo publica en la
rama `data`; los cubitos descargan solo el archivo de su radio y animan las 5 últimas imágenes.

- `generate.py` — descarga el radar y la nubosidad y escribe `radar_<km>.bin` (10, 25, 50, 100, 150, 200 km).
- `tinypng.py` — decodificador PNG en Python puro (sin dependencias).
- `.github/workflows/radar.yml` — ejecución cada 10 min y publicación en la rama `data`.

Archivos publicados: `https://raw.githubusercontent.com/<usuario>/<repo>/data/radar_<km>.bin`

## Formato de `radar_<km>.bin`
`"RN1"` · N (1 byte, 48) · km (2, big endian) · nº de imágenes (1) · flags (1: bit0 radar, bit1 nubes)
y por cada imagen: hora unix (4, big endian) + N×N/2 bytes (casillas de 4 bits, dos por byte).
Casillas: 0 nada · 1-3 nubes · 4-9 lluvia (muy débil → extrema) · 10-12 nieve.

## Fuentes y créditos
- Radar: [RainViewer](https://www.rainviewer.com) (API pública gratuita; requiere citarlo).
- Nubosidad: [Open-Meteo](https://open-meteo.com) (gratuito para uso no comercial).

## Probar en local
```
python generate.py --data data --preview 50
```
