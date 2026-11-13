"""Baja precios (data912) y series (BCRA), valúa todos los bonos y genera docs/index.html.

Uso: python actualizar.py
"""
from __future__ import annotations

import csv
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import calculos as c
import expectativas as X
import feriados as F
import quien_acierta as Q
import rem as R
import instrumentos as I
import nelson_siegel as N
import tickers as T

BASE = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}
UMBRAL_PP = {"dolares": 4.0, "pesos_fija": 5.0, "cer": 4.0, "dolar_linked": 6.0}
MAX_SPREAD_PCT = 2.0   # diferencia comprador-vendedor por encima de la cual el precio no es confiable
MIN_OPERACIONES = 10   # operaciones mínimas en el día


def liquidez(p: dict) -> tuple[float | None, int, str | None]:
    """Spread % entre puntas, cantidad de operaciones y motivo si el precio no sirve para la curva."""
    bid, ask, ops = p.get("px_bid") or 0, p.get("px_ask") or 0, int(p.get("q_op") or 0)
    spread = (ask - bid) / ((ask + bid) / 2) * 100 if bid > 0 and ask > 0 else None
    if ops < MIN_OPERACIONES:
        return spread, ops, f"Poca liquidez: {ops} operaciones en el día"
    if spread is not None and spread > MAX_SPREAD_PCT:
        return spread, ops, f"Poca liquidez: {spread:.1f}% entre comprador y vendedor"
    return spread, ops, None


def bajar(url: str):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))


def fecha_precio(ticker: str) -> str:
    h = bajar(f"https://data912.com/historical/bonds/{ticker}")
    return h[-1]["date"][:10] if h else date.today().isoformat()


def marcar_fuera_de_curva(filas: list[dict]) -> None:
    """Control automático: un bono cuya TIR se aleja mucho del promedio de sus dos vecinos queda 'a revisar'."""
    for f in filas:  # primero: los de poca liquidez quedan fuera y no se usan como vecinos
        f["motivo"] = f.get("motivo_liquidez")
        f["revisar"] = f["motivo"] is not None
    for curva, umbral in UMBRAL_PP.items():
        while True:
            pts = sorted([f for f in filas if f["curva"] == curva and f["dias"] >= 30 and not f["revisar"]],
                         key=lambda f: f["md"])
            if len(pts) < 3:
                break
            peor, desvio = None, 0.0
            for i, f in enumerate(pts):
                if 0 < i < len(pts) - 1:  # interior: contra la recta entre sus vecinos
                    a, b = pts[i - 1], pts[i + 1]
                    esperado = a["tir"] + (b["tir"] - a["tir"]) * (f["md"] - a["md"]) / ((b["md"] - a["md"]) or 1)
                else:                     # punta: contra su vecino más cercano
                    esperado = pts[1 if i == 0 else -2]["tir"]
                d = abs(f["tir"] - esperado)
                if d > desvio:
                    peor, desvio = f, d
            if desvio <= umbral:
                break
            peor["revisar"] = True
            peor["motivo"] = f"Lejos de su curva: {desvio:.1f} puntos contra sus vecinos"


def vecinos(filas: list[dict], curva: str, dias: int) -> list[dict]:
    """Los dos bonos de la curva (sin los marcados) entre los que se interpola ese plazo."""
    pts = sorted([f for f in filas if f["curva"] == curva and not f["revisar"]], key=lambda f: f["dias"])
    for a, b in zip(pts, pts[1:]):
        if a["dias"] <= dias <= b["dias"]:
            return [a, b]
    return []


def fuente_titulo(t: dict, idx_bo: dict) -> dict:
    if t["resolucion"] in idx_bo:
        n = idx_bo[t["resolucion"]]
        return {"fuente": f"Res. Conj. leída por IA ({n['titulo'].split('Resolución Conjunta')[-1].strip()})", "url": n["url"]}
    return {"fuente": "Decreto 676/2020, Anexo IV (carga manual)",
            "url": "https://www.boletinoficial.gob.ar/detalleAviso/primera/233685/20200816"}


def rueda_cerrada() -> bool:
    """True si ya cerró la rueda de hoy (o no hubo rueda). Antes de las 17:30 de un día hábil, los precios
    'en vivo' son parciales (pocas operaciones): sirven para mirar la página pero no se guardan en los historiales,
    porque pisarían la foto buena del cierre anterior. Argentina no tiene horario de verano: UTC-3 fijo."""
    ahora = datetime.now(timezone.utc) - timedelta(hours=3)
    return not F.es_habil(ahora.date()) or (ahora.hour, ahora.minute) >= (17, 30)


def main() -> None:
    F.actualizar()
    guardar = rueda_cerrada()
    if not guardar:
        print("Rueda abierta o sin abrir: se genera la página pero NO se guardan los historiales.")
    precios = {}
    for ep in ("arg_bonds", "arg_notes"):
        for r in bajar(f"https://data912.com/live/{ep}"):
            precios[r["symbol"]] = r
    cierre = fecha_precio("GD30D")
    liq = c.liquidacion_t1(date.fromisoformat(cierre))
    cer = I.serie_bcra(30, "2020-08-01", (liq + timedelta(days=60)).isoformat())
    a3500 = I.serie_bcra(5, "2026-10-01", liq.isoformat())  # desde el inicio del registro de expectativas
    filas: list[dict] = []

    # 1) canje 2020 (flujos cargados a mano desde los decretos)
    fam = json.loads((BASE / "bonos.json").read_text(encoding="utf-8"))["familias"]
    for f in fam.values():
        for ticker, ley in f["bonos"].items():
            p = precios.get(ticker + "D")
            if not p or not p.get("c"):
                continue
            r = c.valuar(f, p["c"], liq)
            filas.append({"ticker": ticker, "curva": "dolares", "grupo": "GD (ley NY)" if ley.endswith("York") else "AL/AE (ley arg.)",
                          "precio": r.precio, "tir": r.tir * 100, "md": r.duration_mod,
                          "dias": (r.vencimiento - liq).days, "vto": r.vencimiento.isoformat(),
                          "var": p.get("pct_change") or 0.0, "fuente": "Decretos 676 y 701/2020 (carga manual)",
                          "detalle": f"Paridad {r.paridad * 100:.1f}%"})
            filas[-1]["spread"], filas[-1]["ops"], filas[-1]["motivo_liquidez"] = liquidez(p)
            filas[-1]["calc"] = {"tipo": "canje", "precio_calc": p["c"], "ley": ley,
                                 "residual": r.residual, "corridos": r.intereses_corridos, "paridad": r.paridad,
                                 "flujos": [[fl.fecha_pago.isoformat(), round(fl.interes + fl.amortizacion, 6)]
                                            for fl in c.flujos(f) if fl.fecha_pago > liq]}

    # 2) bonos leídos por la IA
    tasas = json.loads((BASE / "datos" / "tasas_licitacion.json").read_text(encoding="utf-8"))
    idx_bo = json.loads((BASE / "fuentes" / "boletin" / "indice.json").read_text(encoding="utf-8"))
    tit = {(t["tipo"], t["fecha_vencimiento"]): t for t in T.titulos_ia()}
    controles = {"ia_titulos": len(tit), "vto_ok": 0, "amort_ok": 0, "tasas_ok": 0, "tasas_total": 0}
    for r in csv.DictReader(open(BASE / "datos" / "tickers.csv", encoding="utf-8-sig"), delimiter=";"):
        if r["estado"] != "ok" or r["vencimiento"] <= liq.isoformat():
            continue
        t = tit[(r["tipo"], r["vencimiento"])]
        controles["vto_ok"] += 1  # el emparejamiento exige que el vencimiento leído coincida con el del ticker
        controles["amort_ok"] += abs(sum(a["porcentaje"] for a in t["amortizacion"]) - 100) < 1e-6
        if r["ticker"] in tasas:
            controles["tasas_total"] += 1
            tm, ti = tasas[r["ticker"]]["tem_capitalizacion_pct"], tasas[r["ticker"]]["tirea_pct"]
            controles["tasas_ok"] += (tm is None) or (ti is not None and abs(((1 + tm / 100) ** 12 - 1) * 100 - ti) < 0.05)
        p = precios.get(r["ticker"] + "D") if r["tipo"] == "bono_dolares" else precios.get(r["ticker"])
        if not p or not p.get("c"):
            continue
        try:
            v = I.valuar(r["ticker"], t, p["c"], liq, tasas, cer, a3500)
        except (KeyError, ValueError):
            continue
        if not v:
            continue
        grupos = {"pesos_fija": "LECAP/BONCAP/BONTE", "cer": "CER", "dolar_linked": "Dollar linked", "dolares": "AO/AN (ley arg.)"}
        det = {"pesos_fija": lambda e: f"TEM mercado {e['tem_mercado'] * 100:.2f}%" if "tem_mercado" in e else f"Cupón {e['cupon']:g}%",
               "cer": lambda e: f"Coef. CER {e['coef_cer']:.4f}" + (f" · cupón {e['cupon']:g}%" if e.get("cupon") else ""),
               "dolar_linked": lambda e: f"Precio US$ {e['precio_usd']:.2f} (A3500 {e['a3500']:.2f})",
               "dolares": lambda e: f"Cupón {e['cupon']:g}%"}[v.curva](v.extra)
        filas.append({"ticker": v.ticker, "curva": v.curva, "grupo": grupos[v.curva], "precio": v.precio,
                      "tir": v.tir * 100, "md": v.duration, "dias": v.dias, "vto": v.vencimiento.isoformat(),
                      "var": p.get("pct_change") or 0.0, "detalle": det,
                      **fuente_titulo(t, idx_bo)})
        filas[-1]["spread"], filas[-1]["ops"], filas[-1]["motivo_liquidez"] = liquidez(p)
        filas[-1]["calc"] = {"tipo": t["tipo"], "nombre": t["nombre"], **v.extra}
        if r["ticker"] in tasas:
            filas[-1]["calc"]["licitacion"] = {k: tasas[r["ticker"]].get(k) for k in ("tem_capitalizacion_pct", "tirea_pct", "tasa_cupon_pct")}

    marcar_fuera_de_curva(filas)

    # 3) expectativas implícitas: tasa fija vs CER (inflación) y vs dollar linked (devaluación)
    # Bonos con cupón (TX28, TX31, TY30P) fuera de los pares: comparar TIR de bonos con cupón y durations
    # distintas con Fisher no es un breakeven (crítica del agente 2).
    sin_cupon = lambda f: not (f.get("calc") or {}).get("cupon")
    curva_cer = [(f["dias"], f["tir"]) for f in filas if f["curva"] == "cer" and not f["revisar"] and sin_cupon(f)]
    curva_dl = [(f["dias"], f["tir"]) for f in filas if f["curva"] == "dolar_linked" and not f["revisar"]]
    a_cer, pub = I.restar_habiles(liq, 10), max(cer)
    conocido = I.valor_en(cer, pub) / I.valor_en(cer, a_cer)
    expect = []
    for f in sorted([f for f in filas if f["curva"] == "pesos_fija" and not f["revisar"] and f["dias"] > 30 and sin_cupon(f)],
                    key=lambda f: f["dias"]):
        real = I.interpolar(curva_cer, f["dias"])
        dl = I.interpolar(curva_dl, f["dias"])
        vec = lambda curva: [{"ticker": x["ticker"], "dias": x["dias"], "tir": x["tir"]} for x in vecinos(filas, curva, f["dias"])
                             if sin_cupon(x)]
        # inflación SOLO sobre el CER que todavía no se publicó (antes, en ventanas cortas, 3/4 era dato ya conocido)
        inf_mens = None
        b = I.restar_habiles(date.fromisoformat(f["vto"]), 10)
        if real is not None and (b - pub).days >= 15:
            total = ((1 + f["tir"] / 100) / (1 + real / 100)) ** (f["dias"] / 365)
            inf_mens = ((total / conocido) ** ((365 / 12) / (b - pub).days) - 1) * 100
        dev = ((1 + f["tir"] / 100) / (1 + dl / 100) - 1) * 100 if dl is not None else None
        if inf_mens is None and dev is None:
            continue
        expect.append({"ticker": f["ticker"], "vto": f["vto"], "dias": f["dias"], "tir_fija": f["tir"],
                       "vecinos_cer": vec("cer"), "vecinos_dl": vec("dolar_linked"),
                       "tir_cer": real, "inflacion_mensual": inf_mens,
                       "inflacion_anual": ((1 + inf_mens / 100) ** 12 - 1) * 100 if inf_mens is not None else None,
                       "cer_conocido_pct": (conocido - 1) * 100, "cer_futuro_dias": (b - pub).days,
                       "tir_dl": dl, "devaluacion_anual": dev,
                       "devaluacion_mensual": ((1 + dev / 100) ** (1 / 12) - 1) * 100 if dev is not None else None})

    # 4) misma ventana según el REM (total y Top 10) y registro para ver después quién acertó
    rem = R.cargar()
    if rem:
        for e in expect:
            e.update(X.comparar(e, liq, cer, a3500, rem["total"], "rem"))
            e.update(X.comparar(e, liq, cer, a3500, rem["top10"], "top10"))
        if guardar:
            Q.guardar(cierre, rem["edicion"], expect)
    aciertos = Q.tablero(cer, a3500)

    # 5) Nelson-Siegel: una curva suave por todos los bonos fijos y otra por todos los CER → inflación mes a mes
    ns = N.calcular(filas, liq, cer, rem)
    if ns:
        if guardar:
            N.guardar(cierre, rem["edicion"] if rem else None, ns)
        ns["aciertos"] = N.tablero(cer)

    (BASE / "datos").mkdir(exist_ok=True)
    # historial: una foto por fecha de precios (si se corre dos veces el mismo día, se reemplaza)
    hist = BASE / "datos" / "historial.csv"
    if guardar:
        previas = []
        if hist.exists():
            with hist.open(encoding="utf-8") as fh:
                previas = [r for r in csv.reader(fh)][1:]
            vistas = set()
            previas = [r for r in previas if r[0] != cierre and not ((r[0], r[2]) in vistas or vistas.add((r[0], r[2])))]
        with hist.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["fecha_precio", "liquidacion", "ticker", "curva", "precio", "tir", "md"])
            w.writerows(previas)
            for x in filas:
                w.writerow([cierre, liq.isoformat(), x["ticker"], x["curva"], x["precio"], round(x["tir"], 4), round(x["md"], 4)])

    uso = json.loads((BASE / "datos" / "lecturas" / "uso.json").read_text(encoding="utf-8"))
    for f in filas:
        f.pop("motivo_liquidez", None)
    controles.update({"revisar": [f["ticker"] for f in filas if f["revisar"]],
                      "motivos": {f["ticker"]: f["motivo"] for f in filas if f["revisar"]},
                      "costo_ia": round(sum(uso[k]["costo_usd"] for k in ("opus", "resultados") if k in uso), 2),
                      "resoluciones": len(list((BASE / "datos" / "lecturas" / "opus").glob("*.json"))),
                      "a3500": I.valor_en(a3500, liq), "cer": I.valor_en(cer, liq),
                      "rem_edicion": rem["edicion"] if rem else None, "aciertos": aciertos,
                      "rem": {k: rem[k] for k in ("total", "top10", "fuente")} if rem else None,
                      "cer_publicado_hasta": max(cer).isoformat()})
    plantilla = (BASE / "plantilla.html").read_text(encoding="utf-8")
    out = (plantilla
           .replace("{{DATOS}}", json.dumps(filas, ensure_ascii=False))
           .replace("{{EXPECT}}", json.dumps(expect, ensure_ascii=False))
           .replace("{{NS}}", json.dumps(ns, ensure_ascii=False))
           .replace("{{CONTROLES}}", json.dumps(controles, ensure_ascii=False))
           .replace("{{CIERRE}}", date.fromisoformat(cierre).strftime("%d/%m/%Y"))
           .replace("{{LIQ}}", liq.strftime("%d/%m/%Y"))
           .replace("{{LIQ_ISO}}", liq.isoformat())
           .replace("{{GENERADO}}", datetime.now().strftime("%d/%m/%Y %H:%M")))
    (BASE / "docs").mkdir(exist_ok=True)
    (BASE / "docs" / "index.html").write_text(out, encoding="utf-8")
    print(f"ok: {len(filas)} bonos, {len(expect)} puntos de expectativas, a revisar: {controles['revisar']}, precios al {cierre}")


if __name__ == "__main__":
    main()
