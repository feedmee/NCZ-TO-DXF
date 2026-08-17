# =============================================================================
#  ncz_to_dxf_kmz.py  –  Netcad NCZ → DXF + KMZ toplu dönüştürücü
#
#  Kullanım:
#    python ncz_to_dxf_kmz.py <klasör>
#    python ncz_to_dxf_kmz.py <klasör> --debug
#
#  Kurulum (bir kez):
#    pip install ezdxf pyproj simplekml
#
#  DXF Katmanları: NCZ_POLYGON (+ HATCH), NCZ_POLYLINE, NCZ_LINE, NCZ_POINT, NCZ_TEXT
#  KMZ: polygon/polyline/line/nokta ayrımıyla WGS84
# =============================================================================

import os, sys, re, struct, math, logging, argparse
from pathlib import Path
from collections import Counter

try:
    import ezdxf
    from ezdxf import colors as dxfc
except ImportError:
    sys.exit("Eksik: pip install ezdxf")
try:
    from pyproj import CRS, Transformer
    from pyproj.exceptions import CRSError
except ImportError:
    sys.exit("Eksik: pip install pyproj")
try:
    import simplekml
except ImportError:
    sys.exit("Eksik: pip install simplekml")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Türkiye koordinat filtresi (ITRF96/TM northing & easting)
Y_MIN, Y_MAX = 3_900_000, 4_700_000
X_MIN, X_MAX =   200_000,   900_000

# Netcad 7.x geometri verisi bu offsetten başlar
GEOM_START = 0xF900

# NCZ stride sabitleri
STRIDE_BBOX   = 16   # BBox çifti: Y(8) + X(8)
STRIDE_VERTEX = 24   # Vertex:     Y(8) + X(8) + flag(8)

# Kapalı polygon toleransı (metre)
CLOSE_TOL = 0.05


# ──────────────────────────────────────────────────────────────────────────────
#  YARDIMCI: text okuyucu
# ──────────────────────────────────────────────────────────────────────────────

def _read_text(data: bytes, start: int, end: int) -> str:
    """BBox sonu ile vertex başı arasındaki strlen+string desenini okur."""
    end = min(end, len(data) - 2)
    for off in range(start, end):
        slen = data[off]
        if 1 <= slen <= 30:
            try:
                s = data[off + 1: off + 1 + slen].decode('cp1254')
                if s.isprintable() and s.strip():
                    return s
            except Exception:
                pass
    return ''


# ──────────────────────────────────────────────────────────────────────────────
#  CRS OKUYUCU
# ──────────────────────────────────────────────────────────────────────────────

def read_crs(data: bytes):
    """
    NCZ TLV bloklarını tarayarak WKT / EPSG CRS bilgisini çıkarır.
    Döner: (CRS, epsg_or_None, wkt_or_None)
    """
    off       = 0
    best_wkt  = None
    best_epsg = None

    while off + 5 < len(data):
        typ = data[off]
        try:
            length = struct.unpack_from('<I', data, off + 1)[0]
        except struct.error:
            break
        if length == 0 or length > 50_000_000:
            off += 1
            continue
        end = off + 5 + length
        if end > len(data):
            off += 1
            continue

        if typ == 0x1C:
            try:
                text = data[off + 5: end].decode('utf-8', errors='replace')
                m = re.search(r'<WKT><!\[CDATA\[(.*?)\]\]></WKT>', text, re.DOTALL)
                if m and 'PROJCS' in m.group(1):
                    best_wkt = m.group(1).strip()
                m2 = re.search(r'<SRS>EPSG:(\d+)</SRS>', text)
                if m2:
                    best_epsg = int(m2.group(1))
                if not best_epsg:
                    m3 = re.search(r'AUTHORITY\[.EPSG.,\s*.(\d+).', text)
                    if m3:
                        best_epsg = int(m3.group(1))
            except Exception:
                pass
        off = end

    crs = None
    if best_wkt:
        try:
            crs = CRS.from_wkt(best_wkt)
        except CRSError:
            pass
    if crs is None and best_epsg:
        try:
            crs = CRS.from_epsg(best_epsg)
        except CRSError:
            pass
    if crs is None:
        # NOT: NCZ icindeki "SRS=7933" gibi degerler EPSG kodu DEGIL,
        # Netcad'in kendi ic kodu -- yukaridaki <SRS>EPSG:...</SRS> deseni
        # gercek dosyalarda pratikte hic eslesmiyor, yani bu varsayilan
        # neredeyse her zaman kullaniliyor. Eskiden 5256 (TUREF/TM36)
        # yaziliydi; Turkiye'nin bu bolgesindeki NCZ'ler ITRF/TUREF TM33
        # kullaniyor (dogrulama: AYRANCI.NCZ merkezi 5256 ile boylam
        # 36.72'ye, 5255 ile dogru olan 33.70'e dusuyor). 5255 = TUREF/TM33.
        log.warning("  CRS bulunamadı → EPSG:5255 (TUREF/TM33) varsayılan")
        best_epsg = 5255
        crs       = CRS.from_epsg(5255)

    return crs, best_epsg, best_wkt


# ──────────────────────────────────────────────────────────────────────────────
#  GEOMETRİ OKUYUCU
# ──────────────────────────────────────────────────────────────────────────────

def read_entities(data: bytes) -> list:
    """
    NCZ binary'sinden geometrik entity'leri çıkarır.

    Netcad NCZ entity yapısı (her entity için):
      1) BBox grubu   : 2 × (Y:f64, X:f64) stride=16 → sınırlayıcı kutu
      2) Text meta    : [null][strlen][text][null][style bytes]  (opsiyonel)
      3) Vertex grubu : N × (Y:f64, X:f64, flag:f64) stride=24 → gerçek geometri

    Entity tipleri (vertex sayısına ve kapalılığına göre):
      POLYGON  → N≥3, kapalı (ilk==son)  → LWPOLYLINE(closed) + HATCH
      POLYLINE → N≥3, açık               → LWPOLYLINE(open)
      LINE     → N=2                     → LINE entity
      POINT    → N=1 veya BBox-only      → POINT entity

    Döner: list of dict
      { type, pts[(y,x)], text, closed }
    """
    # 1) Geçerli koordinat offsetlerini bul (byte-by-byte tarama)
    valid: list[int] = []
    off   = GEOM_START
    lim   = len(data) - 16

    while off <= lim:
        try:
            y = struct.unpack_from('<d', data, off)[0]
        except struct.error:
            break
        if Y_MIN < y < Y_MAX and math.isfinite(y):
            try:
                x = struct.unpack_from('<d', data, off + 8)[0]
                if X_MIN < x < X_MAX and math.isfinite(x):
                    valid.append(off)
            except struct.error:
                pass
        off += 1

    if not valid:
        return []

    # 2) Ardışık offsetleri grupla (gap ≤ 32 → aynı grup)
    groups: list[list[int]] = []
    cur = [valid[0]]
    for i in range(1, len(valid)):
        if valid[i] - valid[i - 1] <= 32:
            cur.append(valid[i])
        else:
            groups.append(cur)
            cur = [valid[i]]
    groups.append(cur)

    # 3) Yardımcılar
    def stride_of(g):
        return (g[1] - g[0]) if len(g) >= 2 else 0

    def coords_of(g):
        return [(struct.unpack_from('<d', data, o)[0],
                 struct.unpack_from('<d', data, o + 8)[0])
                for o in g]

    def classify(verts, closed):
        n = len(verts)
        if n == 1:
            return 'POINT'
        elif n == 2:
            return 'LINE'
        elif closed:
            return 'POLYGON'
        else:
            return 'POLYLINE'

    def is_closed(verts):
        return (len(verts) >= 3 and
                abs(verts[0][0] - verts[-1][0]) < CLOSE_TOL and
                abs(verts[0][1] - verts[-1][1]) < CLOSE_TOL)

    # 4) Entity çıkarma: BBox(stride=16) → Vertex(stride=24) çiftleri
    entities = []
    seen     = set()
    i = 0

    while i < len(groups):
        g  = groups[i]
        gs = stride_of(g)

        # ── BBox grubu (stride=16, 2 nokta) ──────────────────────────────────
        if gs == STRIDE_BBOX and len(g) == 2:
            nxt_i = i + 1

            if nxt_i < len(groups):
                ng  = groups[nxt_i]
                ngs = stride_of(ng)

                # Vertex grubu var → gerçek geometri
                if ngs == STRIDE_VERTEX and len(ng) >= 1:
                    verts  = coords_of(ng)
                    closed = is_closed(verts)
                    etype  = classify(verts, closed)
                    # BBox sonu ile vertex başı arasında text ara
                    text   = _read_text(data, g[-1] + 16, ng[0])
                    key    = (etype, verts[0], verts[-1], len(verts))
                    if key not in seen:
                        seen.add(key)
                        entities.append({
                            'type':   etype,
                            'pts':    verts,
                            'text':   text,
                            'closed': closed,
                        })
                    i += 2
                    continue

                # Sonraki de BBox → Bu BBox tek başına = nokta veya text entity
                elif ngs == STRIDE_BBOX:
                    bbox_pts = coords_of(g)
                    cy = (bbox_pts[0][0] + bbox_pts[1][0]) / 2
                    cx = (bbox_pts[0][1] + bbox_pts[1][1]) / 2
                    text = _read_text(data, g[-1] + 16, g[-1] + 80)
                    key  = ('POINT', round(cy, 1), round(cx, 1))
                    if key not in seen:
                        seen.add(key)
                        entities.append({
                            'type': 'POINT', 'pts': [(cy, cx)],
                            'text': text,    'closed': False,
                        })
                    # i'yi ilerletme; sonraki BBox kendi döngüsünde işlenir

        # ── Yalnız Vertex grubu (öncesinde BBox yoksa) ───────────────────────
        elif gs == STRIDE_VERTEX and len(g) >= 1:
            prev_is_bbox = (i > 0 and
                            stride_of(groups[i - 1]) == STRIDE_BBOX and
                            len(groups[i - 1]) == 2)
            if not prev_is_bbox:
                verts  = coords_of(g)
                closed = is_closed(verts)
                etype  = classify(verts, closed)
                key    = (etype, verts[0], verts[-1], len(verts))
                if key not in seen:
                    seen.add(key)
                    entities.append({
                        'type': etype, 'pts': verts,
                        'text': '',    'closed': closed,
                    })
        i += 1

    return entities


# ──────────────────────────────────────────────────────────────────────────────
#  DXF YAZICI
# ──────────────────────────────────────────────────────────────────────────────

def write_dxf(entities: list, crs, epsg, out_path: str, source_name: str):
    """
    Entity'leri orijinal CRS'de DXF olarak yazar.

    Katmanlar:
      NCZ_POLYGON  (cyan)   – LWPOLYLINE(kapalı) + HATCH(şeffaf dolgu)
      NCZ_POLYLINE (yellow) – LWPOLYLINE(açık)
      NCZ_LINE     (green)  – LINE
      NCZ_POINT    (red)    – POINT
      NCZ_TEXT     (white)  – TEXT (parsel no, label vb.)
    """
    doc = ezdxf.new(dxfversion='R2010')
    doc.header['$INSUNITS'] = 6  # metre

    # PDMODE: nokta görünümü X işareti (35)
    doc.header['$PDMODE'] = 35
    doc.header['$PDSIZE'] = 0  # göreli boyut

    msp = doc.modelspace()

    doc.layers.add('NCZ_POLYGON',  color=4)   # cyan
    doc.layers.add('NCZ_POLYLINE', color=2)   # yellow
    doc.layers.add('NCZ_LINE',     color=3)   # green
    doc.layers.add('NCZ_POINT',    color=1)   # red
    doc.layers.add('NCZ_TEXT',     color=7)   # white
    doc.layers.add('NCZ_INFO',     color=8)   # dark gray

    counts  = Counter()

    # Polygon metin yüksekliği için genel ölçek
    all_y = [p[0] for e in entities for p in e['pts']]
    all_x = [p[1] for e in entities for p in e['pts']]
    if all_y:
        extent_y = max(all_y) - min(all_y) or 1.0
        extent_x = max(all_x) - min(all_x) or 1.0
        global_h  = max(min(extent_y, extent_x) * 0.003, 0.5)
    else:
        global_h = 1.0

    for ent in entities:
        etype  = ent['type']
        pts    = ent['pts']      # [(y_northing, x_easting), ...]
        text   = ent['text']
        closed = ent['closed']

        # DXF koordinat sırası: (easting, northing) = (X, Y)
        dxf_pts = [(p[1], p[0]) for p in pts]

        # ── POLYGON ──────────────────────────────────────────────────────────
        if etype == 'POLYGON':
            # LWPOLYLINE (kapalı sınır)
            msp.add_lwpolyline(
                dxf_pts, close=True,
                dxfattribs={'layer': 'NCZ_POLYGON'}
            )
            # HATCH (şeffaf dolgu)
            hatch = msp.add_hatch(color=4, dxfattribs={'layer': 'NCZ_POLYGON'})
            hatch.set_solid_fill()
            hatch.transparency = 0.70          # %70 şeffaf (property setter)
            hatch.paths.add_polyline_path(dxf_pts, is_closed=True)

            # Parsel no / label metni
            if text:
                cx = sum(p[0] for p in dxf_pts) / len(dxf_pts)
                cy = sum(p[1] for p in dxf_pts) / len(dxf_pts)
                # Polygon boyutuna göre metin yüksekliği
                dy = max(p[1] for p in dxf_pts) - min(p[1] for p in dxf_pts)
                dx = max(p[0] for p in dxf_pts) - min(p[0] for p in dxf_pts)
                h  = max(min(dy, dx) * 0.10, global_h * 0.5, 0.3)
                msp.add_text(
                    text,
                    dxfattribs={
                        'layer':     'NCZ_TEXT',
                        'height':    h,
                        'insert':    (cx, cy),
                        'halign':    1,   # center
                        'valign':    2,   # middle
                        'align_point': (cx, cy),
                    }
                )
            counts['POLYGON'] += 1

        # ── POLYLINE ─────────────────────────────────────────────────────────
        elif etype == 'POLYLINE':
            msp.add_lwpolyline(
                dxf_pts, close=False,
                dxfattribs={'layer': 'NCZ_POLYLINE'}
            )
            counts['POLYLINE'] += 1

        # ── LINE ─────────────────────────────────────────────────────────────
        elif etype == 'LINE':
            msp.add_line(
                dxf_pts[0], dxf_pts[1],
                dxfattribs={'layer': 'NCZ_LINE'}
            )
            counts['LINE'] += 1

        # ── POINT ────────────────────────────────────────────────────────────
        elif etype == 'POINT':
            msp.add_point(
                dxf_pts[0],
                dxfattribs={'layer': 'NCZ_POINT'}
            )
            if text:
                px, py = dxf_pts[0]
                msp.add_text(
                    text,
                    dxfattribs={
                        'layer':  'NCZ_TEXT',
                        'height': global_h,
                        'insert': (px + global_h * 0.6, py),
                    }
                )
            counts['POINT'] += 1

    # Bilgi notu
    if all_y:
        ix = (min(all_x) + max(all_x)) / 2
        iy = min(all_y) - extent_y * 0.04
        msp.add_text(
            (f"Kaynak: {source_name}  |  CRS: {crs.name}  |  "
             f"POLYGON:{counts['POLYGON']}  POLYLINE:{counts['POLYLINE']}  "
             f"LINE:{counts['LINE']}  POINT:{counts['POINT']}"),
            dxfattribs={
                'layer': 'NCZ_INFO',
                'height': max(extent_y * 0.010, 1.0),
                'insert': (ix, iy),
            }
        )

    doc.saveas(out_path)
    log.info(f"  DXF  → {Path(out_path).name}  {dict(counts)}")


# ──────────────────────────────────────────────────────────────────────────────
#  KMZ YAZICI
# ──────────────────────────────────────────────────────────────────────────────

def write_kmz(entities: list, crs, epsg, out_path: str, source_name: str):
    """
    Entity'leri WGS84'e dönüştürüp KMZ olarak yazar.
      POLYGON  → KML Polygon  (şeffaf dolgu)
      POLYLINE → KML LineString (sarı)
      LINE     → KML LineString (yeşil)
      POINT    → KML Point (kırmızı)
    """
    try:
        tf = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    except CRSError as e:
        log.error(f"  KMZ projeksiyon hatası: {e}")
        return

    kml = simplekml.Kml()
    kml.document.name = source_name

    folders = {
        'POLYGON':  kml.newfolder(name='Polygon'),
        'POLYLINE': kml.newfolder(name='Polyline'),
        'LINE':     kml.newfolder(name='Line'),
        'POINT':    kml.newfolder(name='Point'),
    }

    counts = Counter()

    for ent in entities:
        etype  = ent['type']
        pts    = ent['pts']   # [(y_northing, x_easting)]
        text   = ent['text']

        try:
            lons, lats = tf.transform(
                [p[1] for p in pts],   # easting  → lon
                [p[0] for p in pts]    # northing → lat
            )
        except Exception:
            continue

        coords = list(zip(lons, lats))

        if etype == 'POLYGON':
            pg = folders['POLYGON'].newpolygon(name=text or '')
            pg.outerboundaryis = coords
            pg.style.linestyle.color = simplekml.Color.cyan
            pg.style.linestyle.width = 1.5
            pg.style.polystyle.color = simplekml.Color.changealpha(
                '40', simplekml.Color.cyan)   # ~25% opak
            pg.style.polystyle.fill  = 1
            if text:
                pg.description = f'Parsel/Alan: {text}'
            counts['POLYGON'] += 1

        elif etype == 'POLYLINE':
            ls = folders['POLYLINE'].newlinestring(name=text or '')
            ls.coords = coords
            ls.style.linestyle.color = simplekml.Color.yellow
            ls.style.linestyle.width = 1.5
            counts['POLYLINE'] += 1

        elif etype == 'LINE':
            ls = folders['LINE'].newlinestring(name='')
            ls.coords = coords
            ls.style.linestyle.color = simplekml.Color.green
            ls.style.linestyle.width = 1.0
            counts['LINE'] += 1

        elif etype == 'POINT':
            pnt = folders['POINT'].newpoint(name=text or '')
            pnt.coords = [coords[0]]
            pnt.style.iconstyle.scale = 0.7
            pnt.style.iconstyle.color = simplekml.Color.red
            pnt.style.iconstyle.icon.href = (
                'http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png')
            if text:
                pnt.description = text
            counts['POINT'] += 1

    # Metadata noktası
    try:
        all_y = [p[0] for e in entities for p in e['pts']]
        all_x = [p[1] for e in entities for p in e['pts']]
        mlon, mlat = tf.transform(
            (min(all_x) + max(all_x)) / 2,
            (min(all_y) + max(all_y)) / 2
        )
        mp = kml.newpoint(name='Bilgi')
        mp.coords = [(mlon, mlat)]
        mp.visibility = 0
        mp.description = (
            f"Kaynak: {source_name}\n"
            f"CRS   : {crs.name}\n"
            f"POLYGON:{counts['POLYGON']}  "
            f"POLYLINE:{counts['POLYLINE']}  "
            f"LINE:{counts['LINE']}  "
            f"POINT:{counts['POINT']}"
        )
    except Exception:
        pass

    kml.savekmz(out_path)
    log.info(f"  KMZ  → {Path(out_path).name}  {dict(counts)}")


# ──────────────────────────────────────────────────────────────────────────────
#  ANA İŞLEV
# ──────────────────────────────────────────────────────────────────────────────

def process_folder(input_folder: str):
    input_folder = Path(input_folder).resolve()
    if not input_folder.is_dir():
        sys.exit(f"Klasör bulunamadı: {input_folder}")

    out_dir = input_folder / 'dxfkmz'
    out_dir.mkdir(exist_ok=True)
    log.info(f"Çıktı klasörü : {out_dir}")

    ncz_files = sorted(
        list(input_folder.glob('*.ncz')) +
        list(input_folder.glob('*.NCZ'))
    )
    if not ncz_files:
        log.warning("NCZ dosyası bulunamadı!")
        return

    log.info(f"Toplam NCZ    : {len(ncz_files)}")
    ok = fail = 0

    for ncz_path in ncz_files:
        stem = ncz_path.stem
        log.info(f"\n{'─'*58}")
        log.info(f"İşleniyor : {ncz_path.name}")

        try:
            with open(ncz_path, 'rb') as f:
                data = f.read()

            crs, epsg, wkt = read_crs(data)
            log.info(f"  CRS       : {crs.name}")

            entities = read_entities(data)
            if not entities:
                log.warning("  Geometri bulunamadı, atlanıyor.")
                fail += 1
                continue

            c = Counter(e['type'] for e in entities)
            log.info(f"  Entity    : {dict(c)}")

            write_dxf(entities, crs, epsg,
                      str(out_dir / f'{stem}.dxf'), ncz_path.name)
            write_kmz(entities, crs, epsg,
                      str(out_dir / f'{stem}.kmz'), ncz_path.name)
            ok += 1

        except Exception as e:
            log.error(f"  HATA: {e}", exc_info=True)
            fail += 1

    log.info(f"\n{'='*58}")
    log.info(f"Tamamlandı : {ok} başarılı, {fail} hatalı")
    log.info(f"Çıktılar   : {out_dir}")


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Netcad NCZ → DXF + KMZ  (POLYGON/POLYLINE/LINE/POINT + HATCH)')
    parser.add_argument('input_folder', help='NCZ dosyalarının bulunduğu klasör')
    parser.add_argument('--debug', action='store_true', help='Ayrıntılı log')
    args = parser.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    process_folder(args.input_folder)
