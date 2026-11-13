"""¿Quién le pega más: el mercado, el REM o el Top 10 del REM?

Cada día se guarda, para cada LECAP/BONCAP, cuánto esperaba cada uno que subiera el CER en su ventana
y qué A3500 esperaba al vencimiento (datos/expectativas_historial.csv). Cuando el CER de fin de ventana
ya está publicado, o el A3500 del vencimiento ya existe, se compara con lo que realmente pasó.

El error se mide en puntos porcentuales de inflación mensual promedio (CER) y en % del dólar (A3500).
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import expectativas as X
import instrumentos as I

BASE = Path(__file__).parent
ARCHIVO = BASE / "datos" / "expectativas_historial.csv"
CAMPOS = ["fecha_precio", "rem_edicion", "ticker", "vto", "ventana_ini", "ventana_fin",
          "cer_suba_mercado", "cer_suba_rem", "cer_suba_top10", "a3500_hoy", "tc_mercado", "tc_rem", "tc_top10"]
QUIENES = ("mercado", "rem", "top10")
MIN_EDICIONES = 6   # con menos ediciones del REM puntuadas no se declara ganador
DEFINICION_DESDE = "2026-11-13"   # filas anteriores usan la definición vieja (ventana con CER ya publicado, TY30P, dólar al vto.)


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
    # Misma información: para cada edición del REM, la foto del mercado del día en que cerró esa encuesta
    # (≤ 4 días antes). La edición del REM se guarda con la fecha en que se BAJÓ el archivo (una semana después),
    # así que se busca la foto por fecha, no por la edición guardada en la fila (agente 2).
    import nelson_siegel as N
    filas = [r for r in csv.DictReader(ARCHIVO.open(encoding="utf-8")) if r["fecha_precio"] >= DEFINICION_DESDE]
    elegidas = []
    for f in sorted((BASE / "datos" / "rem").glob("*.json")):
        ed = f.stem
        cierre = N.cierre_encuesta(ed).isoformat()
        fechas = sorted({r["fecha_precio"] for r in filas if r["fecha_precio"] <= cierre})
        if not fechas or (date.fromisoformat(cierre) - date.fromisoformat(fechas[-1])).days > 4:
            continue
        rem_ed = json.loads(f.read_text(encoding="utf-8"))
        elegidas += [(r, ed, rem_ed) for r in filas if r["fecha_precio"] == fechas[-1]]
    for r, ed, rem_ed in elegidas:
        if r["ventana_fin"] and date.fromisoformat(r["ventana_fin"]) <= cer_hasta:
            a, b = date.fromisoformat(r["ventana_ini"]), date.fromisoformat(r["ventana_fin"])
            meses = (b - a).days / (365 / 12)
            real = I.valor_en(cer, b) / I.valor_en(cer, a)
            mens = lambda suba: ((1 + suba / 100) ** (1 / meses) - 1) * 100
            fila = {"fecha": r["fecha_precio"], "edicion": ed, "ticker": r["ticker"], "real": (real ** (1 / meses) - 1) * 100}
            fila["mercado"] = mens(_f(r["cer_suba_mercado"])) if r["cer_suba_mercado"] else None
            # REM de ESA edición, proyectado con el CER que se conocía ese día (no con el que se conoce hoy)
            cer_dia = {k: v for k, v in cer.items() if k <= a}
            for q, clave in (("rem", "total"), ("top10", "top10")):
                proy = X.cer_proyectado(cer_dia, rem_ed[clave], b)
                fila[q] = mens((proy / I.valor_en(cer_dia, a) - 1) * 100) if proy else None
            infl.append(fila)
        fecha_tc = I.restar_habiles(date.fromisoformat(r["vto"]), 3)   # A3500 que paga el dollar linked
        if r["tc_mercado"] and fecha_tc <= tc_hasta:
            fila = {"fecha": r["fecha_precio"], "edicion": ed, "ticker": r["ticker"], "real": I.valor_en(a3500, fecha_tc),
                    "mercado": _f(r["tc_mercado"]), "rem": X.tc_rem(rem_ed["total"], fecha_tc), "top10": X.tc_rem(rem_ed["top10"], fecha_tc)}
            dol.append(fila)
    resumen = {}
    for nombre, filas, err in (("inflacion", infl, lambda p, x: p - x),
                               ("dolar", dol, lambda p, x: (p / x - 1) * 100)):
        res = {}
        for q in QUIENES:
            e = [err(f[q], f["real"]) for f in filas if f[q] is not None]
            res[q] = {"error_medio": sum(abs(x) for x in e) / len(e) if e else None,
                      "sesgo": sum(e) / len(e) if e else None,          # con signo: + = esperaba de más
                      "casos": len(e),
                      "gana": sum(1 for f in filas if f[q] is not None and
                                  all(f[o] is None or abs(err(f[q], f["real"])) <= abs(err(f[o], f["real"])) for o in QUIENES))}
        # Las LECAP de una misma edición tienen ventanas anidadas (todas cubren el mismo primer mes): no son
        # casos independientes. Lo que cuenta es cuántas EDICIONES distintas hay.
        res["ediciones"] = len({f["edicion"] for f in filas})
        res["hay_ganador"] = res["ediciones"] >= MIN_EDICIONES
        resumen[nombre] = res
    return {"inflacion": infl[-60:], "dolar": dol[-60:], "resumen": resumen, "min_ediciones": MIN_EDICIONES}
