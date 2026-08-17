"""
Donusum raporu: dosya basina istatistik, mukerrer dosya tespiti, CRS/datum
uyarilari. Ciktisi GUI'de gosterilir ve donusum_raporu.txt olarak yazilir.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .dxf_writer import FileResult

# Filtrelenmis merkezleri bu mesafenin altinda olan dosya ciftleri
# "muhtemelen mukerrer" olarak isaretlenir (ayni koye ait iki farkli
# katman/kayit -- ust uste binip gorsel karisikliga sebep olur).
DUP_DISTANCE_M = 500.0


@dataclass
class DuplicatePair:
    file_a: str
    file_b: str
    distance_m: float


def _center(result: FileResult) -> tuple | None:
    """Medyan merkezi kullanir (bbox orta noktasi DEGIL) -- bkz.
    FilterStats.center dokumantasyonu: paylasilan proje sinir katmanlari
    olan dosyalarda bbox orta noktasi yanlislikla ozdes cikabiliyor."""
    if not result.filter_stats:
        return None
    return result.filter_stats.center


def detect_duplicates(results: Iterable[FileResult], max_distance: float = DUP_DISTANCE_M) -> list[DuplicatePair]:
    """Merkezleri birbirine `max_distance` metreden yakin dosya ciftlerini
    dondurur. O(n^2) ama n tipik olarak birkac yuz dosyayi gecmez."""
    items = [(r, _center(r)) for r in results if r.ok]
    items = [(r, c) for r, c in items if c is not None]
    pairs = []
    for i in range(len(items)):
        r1, c1 = items[i]
        for j in range(i + 1, len(items)):
            r2, c2 = items[j]
            d = ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2) ** 0.5
            if d <= max_distance:
                pairs.append(DuplicatePair(r1.ncz_path.name, r2.ncz_path.name, round(d, 1)))
    return pairs


def write_report(path: str | Path, results: list[FileResult], merge_summary: str = "") -> None:
    path = Path(path)
    lines = []
    lines.append("NCZ -> DXF Donusum Raporu")
    lines.append("=" * 60)
    lines.append(f"Islenen dosya sayisi: {len(results)}")
    ok_results = [r for r in results if r.ok]
    fail_results = [r for r in results if not r.ok]
    lines.append(f"Basarili: {len(ok_results)}  |  Hatali: {len(fail_results)}")
    lines.append("")

    if fail_results:
        lines.append("--- HATALI DOSYALAR ---")
        for r in fail_results:
            lines.append(f"  {r.ncz_path.name}: {r.error}")
        lines.append("")

    lines.append("--- DOSYA BASINA DETAY ---")
    for r in ok_results:
        fs = r.filter_stats
        lines.append(f"\n{r.ncz_path.name}")
        if r.parser_projection or r.parser_epsg:
            lines.append(f"  CRS (bilgi amacli, donusturulmuyor): {r.parser_projection} | {r.parser_epsg}")
        if fs:
            for line in fs.as_lines():
                lines.append(f"  {line}")
        if r.entity_stats:
            parts = ", ".join(f"{k}:{v}" for k, v in sorted(r.entity_stats.items()))
            lines.append(f"  Yazilan turler   : {parts}")
        if r.skipped:
            lines.append(f"  Atlanan (gecersiz veri): {r.skipped}")

    dups = detect_duplicates(ok_results)
    if dups:
        lines.append("")
        lines.append("--- OLASI MUKERRER DOSYALAR (merkezleri < 500 m) ---")
        for d in dups:
            lines.append(f"  {d.file_a}  <->  {d.file_b}   (mesafe {d.distance_m} m)")

    ed50 = [r for r in ok_results if "ED50" in (r.parser_projection or "")]
    if ed50:
        lines.append("")
        lines.append("--- FARKLI DATUM UYARISI (donusum yapilmadi, ~100 m kayma olabilir) ---")
        for r in ed50:
            lines.append(f"  {r.ncz_path.name}: {r.parser_projection}")

    if merge_summary:
        lines.append("")
        lines.append("--- BIRLESTIRME ---")
        lines.append(merge_summary)

    path.write_text("\n".join(lines), encoding="utf-8")
