"""
Tkinter arayuzu. Iki adim:
  Adim 1: secilen NCZ dosyalarini dosya-basina DXF'e cevirir (filtre +
          entity turu secenekleriyle).
  Adim 2: Adim 1'in urettigi DXF'leri (secilenleri) tek DXF'te birlestirir.

Is parcaciklari (threading) + queue ile GUI donmadan ilerleme gosterilir.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audit import detect_duplicates, write_report
from .discovery import find_ncz_files
from .dxf_writer import ALL_KINDS, LINE_KINDS, POINT_KINDS, POLYGON_KINDS, WriteOptions, write_file_dxf
from .filters import LOCAL_RADIUS_M, TR_BBOX
from .merger import merge_dxf

KIND_GROUPS = [
    ("Yazi (Text)", {"Text"}),
    ("Nokta / Sembol / Blok", set(POINT_KINDS)),
    ("Cizgi / Polyline", set(LINE_KINDS)),
    ("Poligon", set(POLYGON_KINDS)),
    ("Cember", {"Circle"}),
    ("Yay (Arc)", {"Arc"}),
]


class NCZ2DXFApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NCZ -> DXF Donusturucu")
        self.geometry("880x680")
        self.minsize(760, 560)

        self.queue: queue.Queue = queue.Queue()
        self.ncz_folder = tk.StringVar()
        self.out_folder = tk.StringVar()
        self.status_text = tk.StringVar(value="Hazir.")

        self.stage1_var = tk.BooleanVar(value=True)
        self.stage2_var = tk.BooleanVar(value=True)
        self.radius_km_var = tk.StringVar(value=str(int(LOCAL_RADIUS_M / 1000)))
        self.prefix_layers_var = tk.BooleanVar(value=True)
        self.kind_vars = {label: tk.BooleanVar(value=True) for label, _ in KIND_GROUPS}

        self.file_results: dict[str, object] = {}  # ncz stem -> FileResult
        self.ncz_paths: list[str] = []

        self._build_widgets()
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    def _build_widgets(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="NCZ klasoru:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.ncz_folder, width=60).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(top, text="Sec...", command=self._pick_ncz_folder).grid(row=0, column=2)

        ttk.Label(top, text="Cikti klasoru:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(top, textvariable=self.out_folder, width=60).grid(row=1, column=1, sticky="we", padx=4, pady=(4, 0))
        ttk.Button(top, text="Sec...", command=self._pick_out_folder).grid(row=1, column=2, pady=(4, 0))
        top.columnconfigure(1, weight=1)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=4)

        step1 = ttk.Frame(nb, padding=8)
        step2 = ttk.Frame(nb, padding=8)
        nb.add(step1, text="Adim 1 - Dosya basina DXF")
        nb.add(step2, text="Adim 2 - Birlestir")
        self._build_step1(step1)
        self._build_step2(step2)

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_text).pack(anchor="w", pady=(2, 4))

        self.log = tk.Text(self, height=10, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=False, padx=8, pady=(0, 8))

    def _build_step1(self, parent):
        left = ttk.Frame(parent)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="NCZ dosyalari:").pack(anchor="w")
        self.file_listbox = tk.Listbox(left, selectmode="extended")
        self.file_listbox.pack(fill="both", expand=True)
        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", pady=2)
        ttk.Button(btn_row, text="Tumunu sec", command=lambda: self.file_listbox.select_set(0, "end")).pack(side="left")
        ttk.Button(btn_row, text="Secimi kaldir", command=lambda: self.file_listbox.select_clear(0, "end")).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Yenile", command=self._refresh_ncz_list).pack(side="left")

        right = ttk.Frame(parent, padding=(12, 0))
        right.pack(side="left", fill="y")

        ttk.Label(right, text="Entity turleri:").pack(anchor="w")
        for label, _ in KIND_GROUPS:
            ttk.Checkbutton(right, text=label, variable=self.kind_vars[label]).pack(anchor="w")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(right, text="Filtre:").pack(anchor="w")
        ttk.Checkbutton(right, text="Turkiye TM kutusu (asama 1)", variable=self.stage1_var).pack(anchor="w")
        ttk.Checkbutton(right, text="Dosya-merkezi uzaklik (asama 2)", variable=self.stage2_var).pack(anchor="w")
        radius_row = ttk.Frame(right)
        radius_row.pack(anchor="w", pady=(2, 0))
        ttk.Label(radius_row, text="  Yaricap (km):").pack(side="left")
        ttk.Entry(radius_row, textvariable=self.radius_km_var, width=8).pack(side="left")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)
        ttk.Button(right, text="DXF Uret", command=self._start_step1).pack(anchor="w")

    def _build_step2(self, parent):
        left = ttk.Frame(parent)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Uretilen DXF'ler (Adim 1 sonrasi dolar; ⚠ = olasi mukerrer):").pack(anchor="w")
        self.dxf_listbox = tk.Listbox(left, selectmode="extended")
        self.dxf_listbox.pack(fill="both", expand=True)
        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", pady=2)
        ttk.Button(btn_row, text="Tumunu sec", command=lambda: self.dxf_listbox.select_set(0, "end")).pack(side="left")
        ttk.Button(btn_row, text="Secimi kaldir", command=lambda: self.dxf_listbox.select_clear(0, "end")).pack(side="left", padx=4)

        right = ttk.Frame(parent, padding=(12, 0))
        right.pack(side="left", fill="y")
        ttk.Checkbutton(right, text="Katman adina dosya adi ekle", variable=self.prefix_layers_var).pack(anchor="w")
        ttk.Label(right, text="Cikti dosya adi:").pack(anchor="w", pady=(8, 0))
        self.merge_name_var = tk.StringVar(value="birlesik.dxf")
        ttk.Entry(right, textvariable=self.merge_name_var, width=24).pack(anchor="w")
        ttk.Button(right, text="Birlestir", command=self._start_step2).pack(anchor="w", pady=(12, 0))

    # ------------------------------------------------------------------
    def _pick_ncz_folder(self):
        d = filedialog.askdirectory(title="NCZ klasoru sec")
        if d:
            self.ncz_folder.set(d)
            if not self.out_folder.get():
                self.out_folder.set(str(Path(d) / "dxf_cikti"))
            self._refresh_ncz_list()

    def _pick_out_folder(self):
        d = filedialog.askdirectory(title="Cikti klasoru sec")
        if d:
            self.out_folder.set(d)

    def _refresh_ncz_list(self):
        folder = self.ncz_folder.get()
        self.file_listbox.delete(0, "end")
        self.ncz_paths = []
        if not folder or not os.path.isdir(folder):
            return
        paths = find_ncz_files(folder)
        self.ncz_paths = paths
        for p in paths:
            self.file_listbox.insert("end", os.path.basename(p))
        self.file_listbox.select_set(0, "end")
        self._log(f"{len(paths)} NCZ dosyasi bulundu: {folder}")

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _selected_kinds(self) -> set:
        kinds = set()
        for label, group in KIND_GROUPS:
            if self.kind_vars[label].get():
                kinds |= group
        return kinds or set(ALL_KINDS)

    def _write_options(self) -> WriteOptions:
        try:
            radius = float(self.radius_km_var.get()) * 1000.0
        except ValueError:
            radius = LOCAL_RADIUS_M
        return WriteOptions(
            include_kinds=self._selected_kinds(),
            stage1_enabled=self.stage1_var.get(),
            stage2_enabled=self.stage2_var.get(),
            bbox=TR_BBOX,
            radius=radius,
        )

    # ------------------------------------------------------------------
    def _start_step1(self):
        sel = self.file_listbox.curselection()
        if not sel:
            messagebox.showwarning("Uyari", "En az bir NCZ dosyasi secin.")
            return
        out_folder = self.out_folder.get()
        if not out_folder:
            messagebox.showwarning("Uyari", "Cikti klasoru secin.")
            return
        paths = [self.ncz_paths[i] for i in sel]
        options = self._write_options()
        self.progress.configure(mode="determinate", maximum=len(paths), value=0)
        self.status_text.set(f"Adim 1: 0/{len(paths)}")
        threading.Thread(target=self._run_step1, args=(paths, out_folder, options), daemon=True).start()

    def _run_step1(self, paths, out_folder, options):
        per_file_dir = Path(out_folder) / "per_file"
        results = []
        for i, p in enumerate(paths, start=1):
            stem = Path(p).stem
            out_path = per_file_dir / f"{stem}.dxf"
            res = write_file_dxf(p, out_path, options)
            results.append(res)
            self.queue.put(("step1_progress", i, len(paths), stem, res))
        self.queue.put(("step1_done", results, out_folder))

    def _start_step2(self):
        sel = self.dxf_listbox.curselection()
        if not sel:
            messagebox.showwarning("Uyari", "En az bir DXF secin.")
            return
        out_folder = self.out_folder.get()
        if not out_folder:
            messagebox.showwarning("Uyari", "Cikti klasoru secin.")
            return
        names = [self.dxf_listbox.get(i) for i in sel]
        paths = [self.file_results[n.lstrip("⚠ ")].out_path for n in names]
        merge_name = self.merge_name_var.get() or "birlesik.dxf"
        out_path = Path(out_folder) / merge_name
        prefix = self.prefix_layers_var.get()
        self.progress.configure(mode="determinate", maximum=len(paths), value=0)
        self.status_text.set(f"Adim 2: birlestiriliyor (0/{len(paths)})")
        threading.Thread(target=self._run_step2, args=(paths, out_path, prefix), daemon=True).start()

    def _run_step2(self, paths, out_path, prefix):
        def progress(i, total, name):
            self.queue.put(("step2_progress", i, total, name))

        res = merge_dxf(paths, out_path, prefix_layers=prefix, progress=progress)
        self.queue.put(("step2_done", res))

    # ------------------------------------------------------------------
    def _poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _handle_msg(self, msg):
        kind = msg[0]
        if kind == "step1_progress":
            _, i, total, stem, res = msg
            self.progress.configure(value=i)
            self.status_text.set(f"Adim 1: {i}/{total} - {stem}")
            if res.ok:
                written = sum(res.entity_stats.values())
                self._log(f"  [{i}/{total}] {stem}: {written} entity ({res.skipped} atlandi)")
            else:
                self._log(f"  [{i}/{total}] {stem}: HATA - {res.error}")
        elif kind == "step1_done":
            _, results, out_folder = msg
            self.file_results = {r.ncz_path.stem: r for r in results}
            ok_results = [r for r in results if r.ok]
            dups = detect_duplicates(ok_results)
            dup_names = set()
            for d in dups:
                dup_names.add(Path(d.file_a).stem)
                dup_names.add(Path(d.file_b).stem)
                self._log(f"  ⚠ Olasi mukerrer: {d.file_a} <-> {d.file_b} ({d.distance_m} m)")
            report_path = Path(out_folder) / "donusum_raporu.txt"
            write_report(report_path, results)
            self.status_text.set(f"Adim 1 tamam: {len(ok_results)}/{len(results)} basarili. Rapor: {report_path}")
            self._log(f"Adim 1 tamamlandi. Rapor: {report_path}")

            self.dxf_listbox.delete(0, "end")
            for r in ok_results:
                mark = "⚠ " if r.ncz_path.stem in dup_names else ""
                self.dxf_listbox.insert("end", f"{mark}{r.ncz_path.stem}")
            self.dxf_listbox.select_set(0, "end")
        elif kind == "step2_progress":
            _, i, total, name = msg
            self.progress.configure(value=i)
            self.status_text.set(f"Adim 2: {i}/{total} - {name}")
        elif kind == "step2_done":
            res = msg[1]
            if res.ok:
                self.status_text.set(f"Birlestirme tamam: {res.entity_count} entity -> {res.out_path}")
                self._log(f"Birlestirme tamam: {res.file_count} dosya, {res.entity_count} entity -> {res.out_path}")
                if res.bbox:
                    x0, x1, y0, y1 = res.bbox
                    self._log(f"  Bbox: X[{x0:.1f},{x1:.1f}] Y[{y0:.1f},{y1:.1f}]  ({(x1-x0)/1000:.1f} x {(y1-y0)/1000:.1f} km)")
                if res.failed_files:
                    for name, err in res.failed_files:
                        self._log(f"  Basarisiz: {name}: {err}")
                messagebox.showinfo("Tamam", f"Birlestirme tamamlandi:\n{res.out_path}\n{res.entity_count} entity")
            else:
                self._log(f"Birlestirme HATASI: {res.error}")
                messagebox.showerror("Hata", res.error)


def main():
    app = NCZ2DXFApp()
    app.mainloop()


if __name__ == "__main__":
    main()
