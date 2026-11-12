"""¿Quién le pega más: el mercado, el REM o el Top 10 del REM?

Cada día se guarda, para cada LECAP/BONCAP, cuánto esperaba cada uno que subiera el CER en su ventana
y qué A3500 esperaba al vencimiento (datos/expectativas_historial.csv). Cuando el CER de fin de ventana
ya está publicado, o el A3500 del vencimiento ya existe, se compara con lo que realmente pasó.

El error se mide en puntos porcentuales de inflación mensual promedio (CER) y en % del dólar (A3500).
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import instrumentos as I

BASE = Path(__file__).parent
ARCHIVO = BASE / "datos" / "expectativas_historial.csv"
CAMPOS = ["fecha_precio", "rem_edicion", "ticker", "vto", "ventana_ini", "ventana_fin",
          "cer_suba_mercado", "cer_suba_rem", "cer_suba_top10", "a3500_hoy", "tc_mercado", "tc_rem", "tc_top10"]
QUIENES = ("mercado", "rem", "top10")


def guardar(fecha_precio: str, rem_edicion: str, expect: list[dict]) -> None:
    """Agrega la foto del día; si ya había una de la misma fecha de precios, la reemplaza."""
    filas = []
    if ARCHIVO.exists():
        filas = [r for r in csv.DictReader(ARCHIVO.open(encoding="utf-8")) if r["fecha_precio"] != fecha_precio]
    for e in expect:
        v = e.get("ventana_cer") or ["", ""]
        filas.append({"fecha_precio": fecha_precio, "rem_edicion": rem_edicion, "ticker": e["ticker"], "vto": e["vto"],
                      "ventana_ini": v[0], "ventana_fin": v[1],
                      **{f"cer_suba_{q}": _r(e.get(f"cer_suba_{q}")) for q in QUIENES},
                      "a3500_hoy": _r(e.get("a3500_hoy")), **{f"tc_{q}": _r(e.get(f"tc_{q}")) for q in QUIENES}})
    with ARCHIVO.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)


def _r(x):
    return "" if x is None else round(x, 6)


def _f(x: str):
    return float(x) if x not in ("", None) else None


def tablero(cer: dict[date, float], a3500: dict[date, float]) -> dict:
    """Errores de cada uno en las ventanas que ya se cumplieron."""
    if not ARCHIVO.exists():
        return {"inflacion": [], "dolar": [], "resumen": {}}
    cer_hasta, tc_hasta = max(cer), max(a3500)
    infl, dol = [], []
    for r in csv.DictReader(ARCHIVO.open(encoding="utf-8")):
        if r["ventana_fin"] and date.fromisoformat(r["ventana_fin"]) <= cer_hasta:
            a, b = date.fromisoformat(r["ventana_ini"]), date.fromisoformat(r["ventana_fin"])
            meses = (b - a).days / (365 / 12)
            real = I.valor_en(cer, b) / I.valor_en(cer, a)
            mens = lambda suba: ((1 + suba / 100) ** (1 / meses) - 1) * 100
            fila = {"fecha": r["fecha_precio"], "ticker": r["ticker"], "real": (real ** (1 / meses) - 1) * 100}
            for q in QUIENES:
                s = _f(r[f"cer_suba_{q}"])
                fila[q] = mens(s) if s is not None else None
            infl.append(fila)
        vto = date.fromisoformat(r["vto"])
        if r["tc_mercado"] and vto <= tc_hasta:
            fila = {"fecha": r["fecha_precio"], "ticker": r["ticker"], "real": I.valor_en(a3500, vto)}
            for q in QUIENES:
                fila[q] = _f(r[f"tc_{q}"])
            dol.append(fila)
    resumen = {}
    for nombre, filas, err in (("inflacion", infl, lambda p, x: abs(p - x)),
                               ("dolar", dol, lambda p, x: abs(p / x - 1) * 100)):
        res = {}
        for q in QUIENES:
            e = [err(f[q], f["real"]) for f in filas if f[q] is not None]
            res[q] = {"error_medio": sum(e) / len(e) if e else None, "casos": len(e),
                      "gana": sum(1 for f in filas if f[q] is not None and
                                  all(f[o] is None or err(f[q], f["real"]) <= err(f[o], f["real"]) for o in QUIENES))}
        resumen[nombre] = res
    return {"inflacion": infl[-60:], "dolar": dol[-60:], "resumen": resumen}
