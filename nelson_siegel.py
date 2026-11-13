"""Inflación que espera el mercado, por BLOQUES entre vencimientos de bonos CER.

PASO 1 - TASA FIJA. Curva Nelson-Siegel por las LECAP/BONCAP sin cupón:
    r(t) = b0 + b1 · f1(t) + b2 · f2(t),   f1 = (1 - e^(-t/τ)) / (t/τ),   f2 = f1 - e^(-t/τ)
con tasas continuas r = ln(1 + TIR), t = días al vencimiento / 365 y cada bono pesando según su duration
dividida por su spread entre puntas (es ajustar PRECIOS). Entran también los CER cuyo ajuste ya está todo
publicado (son pesos fijos disfrazados). Sin bonos con cupón (TY30P, TX28, TX31) y sin extrapolar.
Si un bono CER tiene una LECAP/BONCAP "gemela" (mismo vencimiento) se usa la TIR de la gemela, no la curva.

PASO 2 - INFLACIÓN FUTURA DE CADA BONO CER:
    suba total del CER = (1 + tasa fija al mismo plazo)^t / (1 + TIR real)^t
menos la parte del CER ya publicada. Lo que queda (L) es la suba del CER entre el último publicado y el que
usa el bono al vencer (vencimiento - 10 hábiles): inflación que todavía no ocurrió.

PASO 3 - BLOQUES. Los bonos solo dicen cuánto sube el CER hasta SU fecha. Entre dos vencimientos CER
consecutivos, el mercado dice el TOTAL del tramo pero no cómo se reparte adentro: si no vence ningún CER entre
el 15/1 y el 15/3, diciembre y enero no se pueden separar (crítica del agente 2: antes una penalidad
"inventaba" el reparto). Por eso se informa la inflación PROMEDIO de cada bloque:
    bloque_i = L_i / L_(i-1),   promedio mensual = bloque_i ^ (30,4 / días) - 1
Es exacto: no hay suavizado ni parámetros. La regla del BCRA dice qué meses de IPC caen en cada bloque (la
inflación del mes M sube el CER del 16 de M+1 al 15 de M+2).

BANDA = PUNTAS. Cada bono se puede comprar o vender dentro de su spread. Moviendo cada precio media punta,
el bloque cambia como mucho (s_i + s_(i-1)) / 2. Esa es la incertidumbre que pone el MERCADO, no un supuesto
nuestro. No incluye el error de la tasa fija cuando no hay gemela ni la prima por riesgo.

OJO. El número del mercado incluye primas (cobertura de inflación, liquidez) todavía no medidas.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import expectativas as X
import instrumentos as I

BASE = Path(__file__).parent
ARCHIVO = BASE / "datos" / "inflacion_bloques_historial.csv"
CAMPOS = ["fecha_precio", "rem_edicion", "desde", "hasta", "meses", "mercado", "banda_min", "banda_max", "rem", "top10"]
TAUS = np.exp(np.linspace(math.log(0.05), math.log(3), 200))
MIN_DIAS_BLOQUE = 20   # vencimientos CER a menos de esto del anterior no arman bloque propio (se toma el de menor spread)
NOMBRES_MES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


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
    curva["plazo_min"] = float(t.min())
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


def meses_en(d0: date, d1: date) -> list[tuple[str, float]]:
    """Meses de IPC que caen en el tramo de CER (d0, d1], con la fracción de cada mes que cae adentro."""
    out = []
    mes = _mas_meses(f"{d0.year}-{d0.month:02d}", -2)
    while True:
        a, b = ventana_mes(mes)
        if a >= d1:
            break
        dentro = (min(b, d1) - max(a, d0)).days
        if dentro > 0:
            out.append((mes, dentro / (b - a).days))
        mes = _mas_meses(mes, 1)
    return out


def nombre_bloque(meses: list[tuple[str, float]]) -> str:
    """'nov' / 'dic–ene' / 'mar (y 1/3 de abr)': cuenta como mes del bloque si cae más de la mitad."""
    enteros = [m for m, f in meses if f > 0.5]
    if not enteros:
        enteros = [max(meses, key=lambda x: x[1])[0]]
    nom = lambda k: f"{NOMBRES_MES[int(k[5:]) - 1]}-{k[2:4]}"
    return nom(enteros[0]) if len(enteros) == 1 else f"{nom(enteros[0])} a {nom(enteros[-1])}"


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
        spread = max(f.get("spread") or 0.5, 0.05)       # % del precio; mínimo 0,05%
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


def inflacion_futura(fija: dict, pr: list[dict], liq: date, cer: dict, pf: list[dict] | None = None) -> list[dict]:
    """Para cada bono CER: suba del CER que paga DESPUÉS de lo ya publicado (paso 2).
    Tasa fija: la de su LECAP/BONCAP gemela si existe; si no, la curva. Solo dentro del plazo de la curva fija."""
    a, pub = I.restar_habiles(liq, 10), max(cer)
    conocido = I.valor_en(cer, pub) / I.valor_en(cer, a)
    gemelas = {p["vto"]: p for p in (pf or []) if p["origen"] == "pesos_fija"}
    out = []
    for p in pr:
        t = p["plazo"]                                   # inversión: de la liquidación al vencimiento
        gem = gemelas.get(p.get("vto"))
        # no extrapolar la tasa fija ni hacia adelante ni hacia atrás (en el backtest de feb-26 faltaban las LECAP
        # cortas y se comparaban CER de mayo contra una curva que arrancaba en enero del año siguiente)
        if not gem and not (fija.get("plazo_min", 0) - 15 / 365 <= t <= fija["plazo_max"] + 1e-9):
            continue
        r_fija = math.log1p(gem["tir"] / 100) if gem else tasa_continua(fija, t)
        total = math.exp(r_fija * t) / (1 + p["tir"] / 100) ** t
        dias = (p["cer_hasta"] - pub).days               # CER no publicado que cubre
        if dias < 7:
            continue
        out.append({**p, "dias_futuros": dias, "suba_futura": total / conocido,
                    "tasa_fija_de": gem["ticker"] if gem else "curva"})
    return out


def bloques(pts: list[dict], cer: dict) -> list[dict]:
    """Paso 3: inflación promedio de cada tramo entre vencimientos CER consecutivos, con banda de puntas."""
    pub = max(cer)
    elegidos = []
    for p in sorted(pts, key=lambda x: x["cer_hasta"]):
        previo = elegidos[-1]["cer_hasta"] if elegidos else pub
        if (p["cer_hasta"] - previo).days < MIN_DIAS_BLOQUE:
            if elegidos and p["spread"] < elegidos[-1]["spread"]:
                elegidos[-1] = p                          # dos vencimientos casi iguales: el de precio más firme
            elif not elegidos:
                continue
            continue
        elegidos.append(p)
    out, prev, s_prev, l_prev = [], pub, 0.0, 0.0
    for p in elegidos:
        dias = (p["cer_hasta"] - prev).days
        l = math.log(p["suba_futura"]) - l_prev
        d = (p["spread"] / 2 + s_prev / 2) / 100       # media punta de cada bono que lo delimita
        ms = meses_en(prev, p["cer_hasta"])
        n_meses = sum(f for _, f in ms)                  # cuántos "meses de IPC" cubre el bloque
        mens = lambda x: (math.exp(x / n_meses) - 1) * 100
        out.append({"desde": prev.isoformat(), "hasta": p["cer_hasta"].isoformat(), "dias": dias,
                    "bono": p["ticker"], "bono_anterior": out[-1]["bono"] if out else None,
                    "tasa_fija_de": p["tasa_fija_de"],
                    "meses": [[m, round(f, 3)] for m, f in ms], "nombre": nombre_bloque(ms),
                    "mercado": mens(l), "banda_min": mens(l - d), "banda_max": mens(l + d)})
        prev, s_prev, l_prev = p["cer_hasta"], p["spread"], math.log(p["suba_futura"])
    return out


def rem_bloque(rem_ipc: dict, meses: list) -> tuple[float | None, bool]:
    """Inflación mensual promedio que espera el REM en ese bloque (mismos días de cada mes que el mercado).
    Devuelve (valor, completo): completo = todos los meses del bloque los pregunta la encuesta uno por uno."""
    n, log = 0.0, 0.0
    completo = True
    for mes, frac in meses:
        v = rem_ipc.get(mes)
        if v is None:
            completo = False
            v = X.inflacion_rem({"ipc": rem_ipc}, mes)
            if v is None:
                return None, False
        log += math.log1p(v / 100) * frac
        n += frac
    return (math.exp(log / n) - 1) * 100 if n else None, completo


def calcular(filas: list[dict], liq: date, cer: dict, rem: dict | None) -> dict | None:
    """Curva fija, inflación por bloque con banda de puntas, REM del mismo bloque y tramo largo indicativo."""
    pf, pr = puntos(filas, liq, cer)
    publicado = max(cer)
    fija = ajustar([(p["plazo"], p["tir"], p["peso"]) for p in pf])
    if not fija:
        return None
    pts = inflacion_futura(fija, pr, liq, cer, pf)
    bl = bloques(pts, cer)
    if not bl:
        return None
    # Zigzag: dos bloques seguidos que se contradicen más allá de sus puntas (p. ej. 2,6% y 1,5%) no son una
    # expectativa: es un bono del medio que está caro o barato contra sus vecinos. Se avisa y se da el promedio
    # de los dos, que no depende de ese bono.
    for x, y in zip(bl, bl[1:]):
        if (x["banda_min"] > y["banda_max"] or y["banda_min"] > x["banda_max"]) and abs(x["mercado"] - y["mercado"]) > 0.5:
            nx, ny = sum(f for _, f in x["meses"]), sum(f for _, f in y["meses"])
            prom = (math.exp((math.log1p(x["mercado"] / 100) * nx + math.log1p(y["mercado"] / 100) * ny) / (nx + ny)) - 1) * 100
            for b in (x, y):
                b["zigzag"] = {"bono": x["bono"], "promedio_dos": prom, "con": (y if b is x else x)["nombre"]}
    fija["bonos"] = [{"ticker": p["ticker"], "plazo": round(p["plazo"], 4), "tir": round(p["tir"], 3),
                      "spread": round(p["spread"], 3), "origen": p["origen"], "error_pb": e,
                      **({"tir_real": round(p["tir_real"], 3)} if "tir_real" in p else {})}
                     for p, e in zip(pf, fija.pop("errores_pb"))]
    cer_bonos = [{"ticker": p["ticker"], "vto": p["vto"], "tir_real": round(p["tir"], 3), "spread": round(p["spread"], 3),
                  "cer_hasta": p["cer_hasta"].isoformat(), "dias_futuros": p["dias_futuros"], "tasa_fija_de": p["tasa_fija_de"],
                  "suba_futura_pct": round((p["suba_futura"] - 1) * 100, 3),
                  "mensual_promedio": round((p["suba_futura"] ** (30.4 / p["dias_futuros"]) - 1) * 100, 3)} for p in pts]
    afuera = [{"ticker": p["ticker"], "motivo": "vence después de la última LECAP/BONCAP (no hay tasa fija a ese plazo)"}
              for p in pr if p["plazo"] > fija["plazo_max"] + 1e-9]
    if rem:
        for b in bl:
            b["rem"], b["rem_completo"] = rem_bloque(rem["total"]["ipc"], b["meses"])
            b["top10"], _ = rem_bloque(rem["top10"]["ipc"], b["meses"])
    a = I.restar_habiles(liq, 10)
    return {"fija": fija, "cer_bonos": cer_bonos, "cer_afuera": afuera, "bloques": bl, "desde_cer": a.isoformat(),
            "largo": tramo_largo(filas, fija, liq, cer),
            "cer_conocido_pct": (I.valor_en(cer, publicado) / I.valor_en(cer, a) - 1) * 100,
            "cer_publicado_hasta": publicado.isoformat(), "rem_edicion": rem["edicion"] if rem else None}


# ---------------------------------------------------------------- tramo largo (indicativo)
def _df(fija: dict, forward: float, t: float) -> float:
    """Factor de descuento en pesos: la curva NS hasta la última LECAP; después, tasa forward pareja."""
    T = fija["plazo_max"]
    if t <= T:
        return math.exp(-tasa_continua(fija, t) * t)
    return math.exp(-tasa_continua(fija, T) * T - forward * (t - T))


def _forward_que_valua(fija: dict, flujos: list[tuple[float, float]], precio: float) -> float | None:
    """Tasa forward pareja (continua) después de la última LECAP que hace que el bono valga su precio."""
    lo, hi = -0.5, 3.0
    vp = lambda f: sum(c * _df(fija, f, t) for t, c in flujos)
    if not (vp(hi) < precio < vp(lo)):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if vp(mid) > precio else (lo, mid)
    return (lo + hi) / 2


def tramo_largo(filas: list[dict], fija: dict, liq: date, cer: dict) -> dict | None:
    """Inflación promedio implícita en los CER cero cupón que vencen después de la última LECAP/BONCAP.
    No hay LECAP más largas: la única referencia de tasa fija es el BONTE TY30P (con cupón), valuado flujo por
    flujo con un supuesto explícito (tasa forward pareja después de la última LECAP). Por eso es INDICATIVO:
    depende de un solo bono y de ese supuesto. Se informa cuánto cambia si la TIR del TY30P se mueve ±1 punto."""
    bonte = next((f for f in filas if f["ticker"] == "TY30P" and not f["revisar"]), None)
    if not bonte or not (bonte.get("calc") or {}).get("flujos"):
        return None
    flujos = [((date.fromisoformat(d) - liq).days / 365, c) for d, c in bonte["calc"]["flujos"] if date.fromisoformat(d) > liq]
    a, pub = I.restar_habiles(liq, 10), max(cer)
    conocido = I.valor_en(cer, pub) / I.valor_en(cer, a)
    largos = sorted([f for f in filas if f["curva"] == "cer" and not f["revisar"] and not (f.get("calc") or {}).get("cupon")
                     and f["dias"] / 365 > fija["plazo_max"]], key=lambda f: f["dias"])
    if not largos:
        return None

    def calcular_con(precio_bonte: float) -> list[dict] | None:
        fw = _forward_que_valua(fija, flujos, precio_bonte)
        if fw is None:
            return None
        out = []
        for f in largos:
            t = f["dias"] / 365
            b = I.restar_habiles(date.fromisoformat(f["vto"]), 10)
            futura = (1 / _df(fija, fw, t)) / (1 + f["tir"] / 100) ** t / conocido
            out.append({"ticker": f["ticker"], "vto": f["vto"], "cer_hasta": b.isoformat(), "dias_futuros": (b - pub).days,
                        "mensual_promedio": (futura ** (30.4 / (b - pub).days) - 1) * 100, "forward_pct": (math.exp(fw) - 1) * 100})
        return out

    base = calcular_con(bonte["precio"])
    if not base:
        return None
    dp = bonte["precio"] * bonte["md"] / 100            # ±1 punto de TIR del TY30P → precio con su duration
    arriba, abajo = calcular_con(bonte["precio"] - dp), calcular_con(bonte["precio"] + dp)
    for i, r in enumerate(base):
        vals = [r["mensual_promedio"]] + [x[i]["mensual_promedio"] for x in (arriba, abajo) if x]
        r["min"], r["max"] = min(vals), max(vals)
    return {"bonte": {"ticker": "TY30P", "precio": bonte["precio"], "tir": bonte["tir"], "md": bonte["md"],
                      "forward_pct": base[0]["forward_pct"], "desde": (liq + timedelta(days=round(fija["plazo_max"] * 365))).isoformat()},
            "bonos": base}


# ---------------------------------------------------------------- registro y puntaje
def guardar(fecha_precio: str, rem_edicion: str | None, res: dict) -> None:
    """Foto del día (reemplaza la de la misma fecha) para ver después quién acertó cada bloque."""
    filas = []
    if ARCHIVO.exists():
        filas = [r for r in csv.DictReader(ARCHIVO.open(encoding="utf-8")) if r["fecha_precio"] != fecha_precio]
    r4 = lambda v: "" if v is None else round(v, 4)
    for b in res["bloques"]:
        filas.append({"fecha_precio": fecha_precio, "rem_edicion": rem_edicion or "", "desde": b["desde"], "hasta": b["hasta"],
                      "meses": json.dumps(b["meses"]), "mercado": r4(b["mercado"]), "banda_min": r4(b["banda_min"]),
                      "banda_max": r4(b["banda_max"]), "rem": r4(b.get("rem")), "top10": r4(b.get("top10"))})
    with ARCHIVO.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)


def inflacion_real_tramo(cer: dict, desde: date, hasta: date) -> float | None:
    """Inflación mensual promedio que mostró el CER en ese tramo, si ya está publicada."""
    if hasta > max(cer) or desde < min(cer):
        return None
    n = sum(f for _, f in meses_en(desde, hasta))       # misma unidad que el mercado: meses de IPC
    return ((I.valor_en(cer, hasta) / I.valor_en(cer, desde)) ** (1 / n) - 1) * 100


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


def tablero(cer: dict) -> list[dict]:
    """Comparación con la misma información: para cada edición del REM, el mercado con los precios del último
    cierre ANTES O IGUAL al día en que cerró esa encuesta. Una observación por edición y bloque."""
    if not ARCHIVO.exists():
        return []
    filas = list(csv.DictReader(ARCHIVO.open(encoding="utf-8")))
    out = []
    for f in sorted((BASE / "datos" / "rem").glob("*.json")):
        rem = json.loads(f.read_text(encoding="utf-8"))
        cierre = cierre_encuesta(rem["edicion"])
        fechas = sorted({r["fecha_precio"] for r in filas if r["fecha_precio"] <= cierre.isoformat()})
        if not fechas or (cierre - date.fromisoformat(fechas[-1])).days > MAX_DIAS_ANTES:
            continue
        dia = fechas[-1]
        for r in (x for x in filas if x["fecha_precio"] == dia):
            d0, d1 = date.fromisoformat(r["desde"]), date.fromisoformat(r["hasta"])
            real = inflacion_real_tramo(cer, d0, d1)
            if real is None:
                continue
            meses = json.loads(r["meses"])
            fila = {"edicion": rem["edicion"], "precios_del": dia, "desde": r["desde"], "hasta": r["hasta"],
                    "nombre": nombre_bloque(meses), "real": real, "mercado": float(r["mercado"]),
                    "rem": rem_bloque(rem["total"]["ipc"], meses)[0], "top10": rem_bloque(rem["top10"]["ipc"], meses)[0]}
            for q in ("mercado", "rem", "top10"):
                fila["sesgo_" + q] = fila[q] - real if fila[q] is not None else None
            fila["dentro_banda"] = float(r["banda_min"]) <= real <= float(r["banda_max"])
            out.append(fila)
    return out
