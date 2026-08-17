"""NCZ dosya kesfi. Windows dosya sistemi case-insensitive oldugu icin
'*.ncz' ve '*.NCZ' glob'lari ayni dosyalari iki kez doner -- bu modul
sonucu path'e gore tekillestirir."""
from __future__ import annotations

import glob
import os
from pathlib import Path


def find_ncz_files(folder: str) -> list[str]:
    """`folder` icindeki .ncz dosyalarini (buyuk/kucuk harf farketmeksizin,
    tekillestirilmis, isme gore siralanmis) dondurur."""
    raw = glob.glob(os.path.join(folder, "*.ncz")) + glob.glob(os.path.join(folder, "*.NCZ"))
    seen = {}
    for p in raw:
        key = os.path.normcase(os.path.abspath(p))
        seen.setdefault(key, p)
    return sorted(seen.values(), key=lambda p: Path(p).name.lower())
