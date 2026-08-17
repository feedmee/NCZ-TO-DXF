"""
Regresyon testleri. Sayilar C:\\Users\\myilm\\Toplulastirma'daki gercek 69
NCZ dosyasi uzerinde bu projenin gelistirilmesi sirasinda olculdu (bkz.
ncztool/filters.py modul dokumantasyonu). Bu klasor bulunmuyorsa (baska bir
makine/profil) testler atlanir.
"""
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ncz_pure_parser import parse_ncz  # noqa: E402
from ncztool.discovery import find_ncz_files  # noqa: E402
from ncztool.dxf_writer import WriteOptions, normalize_arc_angles, write_file_dxf  # noqa: E402
from ncztool.filters import filter_entities  # noqa: E402
from ncztool.merger import merge_dxf  # noqa: E402

DATA_DIR = Path(r"C:\Users\myilm\Toplulastirma")
requires_data = pytest.mark.skipif(not DATA_DIR.is_dir(), reason="Toplulastirma veri klasoru bu makinede yok")


def _bbox_close(bbox, expected, tol=1.0):
    assert bbox is not None
    for got, exp in zip(bbox, expected):
        assert abs(got - exp) < tol, f"bbox {bbox} != beklenen {expected}"


# ---------------------------------------------------------------------
# filters.py
# ---------------------------------------------------------------------

@requires_data
def test_ayranci_kept_intact():
    """Kucuk/tek bolgeli dosyada hicbir sey atilmamali."""
    r = parse_ncz(str(DATA_DIR / "AYRANCI.NCZ"))
    kept, stats = filter_entities(r["entities"])
    assert stats.raw_total == 1785
    assert stats.stage1_dropped == 0
    assert stats.stage2_dropped == 0
    assert stats.kept_total == 1785
    _bbox_close(stats.bbox, (556531.55, 568442.89, 4128656.95, 4143845.41))


@requires_data
def test_demiryurt_drops_block_garbage_and_far_outliers():
    """Blok/sembol tanim copu (asama 1) ve 9 uzak aykiri deger (asama 2)
    atilmali; sonuc 6x7 km'lik gercek dosya alanina inmeli."""
    r = parse_ncz(str(DATA_DIR / "DEMIRYURT.NCZ"))
    kept, stats = filter_entities(r["entities"])
    assert stats.raw_total == 31897
    assert stats.stage1_dropped == 30448
    assert stats.stage2_dropped == 9
    assert stats.kept_total == 1440
    _bbox_close(stats.bbox, (501124.17, 507238.56, 4130123.26, 4137610.50))


@requires_data
def test_guvercinlik_wide_area_data_not_clipped():
    """Guvercinlik.NCZ ~70 km capinda gercek/coklu-alan proje verisi
    iceriyor. 100 km yaricap bunu korumali: layer_code!=0 olan TUM
    entity'ler (hicbir cop icermedigi olculmustu) filtreden gecmeli."""
    r = parse_ncz(str(DATA_DIR / "Güvercinlik.NCZ"))
    entities = r["entities"]
    nonzero = [e for e in entities if e["layer_code"] != 0]
    kept, stats = filter_entities(entities)
    kept_ids = {id(e) for e in kept}
    missing = [e for e in nonzero if id(e) not in kept_ids]
    assert missing == [], f"{len(missing)} gercek entity yanlislikla atildi"


def test_filter_entities_empty_input():
    kept, stats = filter_entities([])
    assert kept == []
    assert stats.kept_total == 0
    assert stats.bbox is None


def test_filter_entities_stage_toggles():
    entities = [
        {"geometry_kind": "Point", "layer_code": 1, "coordinates": [{"x": 500000, "y": 4100000}]},
        {"geometry_kind": "Point", "layer_code": 0, "coordinates": [{"x": 1.0, "y": 2.0}]},  # TR kutusu disi
    ]
    kept_both, stats_both = filter_entities(entities)
    assert stats_both.kept_total == 1
    kept_no_s1, stats_no_s1 = filter_entities(entities, stage1_enabled=False, stage2_enabled=False)
    assert stats_no_s1.kept_total == 2  # hicbir filtre yok


# ---------------------------------------------------------------------
# dxf_writer.py -- arc yon hatasi duzeltmesi
# ---------------------------------------------------------------------

def test_arc_angle_wraps_through_zero_correctly():
    """Eski normalize_arc_angles() sweep hesaplandiktan SONRA ikinci kez
    '% 360' uyguluyordu; bu da end < start durumuna dusup yayin ters yone
    cizilmesine sebep oluyordu. Duzeltilmis versiyon sweep'i korumali."""
    start, end = normalize_arc_angles(350.0, 10.0)
    assert start == pytest.approx(350.0)
    assert end == pytest.approx(370.0)  # 20 derecelik doğru sweep, 10.0 DEGIL


def test_arc_angle_radian_input_converted():
    start, end = normalize_arc_angles(math.radians(45), math.radians(135))
    assert start == pytest.approx(45.0)
    assert end == pytest.approx(135.0)


def test_arc_angle_zero_sweep_rejected():
    assert normalize_arc_angles(100.0, 100.0) is None


def test_arc_angle_negative_start_normalized():
    start, end = normalize_arc_angles(-30.0, 30.0)
    assert 0 <= start < 360
    assert end - start == pytest.approx(60.0)


# ---------------------------------------------------------------------
# discovery.py -- Windows case-insensitive glob dedup
# ---------------------------------------------------------------------

def test_find_ncz_files_dedups_case_variants(tmp_path):
    (tmp_path / "koy1.ncz").write_bytes(b"")
    (tmp_path / "KOY2.NCZ").write_bytes(b"")
    found = find_ncz_files(str(tmp_path))
    # Windows'ta '*.ncz' + '*.NCZ' ayni dosyayi iki kez donebilir; sonuc
    # tekillestirilmis ve dosya sayisi kadar olmali.
    names = [Path(p).name for p in found]
    assert len(names) == len(set(n.lower() for n in names)) == 2


# ---------------------------------------------------------------------
# uctan uca: write_file_dxf + merge_dxf
# ---------------------------------------------------------------------

@requires_data
def test_end_to_end_write_and_merge(tmp_path):
    names = ["AYRANCI.NCZ", "ALACATI_TESCIL.NCZ", "DEMIRYURT.NCZ"]
    options = WriteOptions()
    results = []
    for name in names:
        out = tmp_path / "per_file" / f"{Path(name).stem}.dxf"
        res = write_file_dxf(DATA_DIR / name, out, options)
        assert res.ok, res.error
        results.append(res)

    total_written = sum(sum(r.entity_stats.values()) for r in results)
    assert total_written == 319 + 1785 + 1440  # ALACATI + AYRANCI + DEMIRYURT

    merge_out = tmp_path / "birlesik.dxf"
    mres = merge_dxf([r.out_path for r in results], merge_out)
    assert mres.ok
    assert mres.entity_count == total_written
    assert mres.failed_files == []

    import ezdxf
    doc = ezdxf.readfile(str(merge_out))
    assert len(doc.modelspace()) == total_written
    auditor = doc.audit()
    assert len(auditor.errors) == 0


@requires_data
def test_merge_prefixes_layers_without_breaking_table(tmp_path):
    """merger._prefix_layers eskiden Table icindeki dict anahtarini
    guncellemeden sadece layer.dxf.name'i degistiriyordu -- bu da saveas()
    sirasinda 'Required table entry ... not found' hatasina yol aciyordu.
    Bu test hem katman adinin dogru degistigini hem de reload sonrasi
    entity->katman baglantisinin saglam kaldigini dogrular."""
    out = tmp_path / "AYRANCI.dxf"
    res = write_file_dxf(DATA_DIR / "AYRANCI.NCZ", out, WriteOptions())
    assert res.ok

    merge_out = tmp_path / "merged.dxf"
    mres = merge_dxf([out], merge_out, prefix_layers=True)
    assert mres.ok

    import ezdxf
    doc = ezdxf.readfile(str(merge_out))
    layer_names = {layer.dxf.name for layer in doc.layers}
    assert any(name.startswith("AYRANCI_") for name in layer_names)
    for entity in doc.modelspace():
        assert entity.dxf.layer in layer_names  # her entity gecerli bir katmana isaret ediyor
    auditor = doc.audit()
    assert len(auditor.errors) == 0


# ---------------------------------------------------------------------
# audit.py
# ---------------------------------------------------------------------

@requires_data
def test_detect_duplicates_and_datum_warning(tmp_path):
    from ncztool.audit import detect_duplicates

    names = ["CUMRA BLOKLAR.NCZ", "ÇUMRA BLOKLAR.NCZ", "URUNLU11.NCZ"]
    results = []
    for name in names:
        p = DATA_DIR / name
        if not p.exists():
            pytest.skip(f"{name} bu makinede yok")
        out = tmp_path / f"{Path(name).stem}.dxf"
        res = write_file_dxf(p, out, WriteOptions())
        assert res.ok
        results.append(res)

    dups = detect_duplicates(results)
    dup_names = {d.file_a for d in dups} | {d.file_b for d in dups}
    assert "CUMRA BLOKLAR.NCZ" in dup_names
    assert "ÇUMRA BLOKLAR.NCZ" in dup_names

    urunlu = next(r for r in results if r.ncz_path.name == "URUNLU11.NCZ")
    assert "ED50" in urunlu.parser_projection


@requires_data
def test_duplicate_detection_uses_median_not_bbox_midpoint(tmp_path):
    """Alibeyhuyugu/Gokhuyuk/Icericumra/Okcu/tcdd_hat/Uchuyuk NCZ dosyalari
    ayni buyuk Toplulastirma projesinin ortak proje sinir katmanini da
    iceriyor; bu yuzden BBOX ORTA NOKTASI hepsinde ayni cikiyor (olcum:
    488525.9, 4149538.9) ve bunlar eskiden yanlislikla 'mukerrer'
    isaretleniyordu. Medyan merkez (yogun parsel verisine agirlik verir)
    bu dosyalari birbirinden en az birkac km ayirmali -- gercekten farkli
    koyler oldugu icin mukerrer olarak ISARETLENMEMELI."""
    from ncztool.audit import detect_duplicates

    names = ["Alibeyhüyüğü.NCZ", "Gökhüyük.NCZ", "tcdd_hat.NCZ"]
    results = []
    for name in names:
        out = tmp_path / f"{Path(name).stem}.dxf"
        res = write_file_dxf(DATA_DIR / name, out, WriteOptions())
        assert res.ok
        results.append(res)

    for r in results:
        assert r.filter_stats.center is not None

    dups = detect_duplicates(results)
    dup_names = {d.file_a for d in dups} | {d.file_b for d in dups}
    for name in names:
        assert name not in dup_names, f"{name} yanlislikla mukerrer isaretlendi"
