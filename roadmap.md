# Roadmap

## Tamamlanan

- [x] Cok-mevkili NCZ dosyalarinda Aşama 2'nin uzaktaki gercek veriyi silme
  riskini sentetik regresyon testiyle yeniden uret.
- [x] Aşama 2'yi API, CLI ve GUI icin varsayilan kapali, acikca etkinlestirilen
  bir filtreye donustur; `--no-stage2` geriye uyumlulugunu koru.
- [x] README ve arayuz aciklamalarini veri-kaybi riski ve `--stage2` kullanimi
  konusunda guncelle.
- [x] Regresyon testlerini, Python derleme kontrolunu, CLI yardimini ve Git diff
  butunluk kontrolunu calistir.

## Dogrulama

- `python -m pytest tests -q`: 17 passed, 11 skipped
- `python -m compileall -q ncztool ncz2dxf.py`: basarili
- `python ncz2dxf.py --help`: yeni `--stage2 | --no-stage2` sozlesmesi goruldu
- `python -m PyInstaller --clean --noconfirm NCZ2DXF.spec`: basarili
- `dist\\NCZ2DXF.exe --help`: exit code 0
- `git diff --check`: hata yok

Not: Atlanan testler yerel `Toplulastirma` ve `Menfezler` NCZ veri klasorlerini
gerektiriyor. `Menfezler_ORT.NCZ` bu makinede bulunmadigi icin gercek dosyayla
uc uca donusum testi yapilamadi.
