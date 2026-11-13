"""Backtest: ¿quién le pegó más a la inflación, el mercado (bonos) o el REM? Con el mismo método de la página.

Para cada edición del REM:
  1. Día de comparación = fecha exacta de la encuesta ("Fecha de pronóstico" del histórico del BCRA). Mercado
     y REM con la MISMA información.
  2. Precios de cierre de ese día (o hasta 4 días antes) de BYMA, para todas las LECAP/BONCAP y CER cero cupón
     vivos ese día cuyas condiciones leyó la IA del Boletín Oficial.
  3. CER tal como se conocía ese día: publicado hasta el 15 del mes siguiente si ya pasó mediados de mes.
  4. Mismo cálculo que la página (nelson_siegel.calcular): inflación por BLOQUES entre vencimientos CER.
  5. Cada bloque se compara con la inflación que después mostró el CER en ese mismo tramo.

Limitaciones (dichas también en la página): sin spreads históricos (todos los bonos con 0,5% de spread, así que
no hay banda de puntas confiable), sin filtro de cantidad de operaciones (solo días con volumen), y solo bonos
cuyas condiciones están leídas. El REM de cada bloque usa solo los meses que la encuesta preguntó.

Uso: python backtest.py      → datos/backtest/resultados.csv y resumen.json
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import calculos as c
import instrumentos as I
import nelson_siegel as N
import tickers as T
from actualizar import marcar_fuera_de_curva

BASE = Path(__file__).parent
CARPETA = BASE / "datos" / "backtest"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
LETRA = dict(zip(range(1, 13), "EFMAYJLGSOND"))
DESDE = date(2025, 1, 1)
TIPOS = ("letra_capitalizable_pesos", "bono_capitalizable_pesos", "letra_cer", "bono_cer")
URL_REM = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/historico-relevamiento-expectativas-mercado.xlsx"


def ticker_de(t: dict) -> str | None:
    """Inverso de tickers.decodificar para los tipos del backtest (S30J5, T30A7, X29E7, TZXD6, TZX27)."""
    v = date.fromisoformat(t["fecha_vencimiento"])
    if t["tipo"] == "letra_capitalizable_pesos":
        return f"S{v.day:02d}{LETRA[v.month]}{v.year % 10}"
    if t["tipo"] == "bono_capitalizable_pesos":
        return f"T{v.day:02d}{LETRA[v.month]}{v.year % 10}"
    if t["tipo"] == "letra_cer":
        return f"X{v.day:02d}{LETRA[v.month]}{v.year % 10}"
    if t["tipo"] == "bono_cer" and not t.get("tasa_nominal_anual_pct"):
        return f"TZX{v.year % 100:02d}" if v.month == 6 else f"TZX{LETRA[v.month]}{v.year % 10}"
    return None


def historia(ticker: str) -> dict[date, tuple[float, float]]:
    """Cierres diarios de BYMA (liquidación 24 hs): {fecha: (cierre, volumen)}. Caché de 20 horas."""
    cache = CARPETA / "precios" / f"{ticker}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 20 * 3600:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        q = urllib.parse.urlencode({"symbol": f"{ticker} 24HS", "resolution": "D",
                                    "from": int(datetime(DESDE.year, DESDE.month, DESDE.day).timestamp()), "to": int(time.time())})
        url = "https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/chart/historical-series/history?" + q
        raw = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw), encoding="utf-8")
        time.sleep(0.5)
    if raw.get("s") != "ok":
        return {}
    # BYMA marca cada vela a las 00:00 de Buenos Aires (03:00 UTC)
    return {(datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=3)).date(): (cl, v)
            for t, cl, v in zip(raw["t"], raw["c"], raw["v"])}


def cer_conocido(cer: dict, d: date) -> dict:
    """CER tal como se conocía el día d: hasta el 15 del mes siguiente si ya pasó mediados de mes."""
    y, m = (d.year, d.month) if d.day < 16 else (d.year + d.month // 12, d.month % 12 + 1)
    hasta = date(y, m, 15)
    return {k: v for k, v in cer.items() if k <= hasta}


def rem_historico() -> dict[date, dict]:
    """{fecha de la encuesta: {"edicion", "total": {"ipc": {mes: mediana}}, "top10": {...}}} desde el histórico
    oficial del BCRA (solo inflación mensual: lo único que usa la comparación por bloques)."""
    import openpyxl
    f = CARPETA / "rem_historico.xlsx"
    if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(urllib.request.urlopen(urllib.request.Request(URL_REM, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(f.read_bytes()), read_only=True, data_only=True)
    out: dict[date, dict] = {}
    for hoja, clave in (("Base de Datos Completa", "total"), ("Base Completa TOP-10", "top10")):
        filas = wb[hoja].iter_rows(values_only=True)
        enc = None
        for row in filas:
            if row and row[0] == "Fecha de pronóstico":
                enc = list(row); break
        i_med = enc.index("Mediana")
        for row in filas:
            fp, var, ref, per = row[0], row[1], row[2], row[3]
            if not isinstance(fp, datetime) or not isinstance(per, datetime) or fp.date() < DESDE:
                continue
            if not (isinstance(var, str) and var.startswith("Precios minoristas (IPC nivel general") and ref == "var. % mensual"):
                continue
            d = out.setdefault(fp.date(), {"edicion": fp.strftime("%Y-%m"), "total": {"ipc": {}}, "top10": {"ipc": {}}})
            d[clave]["ipc"][per.strftime("%Y-%m")] = row[i_med]
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    tit = [t for t in T.titulos_ia() if t["tipo"] in TIPOS and t["fecha_vencimiento"] >= DESDE.isoformat()]
    tasas = json.loads((BASE / "datos" / "tasas_licitacion.json").read_text(encoding="utf-8"))
    cer_full = I.serie_bcra(30, "2024-01-01", (date.today() + timedelta(days=60)).isoformat())
    a3500 = I.serie_bcra(5, "2024-06-01", date.today().isoformat())
    hist = {}
    for t in tit:
        tk = ticker_de(t)
        if tk:
            hist[tk] = (t, historia(tk))
    print(f"{len(hist)} bonos con condiciones; con precios: {sum(1 for _, h in hist.values() if h)}")
    rems = rem_historico()
    print(f"{len(rems)} encuestas del REM desde {DESDE}")

    filas_out = []
    for d, rem in sorted(rems.items()):
        if not rem["top10"]["ipc"]:
            continue
        liq = c.liquidacion_t1(d)
        cer = cer_conocido(cer_full, d)
        filas = []
        for tk, (t, h) in hist.items():
            # precio del MISMO día de la encuesta y con operaciones (antes se aceptaba hasta 4 días antes y se
            # mezclaban precios de días distintos; agente 2)
            if d not in h or h[d][1] <= 0:
                continue
            precio = h[d][0]
            tem = tasas.get(tk, {}).get("tem_capitalizacion_pct") or t.get("tasa_efectiva_mensual_pct")
            if t["tipo"].endswith("capitalizable_pesos") and not tem:
                continue
            try:
                v = I.valuar(tk, t, precio, liq, {tk: {"tem_capitalizacion_pct": tem}}, cer, a3500)
            except (KeyError, ValueError, TypeError, ZeroDivisionError):
                continue
            if not v or v.dias < 7:
                continue
            filas.append({"ticker": tk, "curva": v.curva, "tir": v.tir * 100, "md": v.duration, "dias": v.dias,
                          "vto": v.vencimiento.isoformat(), "spread": 0.5, "calc": {"cupon": None}})
        marcar_fuera_de_curva(filas)
        nf = sum(1 for f in filas if f["curva"] == "pesos_fija" and not f["revisar"])
        nc = sum(1 for f in filas if f["curva"] == "cer" and not f["revisar"])
        try:
            ns = N.calcular(filas, liq, cer, rem)
        except (ValueError, KeyError, ZeroDivisionError):
            ns = None
        if not ns:
            print(f"{rem['edicion']} ({d}): {nf} fijos, {nc} CER → sin estimación"); continue
        print(f"{rem['edicion']} ({d}): {nf} fijos, {nc} CER → bloques {[b['nombre'] for b in ns['bloques']]}")
        for k, b in enumerate(ns["bloques"]):
            real = N.inflacion_real_tramo(cer_full, date.fromisoformat(b["desde"]), date.fromisoformat(b["hasta"]))
            filas_out.append({"edicion": rem["edicion"], "encuesta": d.isoformat(), "bloque": k, "nombre": b["nombre"],
                              "desde": b["desde"], "hasta": b["hasta"], "dias": b["dias"],
                              "mercado": round(b["mercado"], 4), "rem": None if b.get("rem") is None else round(b["rem"], 4),
                              "rem_completo": b.get("rem_completo"),
                              "top10": None if b.get("top10") is None else round(b["top10"], 4),
                              "real": None if real is None else round(real, 4), "bonos_fijos": nf, "bonos_cer": nc})

    CARPETA.mkdir(parents=True, exist_ok=True)
    with (CARPETA / "resultados.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas_out[0]) if filas_out else ["edicion"])
        w.writeheader(); w.writerows(filas_out)
    res = resumir(filas_out)
    (CARPETA / "resumen.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


def resumir(filas: list[dict]) -> dict:
    """Solo bloques con inflación real conocida y REM de meses preguntados uno por uno. Por bloque (0 = el más
    cercano). Sesgo con signo (+ = esperaba más de lo que pasó) y error absoluto, en pp por mes de IPC."""
    ok = [f for f in filas if f["real"] is not None and f["rem"] is not None and f["top10"] is not None and f["rem_completo"]]
    def stats(fs):
        r = {"casos": len(fs), "ediciones": len({f["edicion"] for f in fs})}
        for q in ("mercado", "rem", "top10"):
            e = [f[q] - f["real"] for f in fs]
            r[q] = {"sesgo": round(sum(e) / len(e), 3), "error_abs": round(sum(abs(x) for x in e) / len(e), 3)}
        r["mercado_mas_cerca_que_rem"] = sum(1 for f in fs if abs(f["mercado"] - f["real"]) < abs(f["rem"] - f["real"]))
        return r
    out = {"por_bloque": {}, "desde": DESDE.isoformat()}
    for k in sorted({f["bloque"] for f in ok}):
        fs = [f for f in ok if f["bloque"] == k]
        out["por_bloque"][str(k)] = stats(fs)
    # Se puntúa solo el PRIMER bloque de cada encuesta y solo si es corto (≤ 2 meses de IPC): un "primer bloque"
    # de 4 a 10 meses no es un pronóstico del próximo mes. Y se cuentan las ventanas distintas (bloques que
    # terminan en otra fecha): si todas terminan el mismo día, es el mismo par de bonos medido varias veces.
    cortos = [f for f in ok if f["bloque"] == 0 and int(f["dias"]) <= 62]
    if ok:
        out["total"] = stats(ok)
        out["total"]["ventanas_distintas"] = len({f["hasta"] for f in ok})
        out["total"]["bonos_que_cierran"] = sorted({f["hasta"] for f in ok})
    if cortos:
        out["primer_bloque_corto"] = stats(cortos)
        out["primer_bloque_corto"]["ventanas_distintas"] = len({f["hasta"] for f in cortos})
    return out


if __name__ == "__main__":
    main()
