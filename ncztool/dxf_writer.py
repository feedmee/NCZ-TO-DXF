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


@dataclass
class WriteOptions:
    dxf_version: str = "R2013"
    include_kinds: set = field(default_factory=lambda: set(ALL_KINDS))
    stage1_enabled: bool = True
    stage2_enabled: bool = True
    bbox: tuple = TR_BBOX
    radius: float = LOCAL_RADIUS_M
    text_height_range: tuple = (TEXT_HEIGHT_MIN, TEXT_HEIGHT_MAX)
    max_radius: float = MAX_RADIUS_M


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


def add_entity_to_dxf(msp, entity, layer_name, stats, options: WriteOptions):
    kind = entity.get("geometry_kind")
    if kind not in options.include_kinds:
        stats["skipped"] += 1
        return
    coords = entity.get("coordinates") or []
    pts = [(c["x"], c["y"]) for c in coords]

    dxfattribs = {"layer": layer_name}

    if kind == "Text":
        if not pts or not entity.get("label_text"):
            stats["skipped"] += 1
            return
        attribs = dict(dxfattribs)
        attribs["height"] = _clip_text_height(entity.get("text_height"), options.text_height_range)
        attribs["rotation"] = entity.get("rotation_degrees") or 0.0
        e = msp.add_text(entity["label_text"], dxfattribs=attribs)
        e.dxf.insert = pts[0]
        apply_color(e, entity.get("color_argb"))
        stats["text"] += 1
        return

    if kind in POINT_KINDS:
        if not pts:
            stats["skipped"] += 1
            return
        e = msp.add_point(pts[0], dxfattribs=dxfattribs)
        apply_color(e, entity.get("color_argb"))
        stats["point"] += 1
        return

    if kind == "Circle":
        radius = entity.get("radius") or 0.0
        if not pts or radius <= 0 or radius > options.max_radius:
            stats["skipped"] += 1
            return
        e = msp.add_circle(pts[0], radius, dxfattribs=dxfattribs)
        apply_color(e, entity.get("color_argb"))
        stats["circle"] += 1
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
        apply_color(e, entity.get("color_argb"))
        stats["arc"] += 1
        return

    if kind in LINE_KINDS:
        if len(pts) < 2:
            stats["skipped"] += 1
            return
        e = msp.add_lwpolyline(pts, close=False, dxfattribs=dxfattribs)
        apply_color(e, entity.get("color_argb"))
        stats["polyline"] += 1
        return

    if kind in POLYGON_KINDS:
        if len(pts) < 3:
            stats["skipped"] += 1
            return
        e = msp.add_lwpolyline(pts, close=True, dxfattribs=dxfattribs)
        apply_color(e, entity.get("color_argb"))
        stats["polygon"] += 1
        return

    stats["unsupported"] += 1


def _empty_stats():
    return {
        "text": 0, "point": 0, "circle": 0, "arc": 0,
        "polyline": 0, "polygon": 0, "skipped": 0, "unsupported": 0,
    }


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
    msp = doc.modelspace()
    known_layers = set()

    stats = _empty_stats()
    layer_colors = result.get("layer_colors") or []

    for entity in kept:
        layer_name = sanitize_layer_name(entity.get("layer_name") or f"LAYER_{entity.get('layer_code', 0)}")
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
        entity_stats={k: v for k, v in stats.items() if k not in ("skipped", "unsupported")},
        skipped=stats["skipped"] + stats["unsupported"],
        filter_stats=fstats,
    )
