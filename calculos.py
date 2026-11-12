"""Flujos, TIR, duration y paridad de bonos soberanos en dólares.

Convenciones (ver también la sección "Supuestos" de la página):
- Todo por cada 100 de valor nominal ORIGINAL, como cotiza BYMA.
- Precio de mercado = precio sucio (incluye intereses corridos).
- Intereses 30/360 semestrales; el cupón de cada semestre usa la tasa vigente al inicio.
- Fechas de pago que caen sábado, domingo o feriado pasan al siguiente día hábil (feriados.py).
- TIR efectiva anual, descontando con días corridos / 365.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from feriados import es_habil


def _d(s: str) -> date:
    return date.fromisoformat(s)


def sumar_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    return date(d.year + m // 12, m % 12 + 1, d.day)


def dia_habil(d: date) -> date:
    """Si d cae sábado, domingo o feriado nacional, pasa al siguiente día hábil."""
    while not es_habil(d):
        d += timedelta(days=1)
    return d


def dias_30_360(a: date, b: date) -> int:
    d1, d2 = min(a.day, 30), b.day
    if d1 == 30 and d2 == 31:
        d2 = 30
    return 360 * (b.year - a.year) + 30 * (b.month - a.month) + (d2 - d1)


@dataclass
class Flujo:
    fecha: date          # fecha teórica (9/1 o 9/7)
    fecha_pago: date     # corrida a día hábil
    interes: float
    amortizacion: float
    residual_antes: float

    @property
    def total(self) -> float:
        return self.interes + self.amortizacion


def cronograma_amortizacion(am: dict) -> dict[date, float]:
    n, primera, ultima = am["cuotas"], _d(am["primera"]), _d(am["ultima"])
    fechas = [sumar_meses(primera, 6 * i) for i in range(n)]
    if fechas[-1] != ultima:
        raise ValueError(f"Las {n} cuotas desde {primera} no terminan en {ultima}")
    if am.get("primera_pct"):
        resto = (100 - am["primera_pct"]) / (n - 1)
        pcts = [am["primera_pct"]] + [resto] * (n - 1)
    else:
        pcts = [100 / n] * n
    return dict(zip(fechas, pcts))


def tasa_vigente(tasas: list, inicio_periodo: date) -> float:
    vigente = None
    for desde, tasa in tasas:
        if _d(desde) <= inicio_periodo:
            vigente = tasa
    if vigente is None:
        raise ValueError(f"Sin tasa para {inicio_periodo}")
    return vigente


def flujos(familia: dict) -> list[Flujo]:
    amort = cronograma_amortizacion(familia["amortizacion"])
    ultima = max(amort)
    fecha, residual, out = date(2021, 7, 9), 100.0, []
    inicio = _d(familia["tasas"][0][0])
    while fecha <= ultima:
        tasa = tasa_vigente(familia["tasas"], inicio)
        dias = dias_30_360(inicio, fecha) if inicio.day != 9 else 180
        interes = residual * tasa / 100 * dias / 360
        a = amort.get(fecha, 0.0)
        out.append(Flujo(fecha, dia_habil(fecha), interes, a, residual))
        residual -= a
        inicio, fecha = fecha, sumar_meses(fecha, 6)
    return out


@dataclass
class Resultado:
    precio: float
    tir: float
    duration_mod: float
    residual: float
    intereses_corridos: float
    valor_tecnico: float
    paridad: float
    proximo_pago: date
    vencimiento: date


def valuar(familia: dict, precio: float, liquidacion: date) -> Resultado:
    todos = flujos(familia)
    futuros = [f for f in todos if f.fecha_pago > liquidacion]
    if not futuros:
        raise ValueError("Bono vencido")
    prox = futuros[0]
    inicio_cupon = sumar_meses(prox.fecha, -6)
    tasa = tasa_vigente(familia["tasas"], inicio_cupon)
    residual = prox.residual_antes
    corridos = residual * tasa / 100 * dias_30_360(inicio_cupon, liquidacion) / 360 if liquidacion > inicio_cupon else 0.0
    t = [(f.fecha_pago - liquidacion).days / 365 for f in futuros]
    c = [f.total for f in futuros]
    tir = _tir(c, t, precio)
    vp = [ci / (1 + tir) ** ti for ci, ti in zip(c, t)]
    macaulay = sum(v * ti for v, ti in zip(vp, t)) / sum(vp)
    vt = residual + corridos
    return Resultado(precio, tir, macaulay / (1 + tir), residual, corridos, vt,
                     precio / vt, prox.fecha_pago, futuros[-1].fecha_pago)


def _vp(c, t, y):
    return sum(ci / (1 + y) ** ti for ci, ti in zip(c, t))


def _tir(c, t, precio, lo=-0.9, hi=5.0):
    if not (_vp(c, t, hi) < precio < _vp(c, t, lo)):
        raise ValueError("Precio fuera de rango para calcular TIR")
    for _ in range(200):
        mid = (lo + hi) / 2
        if _vp(c, t, mid) > precio:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def liquidacion_t1(hoy: date) -> date:
    return dia_habil(hoy + timedelta(days=1))
