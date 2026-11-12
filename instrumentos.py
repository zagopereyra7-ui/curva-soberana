"""Valuación de los bonos cargados por la IA (todo lo que no es canje 2020).

Tipos soportados:
- letra/bono capitalizable en pesos (LECAP/BONCAP): un pago VPV = 100*(1+TEM)^(días30/360 / 30)
- bono tasa fija en pesos con cupón (BONTE) y bonos en dólares (AO/AN): cupones por fecha, 30/360
- CER: capital ajustado por CER(t-10 hábiles) / CER(emisión-10 hábiles) -> TIR real; cero cupón o con
  cupón y cuotas (BONCER TX28/TX31), los flujos reales salen de flujos_cupon
- dollar linked (letras y bonos cero cupón): 100 USD al A3500 -> TIR en dólares

Convenciones: precio por 100 de valor nominal original; liquidación T+1; días hábiles = lunes a viernes
sin feriados nacionales (feriados.py); TIR efectiva anual con días corridos / 365.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

import calculos as c
from feriados import es_habil

UA = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}


# ---------- series del BCRA ----------
def serie_bcra(id_var: int, desde: str, hasta: str) -> dict[date, float]:
    url = f"https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/{id_var}?desde={desde}&hasta={hasta}&limit=3000"
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))
    return {date.fromisoformat(x["fecha"]): x["valor"] for x in d["results"][0]["detalle"]}


def valor_en(serie: dict[date, float], d: date) -> float:
    """Valor en la fecha d o el último anterior publicado."""
    previos = [k for k in serie if k <= d]
    if not previos:
        raise ValueError(f"Sin dato para {d}")
    return serie[max(previos)]


def restar_habiles(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if es_habil(d):
            n -= 1
    return d


# ---------- flujos ----------
def flujos_cupon(t: dict, tasa_pct: float) -> list[tuple[date, float]]:
    """Cupones 30/360 sobre el residual + amortizaciones, por cada 100 VN."""
    amort = {date.fromisoformat(a["fecha"]): a["porcentaje"] for a in t["amortizacion"]}
    fechas = sorted({date.fromisoformat(f) for f in t["fechas_pago_intereses"]} | set(amort))
    prev, residual, out = date.fromisoformat(t["fecha_emision"]), 100.0, []
    for f in fechas:
        interes = residual * tasa_pct / 100 * c.dias_30_360(prev, f) / 360
        a = amort.get(f, 0.0)
        out.append((f, interes + a))
        residual -= a
        prev = f
    return out


def vpv_capitalizable(t: dict, tem_pct: float) -> float:
    e, v = date.fromisoformat(t["fecha_emision"]), date.fromisoformat(t["fecha_vencimiento"])
    return 100 * (1 + tem_pct / 100) ** (c.dias_30_360(e, v) / 30)


def _tir(flujos: list[tuple[date, float]], precio: float, liq: date) -> tuple[float, float]:
    fut = [(c.dia_habil(f), m) for f, m in flujos if c.dia_habil(f) > liq]
    t = [(f - liq).days / 365 for f, _ in fut]
    m = [x for _, x in fut]
    y = c._tir(m, t, precio)
    vp = [mi / (1 + y) ** ti for mi, ti in zip(m, t)]
    dur = sum(v * ti for v, ti in zip(vp, t)) / sum(vp) / (1 + y)
    return y, dur


@dataclass
class Valuacion:
    ticker: str
    curva: str          # "pesos_fija", "cer", "dolar_linked", "dolares"
    precio: float
    tir: float          # efectiva anual (real para CER, en USD para DL y dólares)
    duration: float
    dias: int
    vencimiento: date
    extra: dict


def valuar(ticker: str, t: dict, precio: float, liq: date, tasas: dict, cer: dict, a3500: dict) -> Valuacion | None:
    tipo, v = t["tipo"], date.fromisoformat(t["fecha_vencimiento"])
    dias = (c.dia_habil(v) - liq).days
    if dias <= 0:
        return None
    if tipo in ("letra_capitalizable_pesos", "bono_capitalizable_pesos"):
        tem = tasas[ticker]["tem_capitalizacion_pct"]
        vpv = vpv_capitalizable(t, tem)
        y, d = _tir([(v, vpv)], precio, liq)
        return Valuacion(ticker, "pesos_fija", precio, y, d, dias, v,
                         {"tem_emision": tem, "vpv": vpv, "tem_mercado": (1 + y) ** (30 / 365) - 1})
    if tipo == "bono_tasa_fija_pesos":
        fl = flujos_cupon(t, tasas[ticker]["tasa_cupon_pct"])
        y, d = _tir(fl, precio, liq)
        return Valuacion(ticker, "pesos_fija", precio, y, d, dias, v, {"cupon": tasas[ticker]["tasa_cupon_pct"]})
    if tipo == "bono_dolares":
        fl = flujos_cupon(t, t["tasa_nominal_anual_pct"])
        y, d = _tir(fl, precio, liq)
        return Valuacion(ticker, "dolares", precio, y, d, dias, v, {"cupon": t["tasa_nominal_anual_pct"]})
    if tipo in ("letra_cer", "bono_cer"):
        rez = t["dias_habiles_rezago_cer"] or 10
        cer_base = valor_en(cer, restar_habiles(date.fromisoformat(t["fecha_emision"]), rez))
        cer_hoy = valor_en(cer, restar_habiles(liq, rez))
        precio_real = precio / (cer_hoy / cer_base)        # precio en "pesos de la emisión"
        cupon = t.get("tasa_nominal_anual_pct")
        if cupon and t.get("fechas_pago_intereses"):       # BONCER con cupón y cuotas (TX28, TX31)
            flujos = flujos_cupon(t, cupon)                  # en términos reales: capital ajustado por CER
        else:                                              # cero cupón: 100 ajustado al vencimiento
            flujos = [(v, 100.0)]
        y, d = _tir(flujos, precio_real, liq)
        return Valuacion(ticker, "cer", precio, y, d, dias, v,
                         {"coef_cer": cer_hoy / cer_base, "precio_real": precio_real, "cupon": cupon})
    if tipo in ("letra_dolar_linked", "bono_dolar_linked"):
        tc = valor_en(a3500, liq)
        precio_usd = precio / tc                           # precio en dólares al oficial de hoy
        y, d = _tir([(v, 100.0)], precio_usd, liq)
        return Valuacion(ticker, "dolar_linked", precio, y, d, dias, v, {"a3500": tc, "precio_usd": precio_usd})
    return None


def interpolar(puntos: list[tuple[int, float]], dias: int) -> float | None:
    """TIR interpolada linealmente por días; None si cae fuera del rango."""
    pts = sorted(puntos)
    if not pts or dias < pts[0][0] or dias > pts[-1][0]:
        return None
    for (d0, y0), (d1, y1) in zip(pts, pts[1:]):
        if d0 <= dias <= d1:
            return y0 + (y1 - y0) * (dias - d0) / (d1 - d0) if d1 > d0 else y0
    return pts[-1][1]
