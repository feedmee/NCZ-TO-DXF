"""
Tek bir NCZ dosyasini koordinatli DXF'e yazar.

ncz_klasorden_birlesik_dxf.py'deki entity->DXF eslemesinin (POINT_KINDS,
LINE_KINDS, POLYGON_KINDS, sanitize_layer_name, argb_to_rgb) devami ama
asagidaki hatalar duzeltildi:

  1. Arc yon hatasi: normalize_arc_angles() ikinci kez '% 360' uyguluyordu,
     bu da end < start durumuna dusup yayin ters yone (350 derecelik yanlis
     sweep) cizilmesine yol aciyordu ("birkac cizgi atma" sikayetinin
     kaynagi buydu). Duzeltme: sweep bir kez hesaplanir, sadece start
     normalize edilir, end = start + sweep (ezdxf end_angle > 360'i kabul
     eder ve doner ARC dogru yonde cizilir).
  2. text_height parser'dan olcumde 500 m'ye kadar bozuk deger donebiliyor
     (p99 = 30 m) -> makul araliga (0.2-50 m) kirpiliyor.
  3. radius > 10.000 m olan Circle/Arc atlaniyor (kirilmis okuma verisi).
  4. Tam siyah (0,0,0) RGB atanmiyor -- koyu CAD temasinda gorunmez kaliyor;
     bu durumda entity ByLayer/ACI7 birakiliyor.
  5. $INSUNITS=metre, $EXTMIN/$EXTMAX ve modelspace vport yaziliyor ki CAD
     dosyayi actiginda dogrudan veriye baksin.
  6. include_kinds ile TEXT/POINT/BLOCK gibi turler secmeli disi birakilabilir.
  7. KOT (Z) korunuyor. Olcum (69+2 dosya): vertex'lerin %7,8'i gercek kot
     tasiyor (medyan 1022 m -- Konya platosu), %92,1'i 0 (2B veri), %0,05'i
     bozuk (parser bazi offsetlerde 1e34 mertebesinde cop float32 okuyor).
     Bozuk/aralik disi Z sifirlanir, gecerli Z yazilir. Vertex'lerinde farkli
     Z bulunan cizgi/poligonlar POLYLINE (3B) olarak, duz olanlar eskisi gibi
     LWPOLYLINE olarak yazilir -- boylece 2B dosyalarda cikti buyumez.
  8. OZNITELIK (attribute) verisi XDATA olarak yaziliyor. Olcum: parseller
     'label_text' alaninda 59.142, noktalar 'name' alaninda 35.227 parsel
     numarasi (orn. "181/32", "367/1") tasiyor ve bunlarin hicbiri eskiden
     DXF'e gecmiyordu. Artik her entity'ye 'NCZ' appid'li XDATA olarak
     eklenir; CAD tarafinda sorgulanabilir ve merge sirasinda korunur.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf

from ncz_pure_parser import parse_ncz
from .filters import LOCAL_RADIUS_M, TR_BBOX, FilterStats, filter_entities

POINT_KINDS = {"Point", "Symbol", "Block"}
LINE_KINDS = {"Line", "Polyline"}
POLYGON_KINDS = {"Polygon", "Box", "Triangle", "MapSheet", "SmartObject"}
ALL_KINDS = POINT_KINDS | LINE_KINDS | POLYGON_KINDS | {"Text", "Circle", "Arc"}

TEXT_HEIGHT_MIN = 0.2
TEXT_HEIGHT_MAX = 50.0
MAX_RADIUS_M = 10_000.0

# NCZ'de yaricap tasimayan cember icin varsayilan (kullanici istegi).
# Yaricapsiz cemberi atlamak yerine gorunur bir isaret olarak cizeriz.
DEFAULT_CIRCLE_RADIUS_M = 1.0

# Gecerli kot araligi (metre). Turkiye'de en dusuk kara ~ -0 m, en yuksek
# Agri 5137 m; parser'in bozuk okumalari (1e34 mertebesinde) bu araligin
# cok disinda kaldigi icin guvenle ayiklanir.
Z_MIN, Z_MAX = -500.0, 5500.0

# Sifira bu kadar yakin kotlar 2B kabul edilir. Parser bazi vertex'lerde
# 3,19e-27 gibi denormalize cop float'lar okuyor; bunlar araliga girdigi
# icin "kot var" saniliyor ve entity gereksiz yere 3B POLYLINE'a
# donusturuluyordu (olcum: AYRANCI'da 78 sahte 3B poligon). Olcum
# hassasiyeti mikrometrenin altinda anlamsizdir.
Z_EPSILON = 1e-6

# XDATA icin uygulama adi. CAD tarafinda bu appid ile sorgulanir.
XDATA_APPID = "NCZ"

# XDATA'ya tasinacak oznitelik alanlari: (entity anahtari, XDATA etiketi)
XDATA_FIELDS = (
    ("label_text", "label"),
    ("name", "name"),
    ("geometry_kind", "kind"),
    ("layer_code", "layer_code"),
    ("box_width", "box_width"),
    ("box_height", "box_height"),
    ("scale", "scale"),
    ("radius", "radius"),
)


@dataclass
class WriteOptions:
    dxf_version: str = "R2013"
    include_kinds: set = field(default_factory=lambda: set(ALL_KINDS))
    stage1_enabled: bool = True
    stage2_enabled: bool = True
    bbox: tuple = TR_BBOX
    radius: float | None = LOCAL_RADIUS_M
    text_height_range: tuple = (TEXT_HEIGHT_MIN, TEXT_HEIGHT_MAX)
    max_radius: float = MAX_RADIUS_M
    default_circle_radius: float = DEFAULT_CIRCLE_RADIUS_M
    write_elevation: bool = True
    write_xdata: bool = True


@dataclass
class FileResult:
    ncz_path: Path
    out_path: Path | None
    ok: bool
    error: str = ""
    parser_epsg: str = ""
    parser_projection: str = ""
    entity_stats: dict = field(default_factory=dict)  # kind -> written count
    skipped: int = 0
    filter_stats: FilterStats | None = None
    with_z: int = 0  # gercek kot tasiyan entity sayisi
    circle_default_radius: int = 0  # varsayilan yaricapla cizilen cember sayisi


#: DXF satir tabanli bir formattir; bu karakterler yapiyi bozar.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_dxf_text(value, max_len: int = 250) -> str:
    """DXF'e yazilabilir metin dondurur; bozuk okumayi tamamen atar.

    Parser bazi bloklarda metin alani yerine HAM IKILI VERI okuyor (olcum:
    Okçu.NCZ'de 3 adet TEXT entity). Bu diziler NUL ve SATIR SONU icerdigi
    icin satir tabanli DXF yapisini kiriyor ve tek bir bozuk metin TUM
    dosyayi okunamaz hale getiriyordu ("Invalid group code" ile acilmiyordu).

    Gecerli NetCAD etiketleri ("181/32", "H_MENFEZ", Turkce harfler dahil)
    hicbir zaman kontrol karakteri icermez; bu yuzden kontrol karakteri
    goren dizi bozuk kabul edilip bos dondurulur."""
    if not isinstance(value, str) or not value:
        return ""
    if _CONTROL_RE.search(value):
        return ""  # bozuk okuma -- yazma
    return value.strip()[:max_len]


def sanitize_layer_name(name, fallback="KATMANSIZ"):
    text = re.sub(r'[<>/\\":;?*|=`\x00-\x1f]', "_", str(name or "").strip())
    text = text.strip("_") or fallback
    return text[:255]


def argb_to_rgb(color_argb):
    if color_argb is None:
        return None
    r = (color_argb >> 16) & 0xFF
    g = (color_argb >> 8) & 0xFF
    b = color_argb & 0xFF
    return (r, g, b)


def normalize_arc_angles(start, end):
    """NCZ'de aci bazen radyan bazen derece saklaniyor; Jeomatik NCZ Reader
    ile ayni sezgi kullanildi. Duzeltme: sweep bir kez hesaplanir, ikinci
    '% 360' UYGULANMAZ (bu, yayin ters yone cizilmesine sebep oluyordu)."""
    if abs(start) <= (2.0 * math.pi + 0.001) and abs(end) <= (2.0 * math.pi + 0.001):
        start = math.degrees(start)
        end = math.degrees(end)
    start = start % 360.0
    end = end % 360.0
    if math.isclose(start, end, abs_tol=1e-6):
        return None  # sifir sweep -> corrupted/degenerate veri, atla
    if end <= start:
        end += 360.0
    sweep = end - start
    if sweep <= 0 or sweep > 360.0 + 0.01:
        return None
    return start, start + sweep


def ensure_layer(doc, layer_name, known_layers, color=None):
    if layer_name in known_layers:
        return
    known_layers.add(layer_name)
    if layer_name not in doc.layers:
        try:
            attribs = {}
            doc.layers.new(layer_name, dxfattribs=attribs)
        except Exception:
            pass  # AutoCAD icin gecersiz karakter/duplikasyon durumunda sessiz gec


def apply_color(dxf_entity, color_argb):
    rgb = argb_to_rgb(color_argb)
    if rgb is None:
        return
    if rgb == (0, 0, 0):
        # Tam siyah -> koyu temali CAD'de gorunmez olur; ByLayer birak.
        return
    try:
        dxf_entity.rgb = rgb
    except Exception:
        pass


def _clip_text_height(height, rng):
    lo, hi = rng
    if height is None or not math.isfinite(height) or height <= 0:
        return lo
    return max(lo, min(hi, height))


def clean_z(z) -> float:
    """Bozuk/aralik disi kot degerlerini 0'a indirger.

    Parser bazi offsetlerde cop float okuyor (olcumde 1e34 ve -1e38
    mertebesinde degerler goruldu; bunlar 1^34 gibi kucuk sayilar DEGIL,
    bilimsel gosterimde 10 uzeri 34 buyuklugunde bozuk okumalardir).
    Gecerli kotlar Z_MIN..Z_MAX araliginda tutulur."""
    if z is None:
        return 0.0
    try:
        z = float(z)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(z) or not (Z_MIN <= z <= Z_MAX):
        return 0.0
    if abs(z) < Z_EPSILON:
        return 0.0  # denormalize cop okuma; 2B kabul et
    return z


#: Parser'in kot okumasi guvenilmez olan turler.
#: _parse_triangle() ucgenin SADECE ilk kosesinin Z'sini okuyor (B ve C
#: koselerine z_offset vermiyor, onlar 0 kaliyor). Bu da 3B yazildiginda
#: gercekte olmayan dik bir sicrama uretiyor (olcum: KNY dosyasindaki 206
#: ucgenin tamami boyle). Bu turler 2B yazilir.
PARTIAL_Z_KINDS = {"Triangle"}


def entity_points(entity, with_z: bool = True):
    """Entity koordinatlarini (x, y, z) uclulerine cevirir."""
    coords = entity.get("coordinates") or []
    if with_z and entity.get("geometry_kind") not in PARTIAL_Z_KINDS:
        return [(c["x"], c["y"], clean_z(c.get("z"))) for c in coords]
    return [(c["x"], c["y"], 0.0) for c in coords]


def _has_elevation(pts) -> bool:
    return any(p[2] != 0.0 for p in pts)


def _xdata_tags(entity) -> list[tuple[int, str]]:
    """Entity'nin oznitelik alanlarini XDATA string etiketlerine cevirir.

    NetCAD oznitelik tablosunda gorunen parsel numarasi gibi veriler
    parser ciktisinda 'label_text' / 'name' alanlarinda geliyor ve eskiden
    DXF'e hic yazilmiyordu. Bos/sifir alanlar atlanir."""
    tags = []
    for key, tag in XDATA_FIELDS:
        value = entity.get(key)
        if value in (None, "", 0, 0.0):
            continue
        if isinstance(value, str):
            value = sanitize_dxf_text(value, max_len=200)
            if not value:
                continue  # bozuk ikili okuma -- XDATA'ya da yazma
            text = f"{tag}={value}"
        elif isinstance(value, float):
            text = f"{tag}={value:g}"
        else:
            text = f"{tag}={value}"
        tags.append((1000, text[:255]))  # DXF string grup kodu siniri
    return tags


def apply_xdata(dxf_entity, entity, enabled: bool = True):
    if not enabled:
        return
    tags = _xdata_tags(entity)
    if not tags:
        return
    try:
        dxf_entity.set_xdata(XDATA_APPID, tags)
    except Exception:
        pass  # XDATA yazilamazsa geometri yine de kaybolmasin


def _add_path(msp, pts, closed, dxfattribs, options):
    """Cizgi/poligon yazar. Vertex'lerde gercek kot varsa 3B POLYLINE,
    yoksa (verinin %92'si) daha kompakt LWPOLYLINE kullanilir."""
    if options.write_elevation and _has_elevation(pts):
        return msp.add_polyline3d(pts, close=closed, dxfattribs=dxfattribs)
    return msp.add_lwpolyline(
        [(p[0], p[1]) for p in pts], close=closed, dxfattribs=dxfattribs
    )


def add_entity_to_dxf(msp, entity, layer_name, stats, options: WriteOptions):
    kind = entity.get("geometry_kind")
    if kind not in options.include_kinds:
        stats["skipped"] += 1
        return
    pts = entity_points(entity, with_z=options.write_elevation)

    dxfattribs = {"layer": layer_name}

    def finish(dxf_entity, counter):
        apply_color(dxf_entity, entity.get("color_argb"))
        apply_xdata(dxf_entity, entity, options.write_xdata)
        stats[counter] += 1
        if options.write_elevation and _has_elevation(pts):
            stats["with_z"] += 1

    if kind == "Text":
        label = sanitize_dxf_text(entity.get("label_text"))
        if not pts or not label:
            stats["skipped"] += 1
            return
        attribs = dict(dxfattribs)
        attribs["height"] = _clip_text_height(entity.get("text_height"), options.text_height_range)
        attribs["rotation"] = entity.get("rotation_degrees") or 0.0
        e = msp.add_text(label, dxfattribs=attribs)
        e.dxf.insert = pts[0]
        finish(e, "text")
        return

    if kind in POINT_KINDS:
        if not pts:
            stats["skipped"] += 1
            return
        e = msp.add_point(pts[0], dxfattribs=dxfattribs)
        finish(e, "point")
        return

    if kind == "Circle":
        radius = entity.get("radius") or 0.0
        if not pts:
            stats["skipped"] += 1
            return
        if radius > options.max_radius:
            stats["skipped"] += 1  # bozuk okuma
            return
        if radius <= 0:
            # NCZ'de yaricap yok -> atlamak yerine varsayilan ile ciz.
            radius = options.default_circle_radius
            stats["circle_default_radius"] += 1
        e = msp.add_circle(pts[0], radius, dxfattribs=dxfattribs)
        finish(e, "circle")
        return

    if kind == "Arc":
        radius = entity.get("radius") or 0.0
        if not pts or radius <= 0 or radius > options.max_radius:
            stats["skipped"] += 1
            return
        angles = normalize_arc_angles(entity.get("start_angle", 0.0), entity.get("end_angle", 0.0))
        if angles is None:
            stats["skipped"] += 1
            return
        start_deg, end_deg = angles
        e = msp.add_arc(
            center=pts[0],
            radius=radius,
            start_angle=start_deg,
            end_angle=end_deg,
            dxfattribs=dxfattribs,
        )
        finish(e, "arc")
        return

    if kind in LINE_KINDS:
        if len(pts) < 2:
            stats["skipped"] += 1
            return
        e = _add_path(msp, pts, False, dxfattribs, options)
        finish(e, "polyline")
        return

    if kind in POLYGON_KINDS:
        if len(pts) < 3:
            stats["skipped"] += 1
            return
        e = _add_path(msp, pts, True, dxfattribs, options)
        finish(e, "polygon")
        return

    stats["unsupported"] += 1


def _empty_stats():
    return {
        "text": 0, "point": 0, "circle": 0, "arc": 0,
        "polyline": 0, "polygon": 0, "skipped": 0, "unsupported": 0,
        # bilgi sayaclari (entity_stats toplamina dahil edilmez)
        "with_z": 0, "circle_default_radius": 0,
    }


#: entity_stats toplamina girmeyen, yalnizca raporlama amacli sayaclar
INFO_COUNTERS = ("skipped", "unsupported", "with_z", "circle_default_radius")


def _set_extents(doc, bbox):
    """Modelspace VPORT'u veri kutusuna ortalar, boyle CAD dosyayi actiginda
    dogrudan veriye bakar. Not: ezdxf saveas() sirasinda $EXTMIN/$EXTMAX
    header degerlerini her zaman sentinel (1e20/-1e20) degerlere sifirliyor
    (CAD uygulamalarinin zoom-extents ile kendi hesaplamasi beklenir), o
    yuzden header'a yazmak yerine sadece VPORT kullaniliyor -- bu deger
    save/reload sonrasi kaliciligi dogrulanmis tek yontem."""
    if not bbox:
        return
    x0, x1, y0, y1 = bbox
    try:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        height = max(y1 - y0, (x1 - x0) * 0.6, 1.0) * 1.05
        doc.set_modelspace_vport(height=height, center=(cx, cy))
    except Exception:
        pass


def write_file_dxf(ncz_path: str | Path, out_path: str | Path, options: WriteOptions | None = None) -> FileResult:
    """Tek bir NCZ dosyasini filtreleyip koordinatli DXF olarak yazar."""
    ncz_path = Path(ncz_path)
    out_path = Path(out_path)
    options = options or WriteOptions()

    try:
        result = parse_ncz(str(ncz_path))
    except Exception as exc:  # pragma: no cover - parser hatasi
        return FileResult(ncz_path=ncz_path, out_path=None, ok=False, error=str(exc))

    entities = result.get("entities", [])
    kept, fstats = filter_entities(
        entities,
        bbox=options.bbox,
        radius=options.radius,
        stage1_enabled=options.stage1_enabled,
        stage2_enabled=options.stage2_enabled,
    )

    doc = ezdxf.new(options.dxf_version)
    doc.header["$INSUNITS"] = 6  # metre
    if options.write_xdata and XDATA_APPID not in doc.appids:
        doc.appids.add(XDATA_APPID)  # XDATA icin appid kayitli olmali
    msp = doc.modelspace()
    known_layers = set()

    stats = _empty_stats()
    layer_colors = result.get("layer_colors") or []

    for entity in kept:
        raw_layer = sanitize_dxf_text(entity.get("layer_name"), max_len=200)
        layer_name = sanitize_layer_name(raw_layer or f"LAYER_{entity.get('layer_code', 0)}")
        ensure_layer(doc, layer_name, known_layers)
        add_entity_to_dxf(msp, entity, layer_name, stats, options)

    _set_extents(doc, fstats.bbox)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))

    return FileResult(
        ncz_path=ncz_path,
        out_path=out_path,
        ok=True,
        parser_epsg=result.get("epsg", ""),
        parser_projection=result.get("projection_text", ""),
        entity_stats={k: v for k, v in stats.items() if k not in INFO_COUNTERS},
        skipped=stats["skipped"] + stats["unsupported"],
        filter_stats=fstats,
        with_z=stats["with_z"],
        circle_default_radius=stats["circle_default_radius"],
    )
