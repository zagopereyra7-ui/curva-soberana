"""TAMAR que espera el mercado, con los bonos a tasa TAMAR (TMF27, M30A7, TML27, TMG27, TMF28, TMG28).

CÓMO PAGAN (resoluciones del Tesoro, p. ej. RESFC del 11/2/2026 para el TMF27): al vencimiento, el capital
capitalizado mensualmente a la "TAMAR TEM":
    VPV = 100 × (1 + TAMAR TEM) ^ (días 30/360 entre emisión y vencimiento / 30)
    TAMAR TEM = [(1 + (TAMAR + MARGEN) × 32/365) ^ (365/32)] ^ (1/12) − 1          (*)
    TAMAR = promedio aritmético simple de la TAMAR de bancos privados (BCRA) desde 10 días hábiles antes de la
            emisión hasta 10 días hábiles antes del vencimiento. MARGEN: fijado en la licitación de emisión.
(*) La fórmula viene como imagen en el Boletín Oficial y no se pudo leer del texto: se usa la convención estándar
    de los bonos TAMAR del Tesoro. Es un supuesto explícito.

QUÉ ESPERA EL MERCADO (mismo método que la inflación implícita: interpolación exacta, sin modelo):
  1. Tasa fija al mismo plazo: la LECAP/BONCAP gemela (mismo vencimiento) o la interpolación entre las vecinas.
     Si el bono vence después de la última LECAP, se usa la tasa del tramo largo (TY30P): INDICATIVO.
  2. VPV que paga el mercado = precio × (1 + tasa fija)^plazo  →  TAMAR TEM  →  TAMAR promedio de toda la ventana.
  3. Parte ya conocida: promedio de la TAMAR publicada desde el inicio de la ventana. Lo que falta es la TAMAR
     promedio que el mercado espera para los días hábiles que quedan.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import calculos as c
import instrumentos as I
import nelson_siegel as N

ID_TAMAR = 44   # BCRA: Tasa de interés TAMAR de bancos privados (% n.a.)
# Margen de la licitación de EMISIÓN (queda fijo hasta el vencimiento). Fuente: comunicados de la Secretaría de
# Finanzas en argentina.gob.ar ("Resultado de la licitación..."): TMF27 11/2/2026; TMF28 y TMG27 (canje);
# TMG28 28/4/2026; TML27; M30A7 licitación del 11/11/2026.
MARGENES = {"TMF27": 6.50, "TML27": 5.40, "TMG27": 6.00, "TMF28": 6.50, "TMG28": 6.90, "M30A7": 2.50}


def tem(tamar_mas_margen_pct: float) -> float:
    return ((1 + tamar_mas_margen_pct / 100 * 32 / 365) ** (365 / 32)) ** (1 / 12) - 1


def tasa_desde_tem(t: float) -> float:
    """Inversa de tem(): TAMAR + margen (% n.a.) que da esa TEM."""
    return (((1 + t) ** 12) ** (32 / 365) - 1) * 365 / 32 * 100


def habiles(d0: date, d1: date) -> int:
    """Días hábiles en (d0, d1]."""
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        n += I.es_habil(d)
    return n


def calcular(filas: list[dict], titulos: dict, precios: dict, liq: date, cer: dict, ns: dict | None) -> dict | None:
    """titulos: {ticker: ficha de la IA}; precios: {ticker: dict de data912}."""
    if not titulos:
        return None
    desde = min(I.restar_habiles(date.fromisoformat(t["fecha_emision"]), 12) for t in titulos.values())
    serie = I.serie_bcra(ID_TAMAR, desde.isoformat(), liq.isoformat())
    if not serie:
        return None
    ult = max(serie)
    pf, _ = N.puntos(filas, liq, cer)
    gemelas = {p["vto"]: p for p in pf if p["origen"] == "pesos_fija"}
    largo = (ns or {}).get("largo")
    fija = (ns or {}).get("fija")
    out = []
    for tk, t in sorted(titulos.items(), key=lambda x: x[1]["fecha_vencimiento"]):
        p = precios.get(tk)
        if tk not in MARGENES or not p or not p.get("c"):
            continue
        em, vto = date.fromisoformat(t["fecha_emision"]), date.fromisoformat(t["fecha_vencimiento"])
        anios = (c.dia_habil(vto) - liq).days / 365
        if anios <= 7 / 365:
            continue
        # 1) tasa fija al mismo plazo
        gem = gemelas.get(t["fecha_vencimiento"])
        indicativo = False
        if gem:
            r, fuente = math.log1p(gem["tir"] / 100), f"{gem['ticker']} (gemela)"
        else:
            r = N.tasa_fija_interpolada(pf, anios)
            fuente = "interpolación entre LECAP"
            if r is None and largo and fija:
                fw = math.log1p(largo["bonte"]["forward_pct"] / 100)
                r = -math.log(N._df(fija, fw, anios)) / anios
                fuente, indicativo = "tramo largo (TY30P)", True
            if r is None:
                continue
        # 2) TAMAR promedio de toda la ventana que paga el precio de mercado
        vpv = p["c"] * math.exp(r * anios)
        dias360 = c.dias_30_360(em, vto)
        tem_mkt = (vpv / 100) ** (30 / dias360) - 1
        tamar_prom = tasa_desde_tem(tem_mkt) - MARGENES[tk]
        # 3) parte conocida y parte futura
        a, b = I.restar_habiles(em, 10), I.restar_habiles(vto, 10)
        conocidos = [v for d, v in serie.items() if a <= d <= min(b, ult)]
        n_fut = habiles(min(b, ult), b) if b > ult else 0
        if not conocidos or n_fut <= 0:
            continue
        n_tot = len(conocidos) + n_fut
        futura = (tamar_prom * n_tot - sum(conocidos)) / n_fut
        out.append({"ticker": tk, "vto": t["fecha_vencimiento"], "emision": t["fecha_emision"], "margen": MARGENES[tk],
                    "precio": p["c"], "tasa_fija_de": fuente, "tasa_fija_pct": (math.exp(r) - 1) * 100, "indicativo": indicativo,
                    "vpv": vpv, "tem": tem_mkt * 100, "tamar_promedio_ventana": tamar_prom,
                    "tamar_conocida_promedio": sum(conocidos) / len(conocidos), "dias_conocidos": len(conocidos),
                    "dias_futuros": n_fut, "tamar_futura": futura, "ventana": [a.isoformat(), b.isoformat()]})
    return {"bonos": out, "tamar_hoy": serie[ult], "tamar_fecha": ult.isoformat(),
            "tamar_30d": sum(v for d, v in serie.items() if d > ult - timedelta(days=30)) /
                         max(1, sum(1 for d in serie if d > ult - timedelta(days=30)))}
