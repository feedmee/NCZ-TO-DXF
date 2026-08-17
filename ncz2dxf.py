"""
NCZ -> DXF donusturucu giris noktasi.

  python ncz2dxf.py                          -> GUI acar
  python ncz2dxf.py <klasor> --out <cikti>    -> CLI (headless), bkz. --help

--windowed olarak build edilen exe'de argumansiz cift tiklama GUI'yi acar;
argumanla (orn. kisayoldan) cagrilirsa GUI acmadan CLI calisir.
"""
import sys


def main():
    if len(sys.argv) > 1:
        from ncztool.cli import run_cli
        sys.exit(run_cli(sys.argv[1:]))
    else:
        from ncztool.gui import main as gui_main
        gui_main()


if __name__ == "__main__":
    main()
