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
    Asama 2 (uyarlanir bosluk kesimi) :   917.643 entity (2.051 atildi)

ASAMA 2 NEDEN SABIT YARICAP DEGIL
---------------------------------
Ilk surumde asama 2 sabit bir yaricap (medyan +- N km) kullaniyordu. Bu model
yanlisti: NCZ dosyalarinin gercek kapsami veri turune gore buyuk olcude
degisiyor ve tek bir N butun dosyalar icin dogru olamiyor:

    Menfezler2.NCZ                    ~   8.7 km  (tek mevki)
    DEMIRYURT.NCZ                     ~   3.8 km  (koy kadastrosu)
    Guvercinlik.NCZ                   ~ 160.1 km  (coklu-alan toplulastirma)
    KNY_FVZPSA_HDT_33_OND.NCZ         ~  90.2 km  (il geneli karayolu menfezleri)

Yaricap veriden kucuk secilirse gercek veri sessizce siliniyor -- olcum:
KNY_FVZPSA_HDT_33_OND.NCZ'nin 10.900 entity'sinin TAMAMI atilip BOS bir DXF
uretilmisti (en yakin entity bile medyandan 12,3 km uzakta oldugu icin).
Bos cikti, kullanicinin fark etmesi en zor hata turu.

Gercek ayrac yaricap degil, uzaklik dagilimindaki KOPUKLUK: gercek veri
sureklidir, cop geometri ise uzakta ayri bir kume olusturur. Olculen en
buyuk goreli sicrama:

    KNY (il geneli, hepsi gercek)     14,46 ->  15,15 km  (  1,0x)  -> kesme YOK
    Menfezler2 (hepsi gercek)          5,17 ->   7,32 km  (  1,4x)  -> kesme YOK
    Guvercinlik (415 cop)            160,07 -> 368,78 km  (  2,3x)  -> kesilir
    DEMIRYURT (9 cop)                  3,81 -> 492,58 km  (129,2x)  -> kesilir

Bu yuzden asama 2 artik uyarlanir: en buyuk mutlak bosluk aranir ve sadece
bosluk hem mutlak (>= GAP_MIN_ABS_M) hem goreli (>= GAP_MIN_RATIO kat) olarak
belirginse kesim yapilir. Boylece dosyanin kendi olcegi ne olursa olsun
gercek veri korunur, uzaktaki cop kume atilir.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Callable, Iterable

# Turkiye'deki her 3 derece / 6 derece TM diliminin (ED50, ITRF/TUREF, GK vb.)
# northing/easting degerlerini kapsayan gevsek kutu.
TR_BBOX = (100_000.0, 1_000_000.0, 3_800_000.0, 4_800_000.0)  # x_min,x_max,y_min,y_max

# Asama 2 uyarlanir kesim esikleri (bkz. modul dokumantasyonu).
# Bir bosluk ancak SU IKI KOSULU BIRDEN saglarsa "cop kumesi baslangici"
# sayilir; boylece surekli gercek veri (KNY 1,0x / Menfezler2 1,4x) hic
# kesilmez, kopuk cop kumesi (Guvercinlik 2,3x / DEMIRYURT 129x) kesilir.
GAP_MIN_ABS_M = 20_000.0   # bosluk en az 20 km olmali
GAP_MIN_RATIO = 2.0        # ve uzaklik en az 2 katina sicramali

# Guvenlik freni: uyarlanir kesim entity'lerin bundan fazlasini atacaksa
# sezgi yanilmis demektir (cop, verinin cogunlugu olamaz -- asama 1 zaten
# blok tanimlarini temizledi), kesim uygulanmaz.
MAX_STAGE2_DROP_FRACTION = 0.40

# Istege bagli SERT ust sinir (metre). Varsayilan None = sadece uyarlanir
# kesim kullanilir. GUI'den bir deger verilirse ek bir tavan olarak calisir.
LOCAL_RADIUS_M = None


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
    stage2_cut_m: float | None = None  # uyarlanir kesim uzakligi (None = kesilmedi)
    warnings: list = field(default_factory=list)

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
        if self.stage2_cut_m is not None:
            lines.append(f"Asama 2 kesim     : medyandan {self.stage2_cut_m / 1000:.1f} km (uyarlanir bosluk)")
        if self.dropped_by_kind:
            parts = ", ".join(f"{k}:{v}" for k, v in sorted(self.dropped_by_kind.items()))
            lines.append(f"Atilan turler     : {parts}")
        for w in self.warnings:
            lines.append(f"UYARI             : {w}")
        return lines


def _entity_in_bbox(entity: dict, bbox: tuple) -> bool:
    x_min, x_max, y_min, y_max = bbox
    coords = entity.get("coordinates") or []
    if not coords:
        return False
    return all(x_min < c["x"] < x_max and y_min < c["y"] < y_max for c in coords)


def _entity_distance(entity: dict, cx: float, cy: float) -> float:
    """Entity'nin merkeze en uzak vertex'inin Chebyshev (kare kutu) uzakligi."""
    return max(
        max(abs(c["x"] - cx), abs(c["y"] - cy)) for c in entity["coordinates"]
    )


def find_gap_cut(
    distances: list[float],
    min_gap: float = GAP_MIN_ABS_M,
    min_ratio: float = GAP_MIN_RATIO,
    max_drop_fraction: float = MAX_STAGE2_DROP_FRACTION,
) -> float | None:
    """Sirali uzaklik dizisinde gercek veri ile cop kumesi arasindaki
    kopuklugu bulur.

    Gercek veri surekli oldugu icin komsu uzakliklar arasindaki artis
    kucuktur; cop geometri ise uzakta ayri bir kume olusturdugu icin araya
    buyuk bir bosluk girer. Kesim ancak bosluk HEM mutlak (>= min_gap) HEM
    goreli (>= min_ratio kat) olarak belirginse yapilir.

    Returns:
        Kesim uzakligi (bu degerden UZAK entity'ler atilir) veya kesim
        gerekmiyorsa None.
    """
    n = len(distances)
    if n < 2:
        return None
    best_gap = 0.0
    best_index = None
    for i in range(1, n):
        prev, cur = distances[i - 1], distances[i]
        gap = cur - prev
        if gap < min_gap:
            continue
        if cur < min_ratio * max(prev, 1.0):
            continue
        if (n - i) > max_drop_fraction * n:
            continue  # guvenlik freni: cop, verinin cogunlugu olamaz
        if gap > best_gap:
            best_gap = gap
            best_index = i
    if best_index is None:
        return None
    return distances[best_index - 1]


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

    # --- Asama 2: uzaklik dagilimindaki kopukluk ------------------------
    if stage2_enabled:
        xs = [c["x"] for e in stage1_kept for c in e["coordinates"]]
        ys = [c["y"] for e in stage1_kept for c in e["coordinates"]]
        cx = statistics.median(xs)
        cy = statistics.median(ys)

        measured = [(e, _entity_distance(e, cx, cy)) for e in stage1_kept]
        cut = find_gap_cut(sorted(d for _, d in measured))
        if radius is not None:
            cut = radius if cut is None else min(cut, radius)

        if cut is None:
            stage2_kept = stage1_kept  # veri surekli -> kesecek bir sey yok
        else:
            stage2_kept = []
            dropped_by_kind = {}
            for e, dist in measured:
                if dist <= cut:
                    stage2_kept.append(e)
                else:
                    dropped_by_kind[e["geometry_kind"]] = (
                        dropped_by_kind.get(e["geometry_kind"], 0) + 1
                    )
            if not stage2_kept:
                # Guvenlik agi: asama 2 HER SEYI atacaksa esik bu dosya icin
                # yanlistir. Sessizce bos DXF uretmektense filtresiz devam
                # et ve uyar (bkz. modul dokumantasyonu, KNY... ornegi).
                stats.warnings.append(
                    "Asama 2 tum entity'leri atacakti (esik bu dosya icin uygun degil); "
                    "asama 2 bu dosyada uygulanmadi."
                )
                stage2_kept = stage1_kept
            else:
                stats.stage2_cut_m = cut
                stats.stage2_dropped = sum(dropped_by_kind.values())
                for k, v in dropped_by_kind.items():
                    stats.dropped_by_kind[k] = stats.dropped_by_kind.get(k, 0) + v
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
