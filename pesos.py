"""Curva de bonos soberanos en pesos: tasa fija (LECAP/BONCAP), CER e inflación implícita por períodos.

Página aparte y enfocada (docs/pesos.html). Reusa la carga de actualizar.py y el método de inflación de
nelson_siegel.py (bootstrapping, METODOLOGIA.md). Correr: python pesos.py

Puntas (v1.1, en revisión): se toman durante la rueda (puntas.py, 16:00/16:20/16:40, mediana). Las del cierre no
miden liquidez (T30A7: 1.113 operaciones y 8,6% entre puntas); si un día no hay fotos, no se excluye por puntas.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import actualizar as A
import feriados as F
import nelson_siegel as N
import rem as R

BASE = Path(__file__).parent
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def nom_mes(k: str) -> str:
    y, m = k.split("-")
    return f"{MESES[int(m) - 1]}-{y[2:]}"


def fecha(s: str) -> str:
    return date.fromisoformat(s).strftime("%d/%m/%y")


def pct(v, dec=2) -> str:
    return "–" if v is None else f"{v:.{dec}f}%".replace(".", ",")


def acumular(vals: list[float]) -> float:
    return (math.prod(1 + v / 100 for v in vals) - 1) * 100


def periodos(meses: list[dict]) -> list[dict]:
    """Inflación implícita acumulada en períodos: próximos 3 y 6 meses, trimestres y todo el tramo."""
    if not meses:
        return []
    idx = {m["mes"]: m for m in meses}
    claves = [m["mes"] for m in meses]
    cand = []
    for n in (3, 6):
        if len(claves) >= n:
            cand.append((f"Próximos {n} meses", claves[:n]))
    for y in sorted({k[:4] for k in claves}):
        for q, ms in enumerate((("01", "02", "03"), ("04", "05", "06"), ("07", "08", "09"), ("10", "11", "12")), 1):
            ks = [f"{y}-{m}" for m in ms]
            if all(k in idx for k in ks):
                cand.append((f"{q}º trimestre {y}", ks))
    cand.append((f"Todo el tramo ({nom_mes(claves[0])} a {nom_mes(claves[-1])})", claves))
    out, vistos = [], set()
    for nombre, ks in cand:
        if tuple(ks) in vistos:
            continue
        vistos.add(tuple(ks))
        merc = acumular([idx[k]["mensual"] for k in ks])
        rems = [idx[k].get("rem_mensual") for k in ks]
        rem = acumular(rems) if all(r is not None for r in rems) else None
        n = len(ks)
        out.append({"nombre": nombre, "meses": f"{nom_mes(ks[0])} a {nom_mes(ks[-1])}" if n > 1 else nom_mes(ks[0]),
                    "acumulada": merc, "mensual": ((1 + merc / 100) ** (1 / n) - 1) * 100,
                    "rem": rem, "rem_mensual": ((1 + rem / 100) ** (1 / n) - 1) * 100 if rem is not None else None,
                    "promedio_bloque": any(not idx[k]["vencimiento_propio"] for k in ks)})
    return out


# ---------- gráficos (SVG simple, sin librerías) ----------
def svg_curva(series: list[dict], titulo_y: str) -> str:
    W, H, ml, mr, mt, mb = 640, 300, 52, 16, 14, 40
    pts = [p for s in series for p in s["puntos"]]
    if not pts:
        return ""
    xmax = max(p[0] for p in pts) * 1.05
    ys = [p[1] for p in pts]
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.15 or 1
    ymin, ymax = ymin - pad, ymax + pad
    X = lambda d: ml + d / xmax * (W - ml - mr)
    Y = lambda v: mt + (ymax - v) / (ymax - ymin) * (H - mt - mb)
    g = [f'<svg viewBox="0 0 {W} {H}" class="graf" role="img" aria-label="{html.escape(titulo_y)}">']
    paso = 5 if ymax - ymin > 12 else 2 if ymax - ymin > 5 else 1
    v = math.ceil(ymin / paso) * paso
    while v <= ymax:
        g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                 f'<text x="{ml - 6}" y="{Y(v) + 4:.1f}" class="eje" text-anchor="end">{v:g}%</text>')
        v += paso
    for d in range(0, int(xmax) + 1, 60):
        g.append(f'<text x="{X(d):.1f}" y="{H - mb + 16}" class="eje" text-anchor="middle">{d}</text>')
    g.append(f'<text x="{(ml + W - mr) / 2}" y="{H - 6}" class="eje" text-anchor="middle">días al vencimiento</text>')
    for s in series:
        ok = sorted(p for p in s["puntos"] if not p[3])
        if len(ok) > 1:
            g.append(f'<polyline class="linea {s["clase"]}" points="' + " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v, _, _ in ok) + '"/>')
        for d, v, tk, fuera in s["puntos"]:
            g.append(f'<circle cx="{X(d):.1f}" cy="{Y(v):.1f}" r="4.5" class="pt {s["clase"]}{" fuera" if fuera else ""}">'
                     f'<title>{tk}: {v:.2f}% a {d} días{" (fuera de la curva)" if fuera else ""}</title></circle>'
                     f'<text x="{X(d):.1f}" y="{Y(v) - 9:.1f}" class="lbl" text-anchor="middle">{tk}</text>')
    g.append("</svg>")
    return "".join(g)


# ---------- dólar que espera el mercado ----------
# Techo de la banda cambiaria (BCRA, desde ene-2026): sube cada mes por la inflación de dos meses antes, repartida
# día a día. Valores publicados para fin de nov-26 y dic-26; de ahí en adelante se proyecta con la inflación del REM.
TECHO_PUBLICADO = {date(2026, 11, 30): 1989.12, date(2026, 12, 31): 2032.88}
MIN_OPS_DL = 100   # un dollar linked con menos operaciones deja el tramo como "poco confiable"


def _fin_mes(y: int, m: int) -> date:
    return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)


def techo_banda(rem: dict | None, hasta: date) -> dict[date, float]:
    import expectativas as X
    pts = dict(TECHO_PUBLICADO)
    d = max(pts)
    while d < hasta and rem:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        ref = f"{y}-{m - 2:02d}" if m > 2 else f"{y - 1}-{m + 10:02d}"
        inf = X.inflacion_rem(rem, ref)
        if inf is None:
            break
        nuevo = _fin_mes(y, m)
        pts[nuevo] = pts[d] * (1 + inf / 100)
        d = nuevo
    return pts


def valor_techo(pts: dict[date, float], d: date) -> float | None:
    ks = sorted(pts)
    if d > ks[-1]:
        return None
    if d <= ks[0]:
        return pts[ks[0]]
    for a, b in zip(ks, ks[1:]):
        if a <= d <= b:
            w = (d - a).days / (b - a).days
            return math.exp(math.log(pts[a]) * (1 - w) + math.log(pts[b]) * w)
    return None


def dolar_esperado(d: dict, rem: dict | None) -> dict:
    """A3500 que el mercado tiene en el precio: LECAP/BONCAP contra dollar linked al mismo plazo.
    A3500 esperado = A3500 hoy x ((1 + TIR fija) / (1 + TIR DL)) ^ (días / 365)."""
    import expectativas as X
    import instrumentos as I
    filas, liq = d["filas"], d["liq"]
    sin_cupon = lambda f: not (f.get("calc") or {}).get("cupon")
    # Los dollar linked a menos de 30 días dan TIR sin sentido (el dólar de pago ya casi está fijado): afuera.
    dl = sorted([f for f in filas if f["curva"] == "dolar_linked" and not f["revisar"] and f["dias"] >= 30], key=lambda f: f["dias"])
    fija = sorted([f for f in filas if f["curva"] == "pesos_fija" and not f["revisar"] and f["dias"] > 30 and sin_cupon(f)],
                  key=lambda f: f["dias"])
    hoy = I.valor_en(d["a3500"], liq)
    rem_t = (rem or {}).get("total")
    techo = techo_banda(rem_t, date.fromisoformat(fija[-1]["vto"]) if fija else liq)
    out = []
    for f in fija:
        t_dl = I.interpolar([(x["dias"], x["tir"]) for x in dl], f["dias"])
        if t_dl is None:
            continue
        vec = [x for x in dl if x["dias"] == f["dias"]] or \
              [x for a, b in zip(dl, dl[1:]) if a["dias"] <= f["dias"] <= b["dias"] for x in (a, b)][:2]
        ftc = I.restar_habiles(date.fromisoformat(f["vto"]), 3)
        tc = hoy * ((1 + f["tir"] / 100) / (1 + t_dl / 100)) ** (f["dias"] / 365)
        out.append({"ticker": f["ticker"], "vto": f["vto"], "dias": f["dias"], "fecha_tc": ftc, "tir_fija": f["tir"], "tir_dl": t_dl,
                    "vecinos": [x["ticker"] for x in vec], "confiable": all((x.get("ops") or 0) >= MIN_OPS_DL for x in vec),
                    "tc": tc, "suba": (tc / hoy - 1) * 100,
                    "rem": X.tc_rem(rem_t, ftc) if rem_t else None, "techo": valor_techo(techo, ftc)})
    prev_tc, prev_d = hoy, liq
    for e in out:   # devaluación mensual del tramo entre este vencimiento y el anterior
        meses = (e["fecha_tc"] - prev_d).days / (365 / 12)
        e["mens_tramo"] = ((e["tc"] / prev_tc) ** (1 / meses) - 1) * 100 if meses > 0.3 else None
        prev_tc, prev_d = e["tc"], e["fecha_tc"]
    return {"hoy": hoy, "liq": liq, "puntos": out, "techo": techo}


def svg_dolar(dd: dict) -> str:
    pts = dd["puntos"]
    if not pts:
        return ""
    W, H, ml, mr, mt, mb = 700, 330, 60, 16, 16, 40
    d0, hoy = dd["liq"], dd["hoy"]
    dias_tc = lambda p: (p["fecha_tc"] - d0).days
    xmax = max(dias_tc(p) for p in pts) * 1.04
    vals = [hoy] + [p["tc"] for p in pts] + [p["rem"] for p in pts if p["rem"]] + [p["techo"] for p in pts if p["techo"]]
    ymin, ymax = math.floor(min(vals) / 100) * 100, math.ceil(max(vals) * 1.02 / 100) * 100
    X = lambda dias: ml + dias / xmax * (W - ml - mr)
    Y = lambda v: mt + (ymax - v) / (ymax - ymin) * (H - mt - mb)
    g = [f'<svg viewBox="0 0 {W} {H}" class="graf" role="img" aria-label="Dólar oficial que espera el mercado">']
    paso = 100 if ymax - ymin <= 800 else 200
    v = ymin
    while v <= ymax:
        g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                 f'<text x="{ml - 6}" y="{Y(v) + 4:.1f}" class="eje" text-anchor="end">${v:,.0f}</text>'.replace(",", "."))
        v += paso
    y, m = d0.year, d0.month
    while True:
        m += 3
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        dias = (date(y, m, 1) - d0).days
        if dias > xmax:
            break
        g.append(f'<text x="{X(dias):.1f}" y="{H - mb + 16}" class="eje" text-anchor="middle">{MESES[m - 1]}-{str(y)[2:]}</text>')
    linea = lambda xs, cls: f'<polyline class="{cls}" points="' + " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in xs) + '"/>'
    techo = [(0, valor_techo(dd["techo"], d0) or hoy)] + [(dias_tc(p), p["techo"]) for p in pts if p["techo"]]
    g.append(linea(techo, "techo"))
    rem = [(0, hoy)] + [(dias_tc(p), p["rem"]) for p in pts if p["rem"]]
    if len(rem) > 1:
        g.append(linea(rem, "rem"))
    g.append(linea([(0, hoy)] + [(dias_tc(p), p["tc"]) for p in pts], "linea c1"))
    g.append(f'<circle cx="{X(0):.1f}" cy="{Y(hoy):.1f}" r="4.5" class="pt hoy"><title>Hoy: ${hoy:,.2f}</title></circle>'
             f'<text x="{X(0) + 8:.1f}" y="{Y(hoy) + 14:.1f}" class="lbl">hoy</text>')
    for p in pts:
        cls = "pt c1" + ("" if p["confiable"] else " fuera")
        g.append(f'<circle cx="{X(dias_tc(p)):.1f}" cy="{Y(p["tc"]):.1f}" r="4.5" class="{cls}"><title>{p["ticker"]}: '
                 f'${p["tc"]:,.0f} al {p["fecha_tc"]:%d/%m/%Y}</title></circle>'.replace(",", "."))
    g.append("</svg>")
    return "".join(g)


def seccion_dolar(d: dict, rem: dict | None) -> str:
    try:
        dd = dolar_esperado(d, rem)
    except Exception as e:  # noqa: BLE001  (sin esta sección la página sale igual)
        print("dólar esperado:", e)
        return ""
    pts = dd["puntos"]
    if not pts:
        return ""
    plata = lambda v: "–" if v is None else f"${v:,.0f}".replace(",", ".")
    conf = [p for p in pts if p["confiable"]]
    ref = conf[-1] if conf else pts[-1]
    corto = min(pts, key=lambda p: abs(p["dias"] - 90))

    def kpi(p, titulo):
        meses = p["dias"] / (365 / 12)
        mens = ((1 + p["suba"] / 100) ** (1 / meses) - 1) * 100
        r = ""
        if p["rem"]:
            dif = (p["tc"] / p["rem"] - 1) * 100
            r = (f'<div class="mut">REM {plata(p["rem"])} · el mercado espera un dólar {abs(dif):.1f}% '
                 f'{"más alto" if dif > 0 else "más bajo"}</div>').replace(".", ",").replace(",", ".", 0)
        return (f'<div class="kpi"><div class="mut">{titulo} <span class="mut">({fecha(p["fecha_tc"].isoformat())}, {p["ticker"]})</span></div>'
                f'<div class="big">{plata(p["tc"])}</div><div>{pct(p["suba"], 1)} de suba · {pct(mens)} por mes</div>{r}</div>')
    kpis = kpi(corto, "En unos 3 meses") + (kpi(ref, "El plazo más largo con dollar linked líquidos") if ref is not corto else "")
    txt = []
    con_rem = [p for p in conf if p["rem"]]
    if con_rem:
        mas = sum(p["tc"] > p["rem"] for p in con_rem)
        if mas == len(con_rem):
            txt.append("El mercado espera <b>más devaluación que los economistas del REM</b> en todos los plazos")
        elif mas == 0:
            txt.append("El mercado espera <b>menos devaluación que los economistas del REM</b> en todos los plazos")
        else:
            txt.append(f"El mercado espera más devaluación que el REM en {mas} de {len(con_rem)} plazos")
    con_techo = [p for p in pts if p["techo"]]
    if con_techo:
        holgura = min((p["techo"] / p["tc"] - 1) * 100 for p in con_techo)
        txt.append(f"el dólar implícito queda siempre debajo del techo de la banda (en el plazo más justo, {pct(holgura, 1)} por debajo)"
                   if holgura > 0 else "en algún plazo el dólar implícito <b>pasa el techo de la banda</b>: el mercado duda de que el esquema se sostenga")
    lectura = ("<p>" + "; ".join(txt) + ".</p>") if txt else ""
    filas = "".join(
        f'<tr class="{"" if p["confiable"] else "gris"}"><td><b>{p["ticker"]}</b></td><td>{fecha(p["fecha_tc"].isoformat())}</td>'
        f'<td class="n">{pct(p["tir_fija"])}</td><td class="n">{pct(p["tir_dl"])}</td><td>{" / ".join(p["vecinos"])}</td>'
        f'<td class="n">{plata(p["tc"])}</td><td class="n">{pct(p["suba"], 1)}</td><td class="n">{pct(p["mens_tramo"])}</td>'
        f'<td class="n">{plata(p["rem"])}</td><td class="n">{plata(p["techo"])}</td>'
        f'<td>{"" if p["confiable"] else "Dollar linked con pocas operaciones: indicativo"}</td></tr>' for p in pts)
    return f"""<h2>Dólar que espera el mercado</h2>
<p>Comparando lo que rinde una LECAP/BONCAP (pesos) con lo que rinde un bono <i>dollar linked</i> (pesos que siguen al dólar oficial) al mismo
plazo, sale el dólar oficial (A3500) que el mercado tiene "en el precio". Si un bono en pesos rinde 30% y uno atado al dólar rinde 2%, la
diferencia es la suba del dólar que hace que dé lo mismo tener cualquiera de los dos. Hoy el A3500 está en <b>{plata(dd["hoy"])}</b>.</p>
<div class="kpis">{kpis}</div>
{lectura}
<div><span class="ley"><span class="lin"></span>mercado</span><span class="ley"><span class="lin rem"></span>REM (economistas, BCRA)</span><span class="ley"><span class="lin techo"></span>techo de la banda</span></div>
{svg_dolar(dd)}
<p class="mut">Círculo vacío = tramo indicativo: los dollar linked de ese plazo operan poco. El techo de la banda es el publicado por el BCRA
hasta {fecha(max(TECHO_PUBLICADO).isoformat())}; después se proyecta con la inflación del REM (sube por la inflación de dos meses antes).</p>
<details class="detalle"><summary>Ver el detalle bono por bono</summary>
<div class="tabla"><table><tr><th>LECAP/BONCAP</th><th>Dólar del día</th><th class="n">TIR fija</th><th class="n">TIR dollar linked</th><th>Dollar linked usados</th>
<th class="n">Dólar implícito</th><th class="n">Suba desde hoy</th><th class="n">Por mes en el tramo</th><th class="n">REM</th><th class="n">Techo banda</th><th></th></tr>{filas}</table></div>
<p class="mut">"Dólar del día": el dollar linked paga el A3500 de 3 días hábiles antes del vencimiento. "Por mes en el tramo": devaluación mensual
implícita entre ese vencimiento y el anterior.</p>
</details>"""

# ---------- lo que hizo la IA ----------
def datos_ia() -> dict:
    """Resumen de todo lo que leyó la IA, sus controles automáticos y su costo (para la sección de la página)."""
    import leer_con_ia as L
    idx = json.loads((L.FUENTES / "indice.json").read_text(encoding="utf-8"))
    lect = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in (L.SALIDA / "opus").glob("*.json")}
    ultimas = sorted(lect, key=lambda i: idx.get(i, {}).get("fecha", ""), reverse=True)[:5]
    uso_f = L.SALIDA / "uso.json"
    uso = json.loads(uso_f.read_text(encoding="utf-8")) if uso_f.exists() else {}
    ctrl_f = BASE / "datos" / "controles_ia.json"
    return {
        "resoluciones": len(lect), "titulos": sum(len(v.get("titulos", [])) for v in lect.values()),
        "licitaciones": len(list((L.SALIDA / "resultados").glob("*.json"))),
        "ultimas": [{"fecha": idx.get(i, {}).get("fecha", ""), "titulo": idx.get(i, {}).get("titulo", i),
                     "url": idx.get(i, {}).get("url", ""),
                     "emite": [t["nombre"] for t in lect[i].get("titulos", [])]} for i in ultimas],
        "costo": {k: v.get("costo_usd") or 0 for k, v in uso.items() if k != "haiku"},
        "control": json.loads(ctrl_f.read_text(encoding="utf-8")) if ctrl_f.exists() else None,
    }


def bloque_parrafo(res: dict | None) -> str:
    if not res:
        return ""
    if res.get("parrafo"):
        ctrl = "cada número controlado contra los datos"
        if res["control"]["intentos"] > 1:
            ctrl += " (corregido en el 2º intento)"
        return (f'<div class="ia"><div class="mut">Qué pasó hoy · escrito por IA ({res.get("modelo", "Claude")}) · {ctrl} · '
                f'costo US$ {res.get("costo_usd", 0):.3f}</div><p>{html.escape(res["parrafo"])}</p></div>')
    if res.get("control", {}).get("intentos"):
        return ('<div class="ia"><div class="mut">Qué pasó hoy</div><p>Hoy no hay párrafo: el texto de la IA citó números que no '
                'estaban en los datos y no pasó el control automático.</p></div>')
    return ""


def seccion_ia(ia: dict, res: dict | None) -> str:
    c = ia["control"] or {}
    costo_total = sum(ia["costo"].values())
    reglas = "".join(f'<tr><td>{html.escape(r)}</td><td class="n">{g["ok"]} de {g["total"]}</td></tr>'
                     for r, g in c.get("por_regla", {}).items())
    fallas = c.get("fallas", [])
    if fallas:
        items = "".join(f'<li>{html.escape(f["fuente"])} · {html.escape(f["titulo"])}: {html.escape(f["regla"])} '
                        f'({html.escape(f["detalle"])})</li>' for f in fallas[:10])
        t_fallas = f"<p><b>Fallas:</b></p><ul>{items}</ul>"
    else:
        t_fallas = '<p class="mut">Sin fallas.</p>'
    ultimas = "".join(
        f'<tr><td class="n">{u["fecha"][6:8]}/{u["fecha"][4:6]}/{u["fecha"][:4]}</td>'
        f'<td><a href="{html.escape(u["url"])}">{html.escape(u["titulo"])}</a>'
        f'<div class="mut">{html.escape("; ".join(u["emite"]) or "no emite títulos nuevos")}</div></td></tr>'
        for u in ia["ultimas"])
    hoy = f' · el párrafo de hoy, US$ {res["costo_usd"]:.3f}' if res and res.get("costo_usd") else ""
    return f"""<h2>Qué hizo la IA</h2>
<p>La IA hace el trabajo de lectura que hoy un analista hace a mano, y escribe el resumen del día. <b>No calcula ningún número de
esta página</b>: las tasas, la inflación implícita y los rangos salen de fórmulas fijas (ver "Cómo se calcula").</p>
<div class="tabla"><table><tr><th>Tarea</th><th>Qué lee</th><th>Qué devuelve</th><th class="n">Hasta hoy</th></tr>
<tr><td><b>Condiciones de cada bono</b></td><td>Resoluciones de emisión del Boletín Oficial</td><td>Vencimiento, tipo de ajuste, cupones, amortización y rezago del CER, en una tabla fija</td><td class="n">{ia["resoluciones"]} resoluciones · {ia["titulos"]} títulos</td></tr>
<tr><td><b>Tasa de cada LECAP/BONCAP</b></td><td>Comunicados de resultado de licitación de Economía</td><td>TEM, precio de corte y TIREA de cada título</td><td class="n">{ia["licitaciones"]} comunicados</td></tr>
<tr><td><b>Párrafo "Qué pasó hoy"</b></td><td>Los números del día que arma el programa</td><td>3 a 5 oraciones, solo con esos números</td><td class="n">1 por rueda</td></tr>
</table></div>
<h3>Cómo se controla</h3>
<ol>
<li><b>Contra el texto oficial, sin IA.</b> Cada dato leído se revisa con reglas fijas: el vencimiento del nombre del título es el leído,
las cuotas suman 100%, los pagos caen entre emisión y vencimiento, el ticker corresponde a la fecha, y cada tasa y precio aparece escrito
en el documento. Resultado: <b>{c.get("ok", 0)} de {c.get("total", 0)} controles OK</b>.</li>
<li><b>Los controles se prueban</b> con lecturas adulteradas a propósito (vencimiento cambiado, tasa inventada, cuotas que no suman):
si un control no las detecta, las pruebas fallan.</li>
<li><b>El párrafo diario</b> se revisa número por número contra los datos del día. Si cita uno que no está, se le pide corregir una vez;
si vuelve a fallar, ese día no se publica.</li>
<li><b>Nunca se paga dos veces</b> la misma lectura: cada documento leído queda guardado.</li>
</ol>
<details><summary>Detalle por control</summary><div class="tabla"><table><tr><th>Control</th><th class="n">OK</th></tr>{reglas}</table></div>
{t_fallas}</details>
<p class="mut">Costo acumulado de la IA (Claude Opus): US$ {costo_total:.2f}{hoy}.</p>
<h3>Últimas resoluciones leídas</h3>
<div class="tabla"><table><tr><th class="n">Fecha</th><th>Resolución y lo que emite</th></tr>{ultimas}</table></div>
"""


# ---------- página ----------
def fila_bono(f: dict, extra: str) -> str:
    motivo = html.escape(f.get("motivo") or "") if f["revisar"] else ""
    return (f'<tr class="{"gris" if f["revisar"] else ""}"><td><b>{f["ticker"]}</b></td><td>{fecha(f["vto"])}</td>'
            f'<td class="n">{f["dias"]}</td><td class="n">{f["precio"]:,.2f}</td><td class="n">{pct(f["tir"])}</td>{extra}'
            f'<td class="n">{f.get("ops") or 0}</td><td class="n">{pct(f.get("spread"))}</td><td>{"Fuera: " + motivo if motivo else "En la curva"}</td></tr>')


def armar(d: dict, ns: dict | None, rem: dict | None, preliminar: bool, ia: dict | None = None, res: dict | None = None) -> str:
    filas = d["filas"]
    fija = sorted([f for f in filas if f["curva"] == "pesos_fija" and not (f.get("calc") or {}).get("cupon")], key=lambda f: f["dias"])
    cer = sorted([f for f in filas if f["curva"] == "cer"], key=lambda f: f["dias"])
    usados = {b["ticker"] for b in (ns or {}).get("cer_bonos", [])}
    afuera = {a["ticker"]: a["motivo"] for a in (ns or {}).get("cer_afuera", [])}
    tem = lambda f: ((1 + f["tir"] / 100) ** (30 / 365) - 1) * 100
    meses = (ns or {}).get("meses", [])
    pers = periodos(meses)

    t_fija = "".join(fila_bono(f, f'<td class="n">{pct(tem(f))}</td>') for f in fija)

    def uso_cer(f):
        if f["revisar"]:
            return "–"
        if (f.get("calc") or {}).get("cupon"):
            return "No (tiene cupón)"
        if f["ticker"] in usados:
            return "Sí"
        if f["ticker"] in afuera:
            return "No: vence después de la última LECAP/BONCAP"
        return "No: su ajuste CER ya está publicado"
    t_cer = "".join(fila_bono(f, f'<td>{uso_cer(f)}</td>') for f in cer)

    t_per = "".join(
        f'<tr><td><b>{p["nombre"]}</b><div class="mut">{p["meses"]}</div></td><td class="n">{pct(p["acumulada"])}</td>'
        f'<td class="n">{pct(p["mensual"])}</td><td class="n">{pct(p["rem"])}</td><td class="n">{pct(p["rem_mensual"])}</td></tr>'
        for p in pers)

    kp = {p["nombre"]: p for p in pers}
    def kpi(nombre):
        p = kp.get(nombre)
        if not p:
            return ""
        dif = "" if p["rem_mensual"] is None else f'<div class="mut">REM {pct(p["rem_mensual"])} por mes · diferencia {p["mensual"] - p["rem_mensual"]:+.2f} pp</div>'.replace(".", ",")
        return (f'<div class="kpi"><div class="mut">{nombre} <span class="mut">({p["meses"]})</span></div>'
                f'<div class="big">{pct(p["mensual"])} <small>por mes</small></div><div>{pct(p["acumulada"])} acumulada</div>{dif}</div>')

    g_fija = svg_curva([{"clase": "c1", "puntos": [(f["dias"], f["tir"], f["ticker"], f["revisar"]) for f in fija]}], "TIR tasa fija")
    g_cer = svg_curva([{"clase": "c2", "puntos": [(f["dias"], f["tir"], f["ticker"], f["revisar"])
                                                 for f in cer if not (f.get("calc") or {}).get("cupon") and f["dias"] < 1000]}], "TIR real CER")
    origenes = {f.get("puntas") for f in fija + cer if not f["revisar"]}
    if origenes and all(str(o).startswith("rueda") for o in origenes):
        nota_puntas = ('<div class="nota"><b>Puntas tomadas durante la rueda</b> (mediana de las fotos de las 16:00, 16:20 y 16:40). '
                       f'Un bono queda afuera si tuvo menos de {A.MIN_OPERACIONES} operaciones o más de {A.MAX_SPREAD_PCT:g}% entre puntas.</div>')
    else:
        nota_puntas = ('<div class="nota"><b>Ojo: hoy no hay puntas de la rueda</b>, se usan las del cierre. Después de las 17 casi no quedan '
                       'ofertas y la distancia entre comprador y vendedor se agranda (la T30A7 tuvo 1.113 operaciones y 8,6% entre puntas). '
                       f'Por eso hoy un bono queda afuera solo si tuvo menos de {A.MIN_OPERACIONES} operaciones, y el rango por puntas '
                       'sale más ancho de lo real. Desde la próxima rueda se toman durante el día.</div>')
    rem_txt = f'REM del BCRA, edición {rem["edicion"]} (mediana)' if rem else "REM no disponible"
    cierre = date.fromisoformat(d["cierre"]).strftime("%d/%m/%Y")
    hasta = nom_mes(meses[-1]["mes"]) if meses else "–"
    aviso = ('<div class="aviso">Vista preliminar con precios de una rueda abierta: no es la versión publicada.</div>'
             if preliminar else "")

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curva soberana en pesos</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#e4e1da;--card:#fff;--c1:#1f5fa8;--c2:#b4541a;--rem:#111;--prom:#9cb7d8;--warn:#b45309}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151514;--fg:#ecebe7;--mut:#9d9b95;--line:#2f2e2b;--card:#1d1c1a;--c1:#6ea6e6;--c2:#e48a52;--rem:#f2f2f2;--prom:#3d5f86}}}}
:root[data-theme="dark"]{{--bg:#151514;--fg:#ecebe7;--mut:#9d9b95;--line:#2f2e2b;--card:#1d1c1a;--c1:#6ea6e6;--c2:#e48a52;--rem:#f2f2f2;--prom:#3d5f86}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:960px;margin:0 auto;padding:24px 16px 60px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:36px 0 6px;padding-top:8px;border-top:1px solid var(--line)}}
.mut{{color:var(--mut);font-size:13px}} .tag{{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:8px;padding:0 5px;margin-left:4px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:18px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}} .big{{font-size:26px;font-weight:650}} .big small{{font-size:13px;font-weight:400;color:var(--mut)}}
.tabla{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-size:12px;color:var(--mut);font-weight:600}}
td.n,th.n{{text-align:right;white-space:nowrap}} tr.gris td{{color:var(--mut)}}
.graf{{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}}
.grid{{stroke:var(--line)}} .eje{{fill:var(--mut);font-size:11px}} .lbl{{fill:var(--fg);font-size:10px}}
.linea{{fill:none;stroke-width:2}} .linea.c1{{stroke:var(--c1)}} .linea.c2{{stroke:var(--c2)}}
.pt.c1{{fill:var(--c1)}} .pt.c2{{fill:var(--c2)}} .pt.fuera{{fill:var(--bg);stroke:var(--mut);stroke-width:1.5}}
.barra{{fill:var(--c1)}} .barra.prom{{fill:var(--prom)}} .banda{{stroke:var(--fg);stroke-width:1.2;opacity:.6}} .rem{{stroke:var(--rem);stroke-width:2.5;stroke-dasharray:5 3}}
.aviso{{background:var(--warn);color:#fff;padding:8px 16px;font-weight:600}}
.ia{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c2);border-radius:8px;padding:10px 14px;margin:16px 0}} .ia p{{margin:6px 0 0}}
details{{margin:8px 0}} summary{{cursor:pointer;color:var(--c1)}} a{{color:var(--c1)}}
.pt.hoy{{fill:var(--fg)}} .techo{{fill:none;stroke:var(--warn);stroke-width:1.8;stroke-dasharray:2 3}} polyline.rem{{fill:none}}
.ley{{margin-right:16px;font-size:13px;white-space:nowrap}} .lin{{display:inline-block;width:22px;height:0;border-top:2.5px solid var(--c1);margin-right:6px;vertical-align:middle}}
.lin.rem{{border-top:2.5px dashed var(--rem)}} .lin.techo{{border-top:2px dotted var(--warn)}}
.detalle summary{{display:inline-block;list-style:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:9px 16px;font-weight:600;color:var(--fg)}}
.detalle summary::-webkit-details-marker{{display:none}} .detalle summary::before{{content:"▸ ";color:var(--mut)}} .detalle[open] summary::before{{content:"▾ "}}
.nota{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c1);border-radius:8px;padding:10px 14px;margin:12px 0}}
ol li{{margin:4px 0}}
</style></head><body>{aviso}<main>
<h1>Curva de bonos soberanos en pesos</h1>
<div class="mut">Precios de cierre del {cierre} (data912) · CER publicado hasta {fecha(d["cer"] and max(d["cer"]).isoformat())} (BCRA) · {rem_txt} · generado {datetime.now().strftime("%d/%m/%Y %H:%M")}</div>

{bloque_parrafo(res)}
<h2>Inflación que espera el mercado</h2>
<p>Comparando lo que rinde un bono a tasa fija con lo que rinde un bono que ajusta por inflación (CER) al mismo plazo, sale la inflación
que el mercado tiene "en el precio". Llega hasta <b>{hasta}</b>: más allá no hay LECAP/BONCAP para comparar.</p>
<div class="kpis">{kpi("Próximos 3 meses")}{kpi("Próximos 6 meses")}</div>

<h3>Por período</h3>
<div class="tabla"><table><tr><th>Período</th><th class="n">Mercado acumulada</th><th class="n">Mercado por mes</th><th class="n">REM acumulada</th><th class="n">REM por mes</th></tr>{t_per}</table></div>

{seccion_dolar(d, rem)}

<h2>Curva a tasa fija (LECAP y BONCAP)</h2>
{g_fija}
<div class="tabla"><table><tr><th>Bono</th><th>Vence</th><th class="n">Días</th><th class="n">Precio</th><th class="n">TIR (TEA)</th><th class="n">TEM</th><th class="n">Operaciones</th><th class="n">Entre puntas</th><th>Estado</th></tr>{t_fija}</table></div>

<h2>Curva CER (tasa real)</h2>
{g_cer}
<div class="tabla"><table><tr><th>Bono</th><th>Vence</th><th class="n">Días</th><th class="n">Precio</th><th class="n">TIR real</th><th>¿Se usa para la inflación?</th><th class="n">Operaciones</th><th class="n">Entre puntas</th><th>Estado</th></tr>{t_cer}</table></div>

<h2>Cómo se calcula</h2>
<ol>
<li><b>Tasa fija al plazo de cada bono CER</b>: la LECAP/BONCAP que vence el mismo día; si no hay, interpolación lineal entre las dos vecinas. No se extrapola.</li>
<li><b>Inflación que paga cada bono CER</b>: (1 + tasa fija)<sup>t</sup> ÷ (1 + TIR real)<sup>t</sup>, menos la parte del CER que ya está publicada.</li>
<li><b>Bloques</b>: entre dos vencimientos CER seguidos, la inflación promedio por mes de ese tramo. Es lo único que los bonos dicen exactamente.</li>
<li><b>Períodos</b>: la inflación de cada período (3 meses, 6 meses, trimestre) se arma encadenando los bloques que lo cubren. Si un bloque abarca varios meses, los bonos solo dicen el total de esos meses juntos, no cómo se reparte.</li>
<li><b>Regla del CER</b>: la inflación del mes M mueve el CER del 16 de M+1 al 15 de M+2 (BCRA); el bono usa el CER de 10 días hábiles antes de vencer.</li>
<li><b>Rango por puntas</b>: el resultado moviendo cada precio media punta (comprador/vendedor).</li>
</ol>
{nota_puntas}
{seccion_ia(ia, res) if ia else ''}
</main></body></html>"""


def resumen_ia_del_dia(resumen_ia, d: dict, ns: dict | None, rem: dict | None) -> dict | None:
    idx = json.loads((BASE / "fuentes" / "boletin" / "indice.json").read_text(encoding="utf-8"))
    nh = BASE / "datos" / "normas_hoy.json"
    hoy = json.loads(nh.read_text(encoding="utf-8")) if nh.exists() else {}
    ids = hoy.get("ids", []) if hoy.get("fecha") == date.today().isoformat() else []
    normas = [idx.get(i, {}).get("titulo", i) for i in ids]
    hechos = resumen_ia.armar_hechos(d["cierre"], periodos((ns or {}).get("meses", [])), d["filas"],
                                     rem and rem.get("edicion"), normas)
    try:
        return resumen_ia.generar(hechos)
    except Exception as e:  # noqa: BLE001 - sin párrafo la página sale igual
        print("párrafo de IA: falló", e)
        return None


def main() -> None:
    # Igual que actualizar.py: la página publicada solo con la rueda cerrada (después de las 17:30 o día no hábil).
    # Antes de las 11 data912 ya muestra la rueda del día con pocas operaciones: tampoco sirve.
    preliminar = not A.rueda_cerrada()
    d = A.cargar()
    rem = R.cargar()
    ns = N.calcular(d["filas"], d["liq"], d["cer"], rem)
    import controles_ia
    controles_ia.correr()
    ia = datos_ia()
    res = None
    if not preliminar:                      # el párrafo de la IA solo con precios de cierre
        import resumen_ia
        res = resumen_ia_del_dia(resumen_ia, d, ns, rem)
    destino = BASE / "docs" / ("pesos_preliminar.html" if preliminar else "pesos.html")
    destino.write_text(armar(d, ns, rem, preliminar, ia, res), encoding="utf-8")
    print("página:", destino.name, "| meses:", len((ns or {}).get("meses", [])))


if __name__ == "__main__":
    main()
