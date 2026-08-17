"""
NCZ -> Birlesik DXF Donusturucu (QGIS GEREKTIRMEZ)
====================================================

Bir klasordeki tum .ncz dosyalarini dogrudan (QGIS'e ihtiyac duymadan) okur
ve tek bir koordinatli DXF dosyasinda birlestirir.

Onceki QGIS tabanli yontemden farki / iyilestirmeler:
  1) QGIS/PyQGIS gerekmez. Sadece "ezdxf" kutuphanesi gerekli:
         pip install ezdxf
  2) Arc ve Circle entity'leri, QGIS eklentisinin yaptigi gibi facetli
     (kirik cizgili) poligon/polyline'a YAKLASTIRILMIYOR. Bunun yerine ham
     radius/start_angle/end_angle/merkez bilgisinden DOGRUDAN gercek DXF
     ARC / CIRCLE varligi olarak yaziliyor. Bu, onceki "duz cizgi" gorunumu
     sorununu kaynagindan (yaklasiklama adimini tamamen kaldirarak) cozer.
  3) Text entity'leri gercek DXF TEXT olarak yaziliyor (etiket, yukseklik,
     rotasyon korunarak) - QGIS eklentisi bunlari sadece nokta yapiyordu.
  4) Orijinal NetCAD katman adlari ve renkleri (true color / RGB) korunuyor.
  5) Ayni katman adi birden fazla NCZ dosyasinda geciyorsa hepsi ayni DXF
     katmaninda birlesir (PREFIX_LAYER_WITH_FILENAME=True yaparak bunu
     dosya bazinda ayirabilirsiniz).

Parser (ncz_pure_parser.py), Jeomatik NCZ Reader (GPL-2.0-or-later, GitHub:
erdincunal/Jeomatik-NCZ-Reader) projesinden alinmistir; lisans/telif basligi
dosyanin icinde korunmustur. Bu script kendi calismasi icin sadece o
dosyadaki parse_ncz() fonksiyonunu kullanir, QGIS'e bagimliligi yoktur.
"""

import glob
import math
import os
import re
import sys
from pathlib import Path

import ezdxf

# ARSIV NOTU: Bu script legacy/ klasorune tasindi, yerini ncz2dxf.py +
# ncztool/ paketi aldi (koordinat filtresi, dogru arc yonu, GUI/CLI).
# ncz_pure_parser.py bir ust klasorde oldugu icin sys.path'e ekleniyor.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ncz_pure_parser import parse_ncz

# ============================================================
# AYARLAR
# ============================================================
NCZ_KLASORU = r"C:\Users\myilm\Toplulastirma"      # .ncz dosyalarinin oldugu klasor
CIKTI_DXF = r"C:\Users\myilm\Toplulastirma\birlesik.dxf"
DXF_VERSIYON = "R2013"                          # AutoCAD 2013 formati, genis uyumluluk
PREFIX_LAYER_WITH_FILENAME = True              # True: her dosyanin katmanlari ayri kalir
ALT_KLASORLERI_DE_TARA = False                  # True: klasor altindaki alt klasorleri de tara

# ============================================================
# YARDIMCI FONKSIYONLAR
# ============================================================
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
    """NCZ dosyalarinda aci bazen radyan bazen derece olarak saklaniyor.
    Jeomatik NCZ Reader kaynagindaki ayni sezgisel kural kullanildi."""
    if abs(start) <= (2.0 * math.pi + 0.001) and abs(end) <= (2.0 * math.pi + 0.001):
        start = math.degrees(start)
        end = math.degrees(end)
    while end < start:
        end += 360.0
    sweep = end - start
    if sweep <= 0 or sweep > 3600:
        return None
    return start, end % 360.0 if end % 360.0 != 0 else 360.0


def layer_for_entity(entity, source_file_base):
    base = entity.get("layer_name") or f"LAYER_{entity.get('layer_code', 0)}"
    if PREFIX_LAYER_WITH_FILENAME:
        base = f"{source_file_base}_{base}"
    return sanitize_layer_name(base)


def ensure_layer(doc, layer_name, known_layers):
    if layer_name in known_layers:
        return
    known_layers.add(layer_name)
    if layer_name not in doc.layers:
        try:
            doc.layers.new(layer_name)
        except Exception:
            pass  # AutoCAD icin gecersiz karakter/duplikasyon durumunda sessiz gec


def apply_color(dxf_entity, color_argb):
    rgb = argb_to_rgb(color_argb)
    if rgb is not None:
        try:
            dxf_entity.rgb = rgb
        except Exception:
            pass


# ============================================================
# ENTITY -> DXF DONUSUM
# ============================================================
POINT_KINDS = {"Point", "Symbol", "Block"}
LINE_KINDS = {"Line", "Polyline"}
POLYGON_KINDS = {"Polygon", "Box", "Triangle", "MapSheet", "SmartObject"}


def add_entity_to_dxf(msp, entity, layer_name, stats):
    kind = entity.get("geometry_kind")
    coords = entity.get("coordinates") or []
    pts = [(c["x"], c["y"]) for c in coords]

    dxfattribs = {"layer": layer_name}

    if kind == "Text":
        if not pts or not entity.get("label_text"):
            stats["skipped"] += 1
            return
        attribs = dict(dxfattribs)
        attribs["height"] = entity.get("text_height") or 1.0
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
        if not pts or radius <= 0:
            stats["skipped"] += 1
            return
        e = msp.add_circle(pts[0], radius, dxfattribs=dxfattribs)
        apply_color(e, entity.get("color_argb"))
        stats["circle"] += 1
        return

    if kind == "Arc":
        radius = entity.get("radius") or 0.0
        if not pts or radius <= 0:
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


# ============================================================
# ANA AKIS
# ============================================================
def main():
    pattern = "**/*.ncz" if ALT_KLASORLERI_DE_TARA else "*.ncz"
    ncz_files = sorted(glob.glob(os.path.join(NCZ_KLASORU, pattern), recursive=ALT_KLASORLERI_DE_TARA))

    if not ncz_files:
        raise SystemExit(f"'{NCZ_KLASORU}' icinde .ncz dosyasi bulunamadi!")

    print(f"{len(ncz_files)} NCZ dosyasi bulundu.")

    doc = ezdxf.new(DXF_VERSIYON)
    msp = doc.modelspace()
    known_layers = set()

    total_stats = {
        "text": 0, "point": 0, "circle": 0, "arc": 0,
        "polyline": 0, "polygon": 0, "skipped": 0, "unsupported": 0,
    }

    for file_path in ncz_files:
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        try:
            result = parse_ncz(file_path)
        except Exception as exc:
            print(f"HATA: {base_name} ayristirilamadi -> {exc}")
            continue

        entities = result.get("entities", [])
        file_stats = {k: 0 for k in total_stats}

        for entity in entities:
            layer_name = layer_for_entity(entity, base_name)
            ensure_layer(doc, layer_name, known_layers)
            add_entity_to_dxf(msp, entity, layer_name, file_stats)

        for key in total_stats:
            total_stats[key] += file_stats[key]

        unsupported_types = result.get("unsupported_geometry_types") or {}
        extra = f" | desteklenmeyen tur kodu: {unsupported_types}" if unsupported_types else ""
        print(
            f"  {base_name}: {len(entities)} entity "
            f"(nokta {file_stats['point']}, cizgi {file_stats['polyline']}, "
            f"poligon {file_stats['polygon']}, yay {file_stats['arc']}, "
            f"cember {file_stats['circle']}, metin {file_stats['text']}, "
            f"atlanan {file_stats['skipped']}){extra}"
        )

    os.makedirs(os.path.dirname(CIKTI_DXF), exist_ok=True)
    doc.saveas(CIKTI_DXF)

    print("\n--- OZET ---")
    for key, value in total_stats.items():
        print(f"  {key}: {value}")
    print(f"\nBasarili: {CIKTI_DXF}")
    print(f"Toplam katman sayisi: {len(known_layers)}")


if __name__ == "__main__":
    main()
