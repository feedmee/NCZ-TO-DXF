"""
Iki asamali koordinat filtresi.

ncz_pure_parser.parse_ncz() ciktisinin ~%43'u NetCAD blok/sembol *tanim*
geometrisidir: bu geometriler (0,0) civarinda YEREL koordinatlarda durur
(blok/sembol tanimlari, container bloklarin ic taramasi sirasinda gercek
harita geometrisiyle karisir). Bu sablonlar hepsi ayni offsette durmadigi
ve layer_code'a gore de ayrilamadigi icin (ayni layer_code'da hem cop hem
gecerli veri var), tek guvenilir ayrac koordinatin kendisidir.

Olculmus degerler (69 gercek NCZ dosyasi, C:\\Users\\myilm\\Toplulastirma):
    Ham parser ciktisi                : 1.544.580 entity
    Asama 1 (Turkiye TM kutusu)       :   919.694 entity (624.886 atildi)
    Asama 2 (dosya medyani +-100 km)  :   917.643 entity (2.051 atildi)

Yaricap 100 km olarak secildi: dosya medyanindan uzakliklarin dagiliminda
93 km'de gercek veri (buyuk/coklu-alan projeler, orn. Guvercinlik.NCZ ~70 km
capinda) biterken 120 km'de tekrar baslayan kume tamamen "cop" (garbage)
geometri -- daha kucuk bir yaricap (orn. 30 km) bu araliktaki gercek veriyi
de siliyordu (olcum: Guvercinlik.NCZ'de layer_code!=0 olan 15.528 entity'nin
893'u 30 km'de kayboluyordu, 100 km'de hicbiri kaybolmuyor).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Callable, Iterable

# Turkiye'deki her 3 derece / 6 derece TM diliminin (ED50, ITRF/TUREF, GK vb.)
# northing/easting degerlerini kapsayan gevsek kutu.
TR_BBOX = (100_000.0, 1_000_000.0, 3_800_000.0, 4_800_000.0)  # x_min,x_max,y_min,y_max

# Dosya medyaninin etrafinda kac metre disinda kalan entity'lerin atilacagi.
# 100 km, olculen 93->120 km bosluguna dayanarak secildi (yukaridaki modul
# dokumantasyonuna bakiniz). Ornek: DEMIRYURT.NCZ'deki 9 gercek cop entity
# medyandan ~490 km uzakta, kolayca elenir.
LOCAL_RADIUS_M = 100_000.0


@dataclass
class FilterStats:
    """Filtreleme sonrasi istatistikler (rapor ve GUI logu icin)."""

    raw_total: int = 0
    stage1_dropped: int = 0
    stage2_dropped: int = 0
    kept_total: int = 0
    dropped_by_kind: dict = field(default_factory=dict)
    bbox: tuple | None = None  # (x_min, x_max, y_min, y_max) filtre sonrasi
    center: tuple | None = None  # (median_x, median_y) -- bkz. asagidaki not

    # NOT: 'center' bbox orta noktasi DEGIL, koordinatlarin medyanidir.
    # Bircok NCZ dosyasi (orn. Toplulastirma projesindeki komsu koyler)
    # kendi parsellerine ek olarak butun projenin ortak sinir/referans
    # katmanini da iceriyor; bu durumda bbox orta noktasi TUM dosyalarda
    # ayni cikar (ortak katman ekstremum koseleri belirliyor) ve mukerrer
    # dosya tespiti (audit.detect_duplicates) yanlislikla farkli koyleri
    # "ayni yerde" gosterir. Medyan, yogun parsel verisine agirlik verir ve
    # az sayidaki paylasilan sinir noktasindan etkilenmez.

    @property
    def dropped_total(self) -> int:
        return self.stage1_dropped + self.stage2_dropped

    def as_lines(self) -> list[str]:
        lines = [
            f"Ham entity        : {self.raw_total}",
            f"Asama 1 atilan    : {self.stage1_dropped} (Turkiye kutusu disi)",
            f"Asama 2 atilan    : {self.stage2_dropped} (dosya merkezinden uzak)",
            f"Kalan entity      : {self.kept_total}",
        ]
        if self.bbox:
            x0, x1, y0, y1 = self.bbox
            lines.append(
                f"Bbox (filtre sonrasi): X[{x0:.1f}, {x1:.1f}]  Y[{y0:.1f}, {y1:.1f}]"
                f"  ({(x1 - x0) / 1000:.1f} x {(y1 - y0) / 1000:.1f} km)"
            )
        if self.dropped_by_kind:
            parts = ", ".join(f"{k}:{v}" for k, v in sorted(self.dropped_by_kind.items()))
            lines.append(f"Atilan turler     : {parts}")
        return lines


def _entity_in_bbox(entity: dict, bbox: tuple) -> bool:
    x_min, x_max, y_min, y_max = bbox
    coords = entity.get("coordinates") or []
    if not coords:
        return False
    return all(x_min < c["x"] < x_max and y_min < c["y"] < y_max for c in coords)


def _entity_within_radius(entity: dict, cx: float, cy: float, radius: float) -> bool:
    coords = entity["coordinates"]
    return all(abs(c["x"] - cx) <= radius and abs(c["y"] - cy) <= radius for c in coords)


def filter_entities(
    entities: Iterable[dict],
    bbox: tuple = TR_BBOX,
    radius: float = LOCAL_RADIUS_M,
    stage1_enabled: bool = True,
    stage2_enabled: bool = True,
) -> tuple[list[dict], FilterStats]:
    """Cop blok/sembol tanim geometrisini ve uc aykiri degerleri temizler.

    Bir entity'nin tek bir vertex'i bile sinir disindaysa entity tamamen
    atilir -- yarim/bozuk geometri yazmaktansa atmak guvenlidir.

    Returns:
        (kept_entities, stats)
    """
    entities = list(entities)
    stats = FilterStats(raw_total=len(entities))

    # --- Asama 1: Turkiye TM kutusu -----------------------------------
    if stage1_enabled:
        stage1_kept = []
        for e in entities:
            if _entity_in_bbox(e, bbox):
                stage1_kept.append(e)
            else:
                stats.stage1_dropped += 1
                stats.dropped_by_kind[e["geometry_kind"]] = (
                    stats.dropped_by_kind.get(e["geometry_kind"], 0) + 1
                )
    else:
        stage1_kept = entities

    if not stage1_kept:
        stats.kept_total = 0
        return [], stats

    # --- Asama 2: dosya medyanindan uzaklik -----------------------------
    if stage2_enabled:
        xs = [c["x"] for e in stage1_kept for c in e["coordinates"]]
        ys = [c["y"] for e in stage1_kept for c in e["coordinates"]]
        cx = statistics.median(xs)
        cy = statistics.median(ys)
        stage2_kept = []
        for e in stage1_kept:
            if _entity_within_radius(e, cx, cy, radius):
                stage2_kept.append(e)
            else:
                stats.stage2_dropped += 1
                stats.dropped_by_kind[e["geometry_kind"]] = (
                    stats.dropped_by_kind.get(e["geometry_kind"], 0) + 1
                )
    else:
        stage2_kept = stage1_kept

    stats.kept_total = len(stage2_kept)
    if stage2_kept:
        xs = [c["x"] for e in stage2_kept for c in e["coordinates"]]
        ys = [c["y"] for e in stage2_kept for c in e["coordinates"]]
        stats.bbox = (min(xs), max(xs), min(ys), max(ys))
        stats.center = (statistics.median(xs), statistics.median(ys))

    return stage2_kept, stats


def combined_bbox(bboxes: Iterable[tuple]) -> tuple | None:
    """Birden fazla (x_min,x_max,y_min,y_max) kutusunu birlestirir."""
    bboxes = [b for b in bboxes if b]
    if not bboxes:
        return None
    x0 = min(b[0] for b in bboxes)
    x1 = max(b[1] for b in bboxes)
    y0 = min(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    return (x0, x1, y0, y1)
