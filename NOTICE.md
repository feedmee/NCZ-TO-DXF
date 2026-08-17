# Lisans ve Atif

Bu depo GNU General Public License v2.0 (or later) altinda lisanslanmistir.
Tam metin icin bkz. [LICENSE](LICENSE).

## Ucuncu taraf kod

`ncz_pure_parser.py`, [Jeomatik NCZ Reader](https://github.com/erdincunal/Jeomatik-NCZ-Reader)
(QGIS eklentisi, v1.4.3) projesinden **degistirilmeden** alinmistir:

```
Jeomatik NCZ Reader
Copyright (C) 2026 Erdinç Örsan ÜNAL
Lisans: GPL-2.0-or-later
```

Bu depodeki diger tum kod (`ncztool/`, `ncz2dxf.py`, `tests/`) bu projeye
ozgudur ve `ncz_pure_parser.py`'nin `parse_ncz()` fonksiyonunu kullandigi
icin (GPL "copyleft" kosulu geregi) ayni lisans altinda dagitilir.

`legacy/` klasorundeki `ncz_to_dxf_kmz.py` ve `ncz_klasorden_birlesik_dxf.py`
bu projenin erken surumleridir, referans/arsiv amacli tutulmaktadir; aktif
gelistirme `ncztool/` paketinde devam etmektedir.

## Neden bu proje var

Netcad NCZ formati kapali/belgesiz bir binary format. Bu araç, QGIS/PyQGIS
kurulumu gerektirmeden (Jeomatik NCZ Reader'in parser mantigini yeniden
kullanarak) NCZ dosyalarini koordinatli DXF'e cevirir; ham parser ciktisindaki
blok/sembol tanim geometrisini (NCZ'nin ic yapisindan kaynaklanan bir
"gurultu" turu) filtreler ve dosya basina/birlesik DXF uretimini tek bir
Windows exe'sinde toplar. Ayrintili teknik gerekce icin `ncztool/filters.py`
modul dokumantasyonuna bakiniz.
