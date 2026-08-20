# NCZ2DXF

Netcad **NCZ** ciziminden **koordinatli DXF**'e QGIS/PyQGIS gerektirmeden
donusum araci. Dosya basina DXF uretir, istege bagli olarak koordinatlari ve
konumlari koruyarak tek bir DXF'te birlestirir. Windows icin tek dosya exe
olarak dagitilir (Python kurulumu gerekmez).

## Neden

Netcad NCZ formati kapali/belgesiz bir binary format. Ham parser ciktisi
NetCAD'in blok/sembol *tanim* geometrisini de (gercek harita verisiyle
karisan, yerel koordinatlarda duran bir "gurultu" turu) icerir; bu proje bunu
olcumle dogrulanmis iki asamali bir filtreyle ayiklar ve arc/yay yon hatasi
gibi asagi akis DXF hatalarini duzeltir. Ayrintili teknik gerekce:
[`ncztool/filters.py`](ncztool/filters.py).

Korunan veriler:

- **Koordinat ve konum** aynen yazilir; reprojeksiyon/kaydirma yapilmaz.
- **Kot (Z)**: NCZ'de kot varsa DXF'e gecer. Vertex'lerinde farkli kot bulunan
  cizgi/poligonlar 3B `POLYLINE`, duz olanlar kompakt `LWPOLYLINE` olur.
  Bozuk okumalar (10^34 mertebesinde cop float'lar ve denormalize degerler)
  ayiklanir.
- **Oznitelik verisi**: parsel numarasi gibi alanlar (`label_text`, `name`)
  her entity'ye `NCZ` appid'li **XDATA** olarak eklenir ve birlestirmeden
  sonra da korunur. Olcum: 69 dosyalik korpusta 59.142 parsel etiketi +
  35.227 nokta adi -- bunlarin hicbiri eskiden DXF'e gecmiyordu.
- **Orijinal NetCAD katman adlari ve renkleri**.

## Kullanim

### GUI (onerilen)

`NCZ2DXF.exe` dosyasina cift tiklayin:

1. **Adim 1 - Dosya basina DXF**: NCZ klasoru + cikti klasoru secin, entity
   turlerini ve filtre ayarlarini isteginize gore ayarlayin, **DXF Uret**.
2. **Adim 2 - Birlestir**: birlestirilecek DXF'leri secin (olasi mukerrer
   dosyalar ⚠ ile isaretlenir), **Birlestir**.

Adim 2 **Adim 1'e bagli degildir**: program kapatilip acildiginda da cikti
klasorundeki mevcut DXF'ler otomatik listelenir (**Yenile** ile tazelenir,
**DXF klasoru sec...** ile baska bir klasordeki DXF'ler de birlestirilebilir).

**Asama 2 varsayilan olarak kapalidir.** Yalnizca koordinat dagilimina bakan
uzaklik filtresi, cok-mevkili dosyalarda uzaktaki gercek bir mevkiyi cop kume
sanabilir. Asama 1 olculen cop geometrinin %99'undan fazlasini tek basina
temizledigi icin veri kaybi yerine az miktarda cop geometriyi korumak tercih
edilir. Tek-mevkili oldugunu bildiginiz dosyalarda GUI kutusuyla veya
`--stage2` ile asama 2'yi acabilirsiniz. **Yaricap (km)** alani bosken
uyarlanir bosluk kesimi, sayi verildiginde ek bir sert tavan kullanilir.

Her calisma sonunda `donusum_raporu.txt` yazilir: dosya basina entity
sayilari, atilan cop/aykiri veri, tespit edilen CRS (bilgi amacli, donusum
yapilmaz), olasi mukerrer dosya ciftleri, farkli datum uyarilari.

### Komut satiri (headless)

```bash
NCZ2DXF.exe <ncz_klasoru> --out <cikti_klasoru> --merge
```

Onemli secenekler: `--no-stage1`, `--stage2` (istege bagli uzaklik filtresi),
`--no-stage2` (geriye uyumlu, varsayilan davranis),
`--radius <metre>`, `--kinds text,point,line,polygon,circle,arc,block,symbol`,
`--no-prefix-layers`. Tum secenekler: `NCZ2DXF.exe --help`.

## Kaynaktan calistirma / derleme

```bash
python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install ezdxf pyinstaller
.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm NCZ2DXF.spec
```

(Izole venv gerekli: bazi Windows Store Python kurulumlarindaki eskimis
`pathlib` backport paketi PyInstaller ile catisiyor.)

Testler (gercek NCZ verisi gerektirir, yoksa atlanir):

```bash
pip install pytest
pytest tests/
```

## Yapisi

| Yol | Icerik |
|---|---|
| `ncz_pure_parser.py` | Upstream parser (bkz. Lisans), degistirilmez |
| `ncztool/filters.py` | Iki asamali cop/aykiri deger filtresi |
| `ncztool/dxf_writer.py` | Entity -> DXF (dosya basina) |
| `ncztool/merger.py` | DXF'leri koordinat korunarak birlestirir |
| `ncztool/audit.py` | Rapor + mukerrer dosya tespiti |
| `ncztool/gui.py` / `cli.py` | Tkinter arayuz / komut satiri |
| `legacy/` | Erken surumler, referans/arsiv amacli |

## Lisans

GPL-2.0-or-later. Bu proje [Jeomatik NCZ Reader](https://github.com/erdincunal/Jeomatik-NCZ-Reader)
(Copyright (C) 2026 Erdinç Örsan ÜNAL) projesinin parser'ini kullanir; bkz.
[LICENSE](LICENSE) ve [NOTICE.md](NOTICE.md).
