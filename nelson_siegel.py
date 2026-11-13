"""Inflación esperada mes a mes con curvas Nelson-Siegel (como hacen bancos y bancos centrales).

IDEA. En vez de comparar cada LECAP con una recta entre dos bonos CER, se dibuja UNA curva suave por todos
los bonos de tasa fija y otra por todos los CER. Así un bono con un precio raro no mueve todo, y se puede
leer la inflación de cada mes por separado (inflación "forward"), no solo el promedio hasta un vencimiento.

CURVA. Nelson-Siegel describe la tasa de cada plazo t (en años) con 4 números:
    r(t) = b0 + b1 · f1(t) + b2 · f2(t),   f1 = (1 - e^(-t/τ)) / (t/τ),   f2 = f1 - e^(-t/τ)
    b0 = nivel de largo plazo, b1 = pendiente (corto vs. largo), b2 = joroba en el medio, τ = dónde está la joroba.
Se usan tasas continuas r = ln(1 + TIR) y como plazo la duration de Macaulay (en las LECAP y casi todos los
CER, que pagan todo al final, es exactamente el plazo al vencimiento). Para cada τ posible, los b salen por
mínimos cuadrados; se queda el τ con menos error.

INFLACIÓN DE CADA MES. Con las dos curvas:
    suba del CER hasta el plazo t = e^((r_fija(t) - r_real(t)) · t)
y la inflación del mes M es la suba del CER entre dos plazos. Regla del BCRA: la inflación del mes M entra al
CER entre el 16 del mes M+1 y el 16 del mes M+2. Y el CER que usa un bono es el de 10 días hábiles antes de
cada fecha, así que el plazo t se mide desde (liquidación - 10 hábiles).
Solo se informan meses cuyo CER todavía no está publicado (los otros ya son dato, no expectativa).

OJO. El número incluye una prima: quien compra CER paga algo por cubrirse si la inflación se dispara.
"""
from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

import numpy as np

import expectativas as X
import instrumentos as I

BASE = Path(__file__).parent
ARCHIVO = BASE / "datos" / "inflacion_mensual_historial.csv"
CAMPOS = ["fecha_precio", "rem_edicion", "mes", "mercado", "rem", "top10"]
# τ acotado a 0,1-3 años: con bonos que llegan a 1-2 años, un τ libre se va al extremo y da un "nivel de largo
# plazo" absurdo (la curva igual ajusta, pero salta de un día a otro). Probado con precios del 12/11/2026.
TAUS = np.exp(np.linspace(math.log(0.1), math.log(3), 300))


def _cargas(t: np.ndarray, tau: float) -> np.ndarray:
    x = t / tau
    f1 = (1 - np.exp(-x)) / x
    return np.column_stack([np.ones_like(t), f1, f1 - np.exp(-x)])


def ajustar(pts: list[tuple[float, float]]) -> dict | None:
    """pts = [(plazo en años, TIR efectiva anual en %)]. Devuelve la curva y el error de cada bono."""
    if len(pts) < 4:
        return None
    t = np.array([p[0] for p in pts], dtype=float)
    r = np.log1p(np.array([p[1] for p in pts], dtype=float) / 100)
    mejor = None
    for tau in TAUS:
        A = _cargas(t, tau)
        b, *_ = np.linalg.lstsq(A, r, rcond=None)
        sse = float(((A @ b - r) ** 2).sum())
        if mejor is None or sse < mejor[0]:
            mejor = (sse, tau, b)
    _, tau, b = mejor
    curva = {"b0": float(b[0]), "b1": float(b[1]), "b2": float(b[2]), "tau": float(tau)}
    ajuste = [tir_en(curva, ti) for ti in t]
    curva["errores_pb"] = [round((p[1] - a) * 100, 1) for p, a in zip(pts, ajuste)]
    curva["error_medio_pb"] = round(float(np.sqrt(np.mean(np.square(curva["errores_pb"])))), 1)
    curva["plazo_max"] = float(t.max())
    return curva


def tasa_continua(c: dict, t: float) -> float:
    if t <= 1e-9:
        return c["b0"] + c["b1"]
    x = t / c["tau"]
    f1 = (1 - math.exp(-x)) / x
    return c["b0"] + c["b1"] * f1 + c["b2"] * (f1 - math.exp(-x))


def tir_en(c: dict, t: float) -> float:
    """TIR efectiva anual (%) que da la curva al plazo t."""
    return (math.exp(tasa_continua(c, t)) - 1) * 100


def suba_cer(fija: dict, real: dict, t: float) -> float:
    """Cuánto espera el mercado que suba el CER en los próximos t años (1.05 = 5%)."""
    return math.exp((tasa_continua(fija, t) - tasa_continua(real, t)) * t)


def _mas_meses(k: str, n: int) -> str:
    y, m = int(k[:4]), int(k[5:]) - 1 + n
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def inflacion_mensual(fija: dict, real: dict, liq: date, cer_publicado_hasta: date, meses: int = 18) -> list[dict]:
    """Inflación de cada mes según las curvas, para los meses cuyo CER todavía no se publicó."""
    a = I.restar_habiles(liq, 10)
    horizonte = min(fija["plazo_max"], real["plazo_max"])
    out = []
    mes = f"{liq.year}-{liq.month:02d}"
    mes = _mas_meses(mes, -2)
    for _ in range(meses + 3):
        mes = _mas_meses(mes, 1)
        d0 = date.fromisoformat(_mas_meses(mes, 1) + "-16")
        d1 = date.fromisoformat(_mas_meses(mes, 2) + "-16")
        if d0 < cer_publicado_hasta:
            continue                                     # ya publicado: es dato, no expectativa
        t0, t1 = (d0 - a).days / 365, (d1 - a).days / 365
        if t1 > horizonte:
            break                                        # más allá del último bono: no se extrapola
        g = suba_cer(fija, real, t1) / suba_cer(fija, real, t0)
        out.append({"mes": mes, "cer_desde": d0.isoformat(), "cer_hasta": d1.isoformat(),
                    "mercado": (g - 1) * 100, "t0": t0, "t1": t1})
        if len(out) >= meses:
            break
    return out


def calcular(filas: list[dict], liq: date, cer: dict, rem: dict | None) -> dict | None:
    """Ajusta las dos curvas con los bonos que pasaron los controles y arma la tabla mes a mes."""
    def puntos(curva):
        sel = [f for f in filas if f["curva"] == curva and not f["revisar"] and f["dias"] >= 7]
        return sel, [(f["md"] * (1 + f["tir"] / 100), f["tir"]) for f in sel]
    bf, pf = puntos("pesos_fija")
    br, pr = puntos("cer")
    fija, real = ajustar(pf), ajustar(pr)
    if not fija or not real:
        return None
    for curva, bonos, pts in ((fija, bf, pf), (real, br, pr)):
        curva["bonos"] = [{"ticker": f["ticker"], "plazo": round(p[0], 4), "tir": round(f["tir"], 3), "error_pb": e}
                          for f, p, e in zip(bonos, pts, curva.pop("errores_pb"))]
    meses = inflacion_mensual(fija, real, liq, max(cer))
    if rem:
        for m in meses:
            m["rem"] = X.inflacion_rem(rem["total"], m["mes"])
            m["top10"] = X.inflacion_rem(rem["top10"], m["mes"])
    return {"fija": fija, "real": real, "meses": meses, "desde_cer": I.restar_habiles(liq, 10).isoformat()}


def guardar(fecha_precio: str, rem_edicion: str | None, res: dict) -> None:
    """Foto del día (reemplaza la de la misma fecha) para ver después quién acertó cada mes."""
    filas = []
    if ARCHIVO.exists():
        filas = [r for r in csv.DictReader(ARCHIVO.open(encoding="utf-8")) if r["fecha_precio"] != fecha_precio]
    r4 = lambda v: "" if v is None else round(v, 4)
    for m in res["meses"]:
        filas.append({"fecha_precio": fecha_precio, "rem_edicion": rem_edicion or "", "mes": m["mes"],
                      "mercado": r4(m["mercado"]), "rem": r4(m.get("rem")), "top10": r4(m.get("top10"))})
    with ARCHIVO.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)


def inflacion_real(cer: dict, mes: str) -> float | None:
    """Inflación del mes según el propio CER (16 del mes+1 al 16 del mes+2), si ya está publicada."""
    d0 = date.fromisoformat(_mas_meses(mes, 1) + "-16")
    d1 = date.fromisoformat(_mas_meses(mes, 2) + "-16")
    if d1 > max(cer) or d0 < min(cer):
        return None
    return (I.valor_en(cer, d1) / I.valor_en(cer, d0) - 1) * 100


def tablero(cer: dict) -> list[dict]:
    """Para cada mes ya conocido: error promedio de cada pronóstico guardado (puntos porcentuales)."""
    if not ARCHIVO.exists():
        return []
    por_mes: dict[str, list[dict]] = {}
    for r in csv.DictReader(ARCHIVO.open(encoding="utf-8")):
        por_mes.setdefault(r["mes"], []).append(r)
    out = []
    for mes, rs in sorted(por_mes.items()):
        real = inflacion_real(cer, mes)
        if real is None:
            continue
        fila = {"mes": mes, "real": real, "pronosticos": len(rs)}
        for q in ("mercado", "rem", "top10"):
            v = [float(r[q]) for r in rs if r[q]]
            fila[q] = sum(abs(x - real) for x in v) / len(v) if v else None
        out.append(fila)
    return out
