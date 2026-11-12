"""REM (Relevamiento de Expectativas de Mercado, BCRA): lo que esperan los economistas.

Baja el Excel mensual de tablas, saca la mediana de inflación (IPC nivel general) y de tipo de cambio
nominal (A3500, promedio mensual) del total de participantes y del Top 10, y lo guarda en
datos/rem/AAAA-MM.json. Cada edición queda guardada para poder comparar después contra lo que pasó.

Uso: python rem.py   (lo llama diario.py; si la edición ya está guardada no hace nada)
"""
from __future__ import annotations

import io
import json
import re
import urllib.request
import warnings
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).parent
DEST = BASE / "datos" / "rem"
UA = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}
URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/relevamiento-expectativas-mercado-tablas-{}.xlsx"
SECCIONES = {"Precios minoristas (IPC nivel general": "ipc", "Tipo de cambio nominal": "tc"}


def _leer_hoja(ws) -> dict:
    """{'ipc': {'2026-10': 1.8, ..., '12m': (fecha, valor)}, 'tc': {...}} con la mediana."""
    out: dict = {}
    seccion = None
    col_med = None
    for row in ws.iter_rows(values_only=True):
        v = [x for x in row if x is not None]
        if not v:
            continue
        if len(v) == 1 and isinstance(v[0], str):
            seccion = next((k for t, k in SECCIONES.items() if v[0].startswith(t)), None)
            continue
        if seccion is None:
            continue
        if v[0] == "Período":
            col_med = v.index("Mediana")
            continue
        per, ref, med = v[0], v[1], v[col_med]
        d = out.setdefault(seccion, {})
        if isinstance(per, datetime):
            d[per.strftime("%Y-%m")] = med
        elif isinstance(per, str) and per.startswith("próx. 12"):
            d["12m"] = {"hasta": ref.split(";")[-1].strip(), "valor": med}  # ipc: % i.a.; tc: $/USD
        elif isinstance(per, str) and re.fullmatch(r"20\d\d", per):
            d[per] = med                                                      # diciembre de ese año
    return out


def bajar(edicion: str) -> dict | None:
    """edicion = 'AAAA-MM' (mes de la encuesta). None si todavía no se publicó."""
    import openpyxl
    try:
        raw = urllib.request.urlopen(urllib.request.Request(URL.format(edicion), headers=UA), timeout=60).read()
    except Exception:  # noqa: BLE001
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    return {"edicion": edicion, "bajado": date.today().isoformat(), "fuente": URL.format(edicion),
            "total": _leer_hoja(wb["Cuadros de resultados"]), "top10": _leer_hoja(wb["Resultados TOP 10"])}


def actualizar(hoy: date | None = None) -> str | None:
    """Busca la edición del mes pasado y la del anterior; guarda las que falten. Devuelve la última."""
    hoy = hoy or date.today()
    DEST.mkdir(parents=True, exist_ok=True)
    for atras in (1, 2):
        m = hoy.month - atras
        ed = f"{hoy.year + (m - 1) // 12}-{(m - 1) % 12 + 1:02d}"
        f = DEST / f"{ed}.json"
        if not f.exists() and (r := bajar(ed)):
            f.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            print("REM guardado:", ed)
    return ultima()


def ultima() -> str | None:
    eds = sorted(f.stem for f in DEST.glob("*.json"))
    return eds[-1] if eds else None


def cargar(edicion: str | None = None) -> dict | None:
    edicion = edicion or ultima()
    return json.loads((DEST / f"{edicion}.json").read_text(encoding="utf-8")) if edicion else None


if __name__ == "__main__":
    print(actualizar())
    r = cargar()
    print(json.dumps(r["total"], ensure_ascii=False, indent=1)[:1500])
