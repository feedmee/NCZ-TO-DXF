"""
Dosya basina uretilmis DXF'leri koordinat ve konum korunarak tek DXF'te
birlestirir. ezdxf.addons.importer.Importer entity kopyalarken koordinatlara
dokunmaz -- reprojeksiyon/kaydirma yapilmaz (kullanicinin acik talebi).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import ezdxf
from ezdxf import bbox as ezbbox
from ezdxf.addons.importer import Importer


@dataclass
class MergeResult:
    out_path: Path
    ok: bool
    error: str = ""
    file_count: int = 0
    entity_count: int = 0
    bbox: tuple | None = None  # (x_min, x_max, y_min, y_max)
    failed_files: list = field(default_factory=list)  # [(path, error), ...]


def _sanitize(name: str) -> str:
    text = re.sub(r'[<>/\\":;?*|=`\x00-\x1f]', "_", str(name or "").strip())
    return (text.strip("_") or "KATMANSIZ")[:255]


def _prefix_layers(src_doc, prefix: str) -> None:
    """Katman tablosundaki her katmani '{prefix}_{katman}' olarak yeniden
    adlandirir; entity'lerin dxf.layer alani da guncellenir. Boylece ayni
    isimli katmanlar (BLOK, PARSEL_ALN vb.) farkli NCZ dosyalarinda ayri
    kalir ve GUI/CAD'de karismaz."""
    rename_map = {}
    for layer in list(src_doc.layers):
        old_name = layer.dxf.name
        if old_name == "0":
            continue  # DXF standart katmani; olduğu gibi birakilir
        new_name = _sanitize(f"{prefix}_{old_name}")
        try:
            # layer.dxf.name = new_name TEK BASINA yetmez: Table nesnesi
            # ismi bir dict anahtari olarak tutuyor, sadece dxf attribute'u
            # degistirmek tabloyu eski isimle indeksli birakiyor ve sonraki
            # katman aramalari (Importer/entity cozumlemesi) "Required table
            # entry ... not found" hatasi veriyor. Table.replace() eski
            # anahtari siler ve yeni isimle yeniden ekler.
            layer.dxf.name = new_name
            src_doc.layers.replace(old_name, layer)
            rename_map[old_name] = new_name
        except Exception:
            rename_map.pop(old_name, None)

    if not rename_map:
        return
    for entity in src_doc.modelspace():
        try:
            layer = entity.dxf.layer
        except Exception:
            continue
        if layer in rename_map:
            entity.dxf.layer = rename_map[layer]


def merge_dxf(
    paths: Iterable[str | Path],
    out_path: str | Path,
    dxf_version: str = "R2013",
    prefix_layers: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> MergeResult:
    """paths sirasiyla okunup tek `out_path` DXF'inde birlestirilir.

    progress(index, total, current_filename) her dosyadan once cagirilir
    (GUI ilerleme cubugu icin).
    """
    paths = [Path(p) for p in paths]
    out_path = Path(out_path)
    target = ezdxf.new(dxf_version)
    failed = []
    imported_any = False

    for i, p in enumerate(paths, start=1):
        if progress:
            progress(i, len(paths), p.name)
        try:
            src = ezdxf.readfile(str(p))
        except Exception as exc:
            failed.append((str(p), f"okunamadi: {exc}"))
            continue
        try:
            if prefix_layers:
                _prefix_layers(src, p.stem)
            importer = Importer(src, target)
            importer.import_modelspace()
            importer.finalize()
            imported_any = True
        except Exception as exc:
            failed.append((str(p), f"aktarilamadi: {exc}"))
            continue

    if not imported_any:
        return MergeResult(
            out_path=out_path, ok=False,
            error="Hicbir dosya birlestirilemedi.",
            failed_files=failed,
        )

    msp = target.modelspace()
    entity_count = len(msp)

    box = None
    try:
        bb = ezbbox.extents(msp, fast=True)
        if bb.has_data:
            box = (bb.extmin.x, bb.extmax.x, bb.extmin.y, bb.extmax.y)
    except Exception:
        box = None

    if box:
        x0, x1, y0, y1 = box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        height = max(y1 - y0, (x1 - x0) * 0.6, 1.0) * 1.05
        try:
            target.set_modelspace_vport(height=height, center=(cx, cy))
        except Exception:
            pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    target.saveas(str(out_path))

    return MergeResult(
        out_path=out_path,
        ok=True,
        file_count=len(paths) - len(failed),
        entity_count=entity_count,
        bbox=box,
        failed_files=failed,
    )
