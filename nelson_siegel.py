"""Inflación esperada mes a mes: curva Nelson-Siegel de tasa fija + inflación por tramo de los bonos CER.

PASO 1 - CURVA DE TASA FIJA (Nelson-Siegel). Una curva suave por todas las LECAP/BONCAP:
    r(t) = b0 + b1 · f1(t) + b2 · f2(t),   f1 = (1 - e^(-t/τ)) / (t/τ),   f2 = f1 - e^(-t/τ)
con tasas continuas r = ln(1 + TIR) y t = días al vencimiento / 365. Cada bono pesa según cuánto se mueve su
precio por un error de tasa (duration) dividido por su spread entre puntas: así se ajusta en PRECIOS y un bono
de precio firme pesa más. Entran también los CER cuyo ajuste ya está todo publicado (son pesos fijos
disfrazados). Quedan fuera los bonos con cupón (TY30P, TX28, TX31): poner su TIR en la duration como si
pagaran todo al final no es válido, y el TY30P solo, así puesto, definía toda la curva después de jun-27
(crítica del agente 2). La curva fija llega hasta la última LECAP/BONCAP: no se extrapola.

PASO 2 - CUÁNTA INFLACIÓN FUTURA PAGA CADA BONO CER. Contra la tasa fija al mismo plazo:
    suba total del CER = e^(r_fija(t) · t) / (1 + TIR real)^t
A eso se le saca la parte del CER que ya se publicó. Lo que queda (L) es la suba del CER entre el último
publicado y el que usa el bono al vencer (vencimiento - 10 hábiles): inflación que todavía no ocurrió.

PASO 3 - INFLACIÓN DE CADA MES (por tramo). Regla del BCRA: la inflación del mes M se reparte día a día en el
CER del 16 del mes M+1 al 15 del M+2. Entonces el L de cada bono es exactamente la suma de los meses que
cubre su ventana, cada uno por la fracción de días que cae adentro:
    ln(L_i) = Σ_k  fracción_ik · ln(1 + π_k)
Las incógnitas son las π_k (una por mes). Se eligen las que mejor valúan todos los bonos, midiendo el error de
cada uno en "spreads" de su precio, más una penalidad suave a los saltos entre meses consecutivos (sin ella,
con bonos que se contradicen, los meses saltan de 0,8% a 2,5%). Es un sistema lineal: solución exacta.
Ventaja sobre una curva de 4 números: respeta las fechas exactas de cada bono y deja que el bono que mira solo
noviembre (X29E7) defina noviembre.

BANDA. Se repite con más y menos penalidad y sacando un bono por vez (de cualquiera de las dos curvas). Es
la sensibilidad a los supuestos, no un intervalo estadístico. Si alguna versión no llega a un mes, se avisa.

OJO. El número del mercado incluye primas (cobertura de inflación, liquidez) que todavía no están medidas.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import instrumentos as I

BASE = Path(__file__).parent
ARCHIVO = BASE / "datos" / "inflacion_mensual_historial.csv"
CAMPOS = ["fecha_precio", "rem_edicion", "mes", "mercado", "banda_min", "banda_max", "rem", "top10"]
_grilla = lambda lo, hi: np.exp(np.linspace(math.log(lo), math.log(hi), 200))
TAUS = _grilla(0.05, 3)
TAUS_BANDA = [_grilla(0.05, 1), _grilla(0.3, 3)]
# penalidad: un salto de SUAVIDAD (en inflación mensual, como fracción) entre dos meses "cuesta" lo mismo que
# valuar mal un bono por un spread. 0,3 pp por mes de salto ≈ 1 spread.
SUAVIDAD = 0.003
SUAVIDAD_BANDA = [0.0015, 0.006]


# ---------------------------------------------------------------- Nelson-Siegel
def _cargas(t: np.ndarray, tau: float) -> np.ndarray:
    x = t / tau
    f1 = (1 - np.exp(-x)) / x
    return np.column_stack([np.ones_like(t), f1, f1 - np.exp(-x)])


def ajustar(pts: list[tuple], taus=TAUS) -> dict | None:
    """pts = [(plazo en años, TIR efectiva anual %, peso)]. Mínimos cuadrados ponderados; para cada τ los b
    salen exactos y se queda el τ con menos error."""
    if len(pts) < 4:
        return None
    t = np.array([p[0] for p in pts], dtype=float)
    r = np.log1p(np.array([p[1] for p in pts], dtype=float) / 100)
    w = np.array([p[2] if len(p) > 2 else 1.0 for p in pts], dtype=float)
    mejor = None
    for tau in taus:
        A = _cargas(t, tau)
        b, *_ = np.linalg.lstsq(A * w[:, None], r * w, rcond=None)
        sse = float((((A @ b - r) * w) ** 2).sum())
        if mejor is None or sse < mejor[0]:
            mejor = (sse, tau, b)
    _, tau, b = mejor
    curva = {"b0": float(b[0]), "b1": float(b[1]), "b2": float(b[2]), "tau": float(tau),
             "tau_en_borde": bool(abs(tau - taus[0]) < 1e-9 or abs(tau - taus[-1]) < 1e-9)}
    curva["errores_pb"] = [round((p[1] - tir_en(curva, ti)) * 100, 1) for p, ti in zip(pts, t)]
    e = np.array(curva["errores_pb"])
    curva["error_medio_pb"] = round(float(np.sqrt((w ** 2 * e ** 2).sum() / (w ** 2).sum())), 1)
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


def _mas_meses(k: str, n: int) -> str:
    y, m = int(k[:4]), int(k[5:]) - 1 + n
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def ventana_mes(mes: str) -> tuple[date, date]:
    """La inflación del mes M sube el CER del 15 del mes M+1 (exclusive) al 15 del M+2 (inclusive)."""
    return date.fromisoformat(_mas_meses(mes, 1) + "-15"), date.fromisoformat(_mas_meses(mes, 2) + "-15")


# ---------------------------------------------------------------- bonos
def puntos(filas: list[dict], liq: date, cer: dict) -> tuple[list[dict], list[dict]]:
    """Separa los bonos: (para la curva fija, CER con ajuste todavía no publicado)."""
    a = I.restar_habiles(liq, 10)
    publicado = max(cer)
    fija, real = [], []
    for f in filas:
        if f["revisar"] or f["dias"] < 7:
            continue
        if (f.get("calc") or {}).get("cupon"):
            continue                                     # con cupón: ver docstring
        spread = max(f.get("spread") or 0.5, 0.05)       # % del precio; mínimo 0,05% para no dividir por ~0
        anios = f["dias"] / 365
        p = {"ticker": f["ticker"], "plazo": anios, "tir": f["tir"], "spread": spread,
             "peso": anios / (1 + f["tir"] / 100) / spread, "origen": f["curva"], "vto": f["vto"]}
        if f["curva"] == "pesos_fija":
            fija.append(p)
        elif f["curva"] == "cer":
            b = I.restar_habiles(date.fromisoformat(f["vto"]), 10)      # CER que usa el bono al vencer
            if b <= publicado:
                g = I.valor_en(cer, b) / I.valor_en(cer, a)              # todo el ajuste ya se conoce
                tir_pesos = (((1 + f["tir"] / 100) ** anios * g) ** (1 / anios) - 1) * 100
                fija.append({**p, "tir": tir_pesos, "peso": anios / (1 + tir_pesos / 100) / spread,
                             "origen": "cer_conocido", "tir_real": f["tir"]})
            else:
                real.append({**p, "cer_hasta": b})
    return fija, real


def inflacion_futura(fija: dict, pr: list[dict], liq: date, cer: dict) -> list[dict]:
    """Para cada bono CER: suba del CER que paga DESPUÉS de lo ya publicado (ver paso 2).
    Solo bonos dentro del tramo que cubre la curva fija (no se extrapola la tasa fija)."""
    a, pub = I.restar_habiles(liq, 10), max(cer)
    conocido = I.valor_en(cer, pub) / I.valor_en(cer, a)
    out = []
    for p in pr:
        t = p["plazo"]                                   # inversión: de la liquidación al vencimiento
        if t > fija["plazo_max"] + 1e-9:
            continue
        total = math.exp(tasa_continua(fija, t) * t) / (1 + p["tir"] / 100) ** t
        dias = (p["cer_hasta"] - pub).days               # CER no publicado que cubre
        if dias < 7:
            continue
        out.append({**p, "h": dias / 365, "dias_futuros": dias, "suba_futura": total / conocido})
    return out


# ---------------------------------------------------------------- inflación por tramo
def meses_por_tramo(pts: list[dict], cer: dict, suavidad: float = SUAVIDAD) -> tuple[list[dict], list[float]]:
    """Resuelve la inflación de cada mes (paso 3). Devuelve (meses, error de cada bono en spreads)."""
    if len(pts) < 2:
        return [], []
    pub = max(cer)
    fin = max(p["cer_hasta"] for p in pts)
    meses, mes = [], _mas_meses(f"{pub.year}-{pub.month:02d}", -2)
    while True:
        d0, d1 = ventana_mes(mes)
        if d1 <= pub:
            mes = _mas_meses(mes, 1); continue
        if d0 >= fin:
            break
        meses.append((mes, max(d0, pub), d1))
        mes = _mas_meses(mes, 1)
    K = len(meses)
    filas, obj, pesos = [], [], []
    for p in pts:                                        # una ecuación por bono
        fila = []
        for _, d0, d1 in meses:
            dentro = (min(d1, p["cer_hasta"]) - d0).days
            fila.append(max(dentro, 0) / (ventana_mes(_)[1] - ventana_mes(_)[0]).days)
        filas.append(fila); obj.append(math.log(p["suba_futura"])); pesos.append(1 / (p["spread"] / 100))
    for k in range(K - 1):                               # penalidad a los saltos entre meses
        fila = [0.0] * K; fila[k], fila[k + 1] = -1.0, 1.0
        filas.append(fila); obj.append(0.0); pesos.append(1 / suavidad)
    A, y, w = np.array(filas), np.array(obj), np.array(pesos)
    x, *_ = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)
    errores = [float((A[i] @ x - y[i]) / (p["spread"] / 100)) for i, p in enumerate(pts)]
    # el último mes solo se informa si algún bono cubre al menos la mitad de su ventana
    out = []
    for k, (mes, d0, d1) in enumerate(meses):
        cubierto = max(min(d1, p["cer_hasta"]) for p in pts)
        if (cubierto - d0).days < 0.5 * (d1 - d0).days:
            break
        out.append({"mes": mes, "cer_desde": ventana_mes(mes)[0].isoformat(), "cer_hasta": ventana_mes(mes)[1].isoformat(),
                    "mercado": (math.exp(x[k]) - 1) * 100,
                    "bonos_que_lo_cubren": sum(1 for p in pts if p["cer_hasta"] > d0)})
    return out, errores


def _todo(pf, pr, liq, cer, taus=TAUS, suavidad=SUAVIDAD):
    fija = ajustar([(p["plazo"], p["tir"], p["peso"]) for p in pf], taus)
    if not fija:
        return None, [], [], []
    pts = inflacion_futura(fija, pr, liq, cer)
    meses, err = meses_por_tramo(pts, cer, suavidad)
    return fija, meses, pts, err


def calcular(filas: list[dict], liq: date, cer: dict, rem: dict | None) -> dict | None:
    """Curva fija, inflación por tramo, banda de sensibilidad y REM de los meses que pregunta."""
    pf, pr = puntos(filas, liq, cer)
    publicado = max(cer)
    fija, meses, pts, err = _todo(pf, pr, liq, cer)
    if not fija or not meses:
        return None
    fija["bonos"] = [{"ticker": p["ticker"], "plazo": round(p["plazo"], 4), "tir": round(p["tir"], 3),
                      "spread": round(p["spread"], 3), "origen": p["origen"], "error_pb": e,
                      **({"tir_real": round(p["tir_real"], 3)} if "tir_real" in p else {})}
                     for p, e in zip(pf, fija.pop("errores_pb"))]
    cer_bonos = [{"ticker": p["ticker"], "vto": p["vto"], "tir_real": round(p["tir"], 3), "spread": round(p["spread"], 3),
                  "cer_hasta": p["cer_hasta"].isoformat(), "dias_futuros": p["dias_futuros"],
                  "suba_futura_pct": round((p["suba_futura"] - 1) * 100, 3),
                  "mensual_promedio": round((p["suba_futura"] ** (30.4 / p["dias_futuros"]) - 1) * 100, 3),
                  "error_spreads": round(e, 2)} for p, e in zip(pts, err)]
    afuera = [{"ticker": p["ticker"], "motivo": "vence después de la última LECAP/BONCAP (no hay tasa fija a ese plazo)"}
              for p in pr if p["plazo"] > fija["plazo_max"] + 1e-9]

    variantes = [_todo(pf, pr, liq, cer, taus=tx)[1] for tx in TAUS_BANDA]
    variantes += [_todo(pf, pr, liq, cer, suavidad=s)[1] for s in SUAVIDAD_BANDA]
    variantes += [_todo(pf[:i] + pf[i + 1:], pr, liq, cer)[1] for i in range(len(pf))]
    variantes += [_todo(pf, pr[:i] + pr[i + 1:], liq, cer)[1] for i in range(len(pr))]
    for m in meses:
        llegan = [v["mercado"] for var in variantes for v in var if v["mes"] == m["mes"]]
        vals = [m["mercado"]] + llegan
        m["banda_min"], m["banda_max"] = min(vals), max(vals)
        m["versiones"] = len(vals)
        m["banda_completa"] = len(llegan) == len(variantes)
        if rem:   # solo los meses que el REM pregunta uno por uno; el resto sería un número armado por nosotros
            m["rem"] = rem["total"]["ipc"].get(m["mes"])
            m["top10"] = rem["top10"]["ipc"].get(m["mes"])
    a = I.restar_habiles(liq, 10)
    return {"fija": fija, "cer_bonos": cer_bonos, "cer_afuera": afuera, "meses": meses, "desde_cer": a.isoformat(),
            "cer_conocido_pct": (I.valor_en(cer, publicado) / I.valor_en(cer, a) - 1) * 100,
            "cer_publicado_hasta": publicado.isoformat(), "rem_edicion": rem["edicion"] if rem else None,
            "suavidad_pp": SUAVIDAD * 100}


# ---------------------------------------------------------------- registro y puntaje
def guardar(fecha_precio: str, rem_edicion: str | None, res: dict) -> None:
    """Foto del día (reemplaza la de la misma fecha) para ver después quién acertó cada mes."""
    filas = []
    if ARCHIVO.exists():
        filas = [r for r in csv.DictReader(ARCHIVO.open(encoding="utf-8")) if r["fecha_precio"] != fecha_precio]
    r4 = lambda v: "" if v is None else round(v, 4)
    for m in res["meses"]:
        filas.append({"fecha_precio": fecha_precio, "rem_edicion": rem_edicion or "", "mes": m["mes"],
                      "mercado": r4(m["mercado"]), "banda_min": r4(m.get("banda_min")), "banda_max": r4(m.get("banda_max")),
                      "rem": r4(m.get("rem")), "top10": r4(m.get("top10"))})
    with ARCHIVO.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)


def inflacion_real(cer: dict, mes: str) -> float | None:
    """Inflación del mes según el propio CER (15 del mes+1 al 15 del mes+2), si ya está publicada."""
    d0, d1 = ventana_mes(mes)
    if d1 > max(cer) or d0 < min(cer):
        return None
    return (I.valor_en(cer, d1) / I.valor_en(cer, d0) - 1) * 100


def cierre_encuesta(edicion: str) -> date:
    """El REM de un mes se responde en sus últimos días hábiles: se toma el último hábil del mes."""
    y, m = int(edicion[:4]), int(edicion[5:])
    d = date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
    while not I.es_habil(d):
        d -= timedelta(days=1)
    return d


MAX_DIAS_ANTES = 4   # la foto del mercado puede ser de hasta 4 días corridos antes del cierre de la encuesta
MIN_OBSERVACIONES = 12   # con menos casos independientes no se declara ganador


def tablero(cer: dict) -> list[dict]:
    """Comparación con la misma información: para cada edición del REM, el mercado con los precios del último
    cierre ANTES O IGUAL al día en que cerró esa encuesta (no el día en que se bajó el archivo, una semana
    después). El REM sale del archivo de esa edición. Una observación por edición y mes."""
    if not ARCHIVO.exists():
        return []
    filas = list(csv.DictReader(ARCHIVO.open(encoding="utf-8")))
    out = []
    for f in sorted((BASE / "datos" / "rem").glob("*.json")):
        rem = json.loads(f.read_text(encoding="utf-8"))
        cierre = cierre_encuesta(rem["edicion"])
        fechas = sorted({r["fecha_precio"] for r in filas if r["fecha_precio"] <= cierre.isoformat()})
        if not fechas or (cierre - date.fromisoformat(fechas[-1])).days > MAX_DIAS_ANTES:
            continue                                     # no había foto del mercado ese día: no se puntúa
        dia = fechas[-1]
        for r in (x for x in filas if x["fecha_precio"] == dia):
            real = inflacion_real(cer, r["mes"])
            if real is None:
                continue
            fila = {"edicion": rem["edicion"], "precios_del": dia, "mes": r["mes"], "real": real,
                    "mercado": float(r["mercado"]), "rem": rem["total"]["ipc"].get(r["mes"]),
                    "top10": rem["top10"]["ipc"].get(r["mes"])}
            for q in ("mercado", "rem", "top10"):
                fila["sesgo_" + q] = fila[q] - real if fila[q] is not None else None   # con signo: la prima aparece acá
            fila["dentro_banda"] = bool(r["banda_min"]) and float(r["banda_min"]) <= real <= float(r["banda_max"])
            out.append(fila)
    return out
