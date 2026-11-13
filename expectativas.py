"""Mercado vs. REM: inflación y dólar esperados, medidos sobre la misma ventana.

INFLACIÓN. Lo que el mercado "espera" sale de comparar una LECAP con un bono CER del mismo plazo:
    CER(vto - 10 hábiles) / CER(liq - 10 hábiles) = ((1 + TIR fija) / (1 + TIR real)) ^ (días / 365)
El CER no es la inflación del día: el BCRA reparte la inflación del mes m-1 entre el 16 del mes m y el 15
del mes m+1. Para comparar lo mismo, la inflación mensual del REM se pasa por esa misma regla y se calcula
cuánto subiría el CER en la misma ventana. Los días con CER ya publicado usan el dato real.

DÓLAR. Un dollar linked paga el A3500 al vencimiento. Lo que espera el mercado:
    A3500 esperado = A3500 hoy × ((1 + TIR fija) / (1 + TIR dollar linked)) ^ (días / 365)
El REM da el A3500 promedio de cada mes; se toma como valor del día 15 y se interpola en el medio.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

import instrumentos as I


MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def _mes(d: date, k: int) -> str:
    m = d.month - 1 + k
    return f"{d.year + m // 12}-{m % 12 + 1:02d}"


def inflacion_rem(rem: dict, mes: str) -> float | None:
    """Inflación mensual (%) que espera el REM para 'AAAA-MM'. Más allá de los meses que pregunta usa
    los próximos 12 meses y después cada año calendario, pasados a mensual. None si la encuesta no
    llega a ese mes (no se inventa un pronóstico que nadie hizo)."""
    ipc = rem["ipc"]
    if mes in ipc:
        return ipc[mes]
    meses = sorted(k for k in ipc if re.match(r"^\d{4}-\d{2}$", k))
    if mes < meses[0]:
        return ipc[meses[0]]
    anual = None
    if "12m" in ipc:
        h = ipc["12m"]["hasta"]                      # p. ej. 'oct-27'
        fin12 = f"20{h[-2:]}-{MESES[h[:3]]:02d}"
        if mes <= fin12:
            anual = ipc["12m"]["valor"]
    if anual is None:
        anual = ipc.get(mes[:4])
    if anual is None:
        return None
    return ((1 + anual / 100) ** (1 / 12) - 1) * 100


def cer_proyectado(cer: dict[date, float], rem: dict, d: date) -> float | None:
    """CER en la fecha d: el publicado si existe; si no, el último publicado llevado hasta d con la
    inflación del REM y la regla del BCRA (día 16 del mes m al 15 del m+1 reparte la del mes m-1)."""
    ultimo = max(cer)
    if d <= ultimo:
        return I.valor_en(cer, d)
    v, x = cer[ultimo], ultimo
    while x < d:
        x += timedelta(days=1)
        ini = date(x.year, x.month, 16) if x.day >= 16 else date(x.year if x.month > 1 else x.year - 1,
                                                                    x.month - 1 if x.month > 1 else 12, 16)
        fin = date(ini.year + (ini.month // 12), ini.month % 12 + 1, 16)
        pi = inflacion_rem(rem, _mes(ini, -1))
        if pi is None:
            return None
        v *= (1 + pi / 100) ** (1 / (fin - ini).days)
    return v


def tc_rem(rem: dict, d: date) -> float | None:
    """A3500 que espera el REM para la fecha d (promedios mensuales al día 15, interpolación geométrica)."""
    tc = rem["tc"]
    pts = [(date(int(k[:4]), int(k[5:]), 15), v) for k, v in tc.items() if re.match(r"^\d{4}-\d{2}$", k)]
    if "12m" in tc:
        h = tc["12m"]["hasta"]
        pts.append((date(2000 + int(h[-2:]), MESES[h[:3]], 15), tc["12m"]["valor"]))
    for k, v in tc.items():
        if re.fullmatch(r"20\d\d", k):
            pts.append((date(int(k), 12, 15), v))
    pts = sorted(dict(pts).items())
    if d < pts[0][0] or d > pts[-1][0]:
        return None
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= d <= d1:
            w = (d - d0).days / ((d1 - d0).days or 1)
            return math.exp(math.log(v0) * (1 - w) + math.log(v1) * w)
    return pts[-1][1]


def comparar(e: dict, liq: date, cer: dict, a3500: dict, rem: dict, etiqueta: str) -> dict:
    """Agrega al punto de expectativas e (una LECAP) lo que espera el REM en la misma ventana."""
    vto = date.fromisoformat(e["vto"])
    dias = e["dias"]
    out = {}
    if e.get("tir_cer") is not None and e.get("inflacion_mensual") is not None:
        # Solo el CER que todavía no se publicó: del último publicado (pub) al que usa el bono (b). Si se
        # incluyera la parte ya conocida, el REM la "recibe" como dato exacto y la diferencia queda diluida
        # distinto en cada fila (crítica del agente 2).
        a, b, pub = I.restar_habiles(liq, 10), I.restar_habiles(vto, 10), max(cer)
        suba_mercado = ((1 + e["tir_fija"] / 100) / (1 + e["tir_cer"] / 100)) ** (dias / 365) / (I.valor_en(cer, pub) / I.valor_en(cer, a))
        proy = cer_proyectado(cer, rem, b)
        suba_rem = proy / I.valor_en(cer, pub) if proy else None
        mens = lambda g: (g ** (1 / ((b - pub).days / (365 / 12))) - 1) * 100   # promedio mensual en la ventana
        out.update({"ventana_cer": [pub.isoformat(), b.isoformat()],
                    "cer_suba_mercado": (suba_mercado - 1) * 100, "inf_mens_mercado": mens(suba_mercado),
                    f"cer_suba_{etiqueta}": (suba_rem - 1) * 100 if suba_rem else None,
                    f"inf_mens_{etiqueta}": mens(suba_rem) if suba_rem else None})
    if e.get("tir_dl") is not None:
        # El dollar linked paga el A3500 del 3er día hábil anterior al pago (resoluciones del Tesoro), así que lo
        # que "espera" el mercado es el A3500 de esa fecha, y contra eso se compara el REM.
        hoy = I.valor_en(a3500, liq)
        fecha_tc = I.restar_habiles(vto, 3)
        tc_mercado = hoy * ((1 + e["tir_fija"] / 100) / (1 + e["tir_dl"] / 100)) ** (dias / 365)
        out.update({"a3500_hoy": hoy, "tc_mercado": tc_mercado, "fecha_tc": fecha_tc.isoformat(),
                    f"tc_{etiqueta}": tc_rem(rem, fecha_tc)})
    return out

