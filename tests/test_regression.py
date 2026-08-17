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
from ncztool.dxf_writer import (  # noqa: E402
    XDATA_APPID,
    WriteOptions,
    clean_z,
    normalize_arc_angles,
    sanitize_dxf_text,
    write_file_dxf,
)
from ncztool.filters import filter_entities, find_gap_cut  # noqa: E402
from ncztool.merger import merge_dxf  # noqa: E402

DATA_DIR = Path(r"C:\Users\myilm\Toplulastirma")
MENFEZ_DIR = Path(r"C:\Users\myilm\Menfezler")
requires_data = pytest.mark.skipif(not DATA_DIR.is_dir(), reason="Toplulastirma veri klasoru bu makinede yok")
requires_menfez = pytest.mark.skipif(not MENFEZ_DIR.is_dir(), reason="Menfezler veri klasoru bu makinede yok")


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
# filters.py -- uyarlanir asama 2 (sabit yaricap yerine bosluk tespiti)
# ---------------------------------------------------------------------

def test_gap_cut_ignores_continuous_data():
    """Gercek veri sureklidir: kucuk artislar kesim tetiklememeli."""
    distances = [float(i) * 1000 for i in range(1, 100)]  # 1..99 km, duzgun
    assert find_gap_cut(distances) is None


def test_gap_cut_finds_far_garbage_cluster():
    """Uzakta ayri duran cop kumesi kesilmeli (DEMIRYURT deseni)."""
    distances = [1000.0, 2000.0, 3000.0, 3810.0, 492580.0, 492600.0]
    cut = find_gap_cut(distances)
    assert cut == pytest.approx(3810.0)


def test_gap_cut_safety_brake_on_majority_drop():
    """Cop, verinin cogunlugu olamaz; boyle bir kesim reddedilmeli."""
    distances = [1000.0] + [500_000.0 + i for i in range(99)]
    assert find_gap_cut(distances) is None


@requires_menfez
def test_province_wide_file_not_emptied():
    """KNY_FVZPSA_HDT_33_OND.NCZ il geneli bir karayolu menfez verisi
    (168 x 86 km). Sabit yaricapli eski filtre 10.900 entity'nin TAMAMINI
    atip BOS DXF uretiyordu -- uyarlanir kesim hicbirini atmamali."""
    r = parse_ncz(str(MENFEZ_DIR / "KNY_FVZPSA_HDT_33_OND.NCZ"))
    kept, stats = filter_entities(r["entities"])
    assert stats.raw_total == 10900
    assert stats.kept_total == 10900
    assert stats.stage2_dropped == 0
    assert stats.stage2_cut_m is None  # veri surekli -> kesim yok


def test_stage2_never_silently_empties_output():
    """Esik bu dosya icin yanlissa asama 2 devre disi kalip UYARMALI,
    sessizce bos cikti uretmemeli."""
    entities = [
        {"geometry_kind": "Point", "layer_code": 1, "coordinates": [{"x": 500000.0 + i * 5, "y": 4100000.0}]}
        for i in range(20)
    ]
    kept, stats = filter_entities(entities, radius=1.0)  # 1 m: herkesi atacak kadar kucuk
    assert stats.kept_total == 20
    assert stats.warnings, "asama 2 her seyi atarken uyari verilmeli"


# ---------------------------------------------------------------------
# dxf_writer.py -- kot (Z), oznitelik (XDATA), bozuk metin
# ---------------------------------------------------------------------

def test_clean_z_rejects_corrupt_and_denormal_values():
    assert clean_z(1022.37) == pytest.approx(1022.37)
    assert clean_z(1e34) == 0.0            # bozuk okuma (1^34 degil, 10^34)
    assert clean_z(-1.3e31) == 0.0
    assert clean_z(float("nan")) == 0.0
    assert clean_z(3.19e-27) == 0.0        # denormalize cop -> 2B kabul
    assert clean_z(None) == 0.0


def test_sanitize_dxf_text_drops_binary_garbage():
    """Tek bir bozuk metin (NUL/satir sonu iceren) DXF'in TAMAMINI
    okunamaz hale getiriyordu; boyle diziler tamamen atilmali."""
    assert sanitize_dxf_text("181/32") == "181/32"
    assert sanitize_dxf_text("KOY_SINIR_ÇĞİÖŞÜ") == "KOY_SINIR_ÇĞİÖŞÜ"
    assert sanitize_dxf_text("\x81\xa0A\x0f@6\x00\x00\x01") == ""
    assert sanitize_dxf_text("satir\nsonu") == ""  # DXF satir yapisini bozar
    assert sanitize_dxf_text(None) == ""


@requires_data
def test_parcel_labels_written_as_xdata(tmp_path):
    """Parsel numaralari ('label_text'/'name') eskiden DXF'e hic
    yazilmiyordu; artik XDATA olarak tasinmali."""
    import ezdxf

    out = tmp_path / "AYRANCI.dxf"
    assert write_file_dxf(DATA_DIR / "AYRANCI.NCZ", out, WriteOptions()).ok
    doc = ezdxf.readfile(str(out))
    labels = []
    for e in doc.modelspace():
        if e.has_xdata(XDATA_APPID):
            labels += [t.value for t in e.get_xdata(XDATA_APPID) if t.code == 1000]
    assert any(v.startswith("label=") for v in labels)
    assert any(v.startswith("kind=") for v in labels)


@requires_data
def test_xdata_survives_merge(tmp_path):
    """ezdxf Importer new_clean_entity()'yi keep_xdata VERMEDEN cagirdigi
    icin butun XDATA merge sirasinda siliniyordu."""
    import ezdxf

    src = tmp_path / "AYRANCI.dxf"
    write_file_dxf(DATA_DIR / "AYRANCI.NCZ", src, WriteOptions())
    before = sum(1 for e in ezdxf.readfile(str(src)).modelspace() if e.has_xdata(XDATA_APPID))
    assert before > 0

    merged = tmp_path / "merged.dxf"
    assert merge_dxf([src], merged, prefix_layers=True).ok
    after = sum(1 for e in ezdxf.readfile(str(merged)).modelspace() if e.has_xdata(XDATA_APPID))
    assert after == before

    # monkeypatch geri alinmis olmali
    import ezdxf.addons.importer as importer_mod

    assert importer_mod.new_clean_entity.__name__ == "new_clean_entity"


@requires_menfez
def test_elevation_written_and_survives_merge(tmp_path):
    """NCZ'de kot varsa DXF'e gecmeli (3B POLYLINE / 3B POINT) ve
    birlestirmeden sonra da korunmali."""
    import ezdxf

    src = tmp_path / "KNY.dxf"
    res = write_file_dxf(MENFEZ_DIR / "KNY_FVZPSA_HDT_33_OND.NCZ", src, WriteOptions())
    assert res.ok and res.with_z > 0

    def collect_z(path):
        zs = []
        for e in ezdxf.readfile(str(path)).modelspace():
            t = e.dxftype()
            if t == "POLYLINE":
                zs += [v.dxf.location.z for v in e.vertices if abs(v.dxf.location.z) > 1e-6]
            elif t == "POINT" and abs(e.dxf.location.z) > 1e-6:
                zs.append(e.dxf.location.z)
        return zs

    zs = collect_z(src)
    assert zs, "kot yazilmadi"
    # Kotlarin buyuk cogunlugu Konya platosu araliginda olmali (olcum:
    # p5..p100 = 1007..1118 m). Parser'in bazi entity'lerde hatali okudugu
    # ~%2'lik dusuk deger kalintisi kabul edilir; medyan belirleyicidir.
    zs.sort()
    median = zs[len(zs) // 2]
    assert 900 < median < 1200, f"medyan kot makul degil: {median}"
    assert max(zs) < 2000, f"asiri yuksek kot: {max(zs)}"

    merged = tmp_path / "merged.dxf"
    assert merge_dxf([src], merged, prefix_layers=True).ok
    assert len(collect_z(merged)) == len(zs)


def test_circle_without_radius_uses_default():
    """NCZ'de yaricap yoksa cember atlanmak yerine varsayilan 1 m ile
    cizilmeli (kullanici istegi)."""
    import ezdxf

    from ncztool.dxf_writer import _empty_stats, add_entity_to_dxf

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    stats = _empty_stats()
    entity = {
        "geometry_kind": "Circle", "layer_code": 1, "radius": 0.0,
        "coordinates": [{"x": 500000.0, "y": 4100000.0, "z": 0.0}],
    }
    add_entity_to_dxf(msp, entity, "TEST", stats, WriteOptions())
    circles = [e for e in msp if e.dxftype() == "CIRCLE"]
    assert len(circles) == 1
    assert circles[0].dxf.radius == pytest.approx(1.0)
    assert stats["circle_default_radius"] == 1


def test_circle_with_corrupt_radius_still_skipped():
    """Varsayilan yaricap sadece 'yaricap yok' durumu icin; bozuk (asiri
    buyuk) okuma yine atlanmali."""
    import ezdxf

    from ncztool.dxf_writer import _empty_stats, add_entity_to_dxf

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    stats = _empty_stats()
    entity = {
        "geometry_kind": "Circle", "layer_code": 1, "radius": 5_000_000.0,
        "coordinates": [{"x": 500000.0, "y": 4100000.0, "z": 0.0}],
    }
    add_entity_to_dxf(msp, entity, "TEST", stats, WriteOptions())
    assert [e for e in msp if e.dxftype() == "CIRCLE"] == []
    assert stats["skipped"] == 1


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
