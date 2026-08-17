"""Komut satiri arayuzu (headless mod). GUI acmadan toplu islem icin."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .audit import write_report
from .discovery import find_ncz_files
from .dxf_writer import ALL_KINDS, WriteOptions, write_file_dxf
from .filters import LOCAL_RADIUS_M, TR_BBOX
from .merger import merge_dxf

KIND_ALIASES = {
    "text": "Text", "yazi": "Text",
    "point": "Point", "nokta": "Point",
    "symbol": "Symbol", "sembol": "Symbol",
    "block": "Block", "blok": "Block",
    "line": "Line", "cizgi": "Line",
    "polyline": "Polyline",
    "polygon": "Polygon", "poligon": "Polygon",
    "circle": "Circle", "cember": "Circle",
    "arc": "Arc", "yay": "Arc",
    "box": "Box", "triangle": "Triangle", "mapsheet": "MapSheet",
    "smartobject": "SmartObject",
}


def _parse_kinds(value: str) -> set:
    if not value:
        return set(ALL_KINDS)
    out = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        key = token.lower()
        out.add(KIND_ALIASES.get(key, token))
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ncz2dxf",
        description="NCZ -> DXF donusturucu (dosya basina + istege bagli birlestirme). "
                    "Argumansiz calistirilirsa GUI acilir.",
    )
    p.add_argument("input_folder", help="NCZ dosyalarinin bulundugu klasor")
    p.add_argument("--out", required=True, help="Cikti klasoru")
    p.add_argument("--merge", action="store_true", help="Dosya basina DXF'lerin ardindan birlesik DXF de uret")
    p.add_argument("--merge-name", default="birlesik.dxf", help="Birlesik DXF dosya adi (varsayilan: birlesik.dxf)")
    p.add_argument("--no-prefix-layers", action="store_true", help="Birlestirirken katman adina dosya adi ekleme")
    p.add_argument("--no-stage1", action="store_true", help="Turkiye TM kutusu filtresini kapat")
    p.add_argument("--no-stage2", action="store_true", help="Dosya-merkezi uzaklik filtresini kapat")
    p.add_argument(
        "--radius",
        type=float,
        default=LOCAL_RADIUS_M,
        help="Asama 2 icin istege bagli SERT yaricap tavani, metre. "
             "Varsayilan: verilmezse uyarlanir bosluk tespiti kullanilir.",
    )
    p.add_argument("--kinds", default="", help="Sadece bu turleri yaz (virgulle ayrik: text,point,line,polygon,circle,arc,block,symbol). Bos ise hepsi.")
    p.add_argument("--dxf-version", default="R2013", help="DXF surumu (varsayilan R2013)")
    return p


def run_cli(argv=None) -> int:
    args = build_parser().parse_args(argv)

    ncz_files = find_ncz_files(args.input_folder)
    if not ncz_files:
        print(f"'{args.input_folder}' icinde .ncz dosyasi bulunamadi.", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    per_file_dir = out_dir / "per_file"
    per_file_dir.mkdir(parents=True, exist_ok=True)

    options = WriteOptions(
        dxf_version=args.dxf_version,
        include_kinds=_parse_kinds(args.kinds),
        stage1_enabled=not args.no_stage1,
        stage2_enabled=not args.no_stage2,
        bbox=TR_BBOX,
        radius=args.radius,
    )

    print(f"{len(ncz_files)} NCZ dosyasi bulundu.")
    t0 = time.time()
    results = []
    for i, ncz_path in enumerate(ncz_files, start=1):
        stem = Path(ncz_path).stem
        out_path = per_file_dir / f"{stem}.dxf"
        res = write_file_dxf(ncz_path, out_path, options)
        results.append(res)
        if res.ok:
            written = sum(res.entity_stats.values())
            print(f"  [{i}/{len(ncz_files)}] {stem}: {written} entity yazildi")
        else:
            print(f"  [{i}/{len(ncz_files)}] {stem}: HATA - {res.error}")

    ok_results = [r for r in results if r.ok]
    print(f"\nDosya basina tamamlandi: {len(ok_results)}/{len(ncz_files)} basarili, {time.time()-t0:.1f}s")

    merge_summary = ""
    if args.merge and ok_results:
        merge_out = out_dir / args.merge_name
        t1 = time.time()

        def _progress(i, total, name):
            if i % 10 == 0 or i in (1, total):
                print(f"  Birlestiriliyor [{i}/{total}] {name}")

        mres = merge_dxf(
            [r.out_path for r in ok_results],
            merge_out,
            dxf_version=args.dxf_version,
            prefix_layers=not args.no_prefix_layers,
            progress=_progress,
        )
        if mres.ok:
            print(f"\nBirlestirme tamam: {mres.entity_count} entity, {mres.file_count} dosya, {time.time()-t1:.1f}s")
            print(f"  Cikti: {mres.out_path}")
            if mres.bbox:
                x0, x1, y0, y1 = mres.bbox
                print(f"  Bbox: X[{x0:.1f},{x1:.1f}] Y[{y0:.1f},{y1:.1f}]  ({(x1-x0)/1000:.1f} x {(y1-y0)/1000:.1f} km)")
            merge_summary = f"Cikti: {mres.out_path}\nEntity: {mres.entity_count}\nDosya: {mres.file_count}"
            if mres.failed_files:
                merge_summary += f"\nBasarisiz: {len(mres.failed_files)}"
        else:
            print(f"\nBirlestirme HATASI: {mres.error}", file=sys.stderr)
            merge_summary = f"HATA: {mres.error}"

    report_path = out_dir / "donusum_raporu.txt"
    write_report(report_path, results, merge_summary=merge_summary)
    print(f"\nRapor: {report_path}")
    return 0
