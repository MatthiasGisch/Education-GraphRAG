import tkinter as tk
from tkinter import filedialog, messagebox
import pandas as pd
import re

def to_number_safe(x):
    if pd.isna(x):
        return None
    s = str(x).strip()
    s = re.sub(r"[^0-9,.-]", "", s)
    if s.count('.') > 1:
        s = s.replace('.', '')
    s = s.replace(',', '.')
    try:
        return float(s)
    except:
        return None

def merge_csv_files():
    file_paths = filedialog.askopenfilenames(
        title="CSV-Dateien auswählen",
        filetypes=[("CSV-Dateien", "*.csv")]
    )
    if not file_paths:
        return

    save_path = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV-Dateien", "*.csv")],
        title="Zusammengeführte Datei speichern"
    )
    if not save_path:
        return

    separator = ";"  # ggf. "," verwenden
    do_mean = mean_var.get()

    dataframes = []
    for i, file_path in enumerate(file_paths):
        if i == 0:
            df = pd.read_csv(file_path, sep=separator)
        else:
            df = pd.read_csv(file_path, sep=separator, skiprows=1, header=None)
            df.columns = dataframes[0].columns
        dataframes.append(df)

    merged_df = pd.concat(dataframes, ignore_index=True)

    # Neue Spalte "Index" vorne einfügen, beginnend bei 1
    merged_df.insert(0, "Index", range(1, len(merged_df) + 1))

    if do_mean:
        # Sicherstellen, dass mindestens 8 Spalten existieren (jetzt inklusive Index)
        if merged_df.shape[1] - 1 >= 8:
            # Letzte 8 Spalten (ohne Index) auswählen
            last8 = merged_df.iloc[:, -8:].applymap(to_number_safe)
            merged_df[''] = last8.mean(axis=1, skipna=True)
        else:
            messagebox.showwarning(
                "Warnung",
                f"Nur {merged_df.shape[1]-1} Spalten übrig – Mittelwert nicht berechnet."
            )

    merged_df.to_csv(save_path, sep=separator, index=False)

    messagebox.showinfo(
        "Fertig",
        f"Dateien wurden zusammengeführt und gespeichert:\n{save_path}"
    )

# GUI
root = tk.Tk()
root.title("CSV-Merger")

mean_var = tk.BooleanVar()

btn_merge = tk.Button(root, text="CSV-Dateien zusammenführen", command=merge_csv_files)
btn_merge.pack(padx=20, pady=10)

chk_mean = tk.Checkbutton(root, text="Mittelwert der letzten 8 Spalten berechnen", variable=mean_var)
chk_mean.pack(padx=20, pady=10)

root.mainloop()
