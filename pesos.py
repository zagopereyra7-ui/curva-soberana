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
from datetime import date, datetime
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


def svg_barras(meses: list[dict]) -> str:
    W, H, ml, mr, mt, mb = 640, 260, 44, 10, 14, 34
    n = len(meses)
    if not n:
        return ""
    vals = [m["mensual_max"] for m in meses] + [m.get("rem_mensual") or 0 for m in meses]
    ymax = max(vals) * 1.1
    ymin = min(0, min(m["mensual_min"] for m in meses))
    Y = lambda v: mt + (ymax - v) / (ymax - ymin) * (H - mt - mb)
    bw = (W - ml - mr) / n
    g = [f'<svg viewBox="0 0 {W} {H}" class="graf" role="img" aria-label="Inflación implícita mensual vs REM">']
    v = 0.0
    while v <= ymax:
        g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                 f'<text x="{ml - 6}" y="{Y(v) + 4:.1f}" class="eje" text-anchor="end">{v:.1f}%</text>')
        v += 0.5
    for i, m in enumerate(meses):
        x0 = ml + i * bw
        cx = x0 + bw * 0.38
        g.append(f'<rect x="{x0 + bw * 0.14:.1f}" y="{Y(m["mensual"]):.1f}" width="{bw * 0.48:.1f}" '
                 f'height="{Y(0) - Y(m["mensual"]):.1f}" class="barra{"" if m["vencimiento_propio"] else " prom"}">'
                 f'<title>{nom_mes(m["mes"])}: mercado {m["mensual"]:.2f}% (puntas {m["mensual_min"]:.2f}–{m["mensual_max"]:.2f})</title></rect>'
                 f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{Y(m["mensual_min"]):.1f}" y2="{Y(m["mensual_max"]):.1f}" class="banda"/>'
                 f'<text x="{cx:.1f}" y="{Y(m["mensual"]) - 5:.1f}" class="lbl" text-anchor="middle">{m["mensual"]:.2f}</text>')
        if m.get("rem_mensual") is not None:
            ry = Y(m["rem_mensual"])
            g.append(f'<line x1="{x0 + bw * 0.08:.1f}" x2="{x0 + bw * 0.92:.1f}" y1="{ry:.1f}" y2="{ry:.1f}" class="rem">'
                     f'<title>REM {m["rem_mensual"]:.2f}%</title></line>')
        g.append(f'<text x="{x0 + bw / 2:.1f}" y="{H - mb + 18}" class="eje" text-anchor="middle">{nom_mes(m["mes"])}</text>')
    g.append("</svg>")
    return "".join(g)


# ---------- página ----------
def fila_bono(f: dict, extra: str) -> str:
    motivo = html.escape(f.get("motivo") or "") if f["revisar"] else ""
    return (f'<tr class="{"gris" if f["revisar"] else ""}"><td><b>{f["ticker"]}</b></td><td>{fecha(f["vto"])}</td>'
            f'<td class="n">{f["dias"]}</td><td class="n">{f["precio"]:,.2f}</td><td class="n">{pct(f["tir"])}</td>{extra}'
            f'<td class="n">{f.get("ops") or 0}</td><td class="n">{pct(f.get("spread"))}</td><td>{"Fuera: " + motivo if motivo else "En la curva"}</td></tr>')


def armar(d: dict, ns: dict | None, rem: dict | None, preliminar: bool) -> str:
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

    t_meses = "".join(
        f'<tr><td><b>{nom_mes(m["mes"])}</b>{"" if m["vencimiento_propio"] else " <span class=tag>promedio de su bloque</span>"}</td>'
        f'<td class="n">{pct(m["mensual"])}</td><td class="n mut">{pct(m["mensual_min"])} a {pct(m["mensual_max"])}</td>'
        f'<td class="n">{pct(m["anual"], 1)}</td><td class="n">{pct(m.get("rem_mensual"))}</td>'
        f'<td class="n">{"–" if m.get("rem_mensual") is None else f"{m["mensual"] - m["rem_mensual"]:+.2f}".replace(".", ",")}</td></tr>'
        for m in meses)
    t_per = "".join(
        f'<tr><td><b>{p["nombre"]}</b><div class="mut">{p["meses"]}</div></td><td class="n">{pct(p["acumulada"])}</td>'
        f'<td class="n">{pct(p["mensual"])}</td><td class="n">{pct(p["rem"])}</td><td class="n">{pct(p["rem_mensual"])}</td></tr>'
        for p in pers)
    t_bl = "".join(
        f'<tr><td><b>{b["nombre"]}</b></td><td>{b["bono"]}</td><td>{b["tasa_fija_de"]}</td><td class="n">{pct(b["mercado"])}</td>'
        f'<td class="n mut">{pct(b["banda_min"])} a {pct(b["banda_max"])}</td><td class="n">{pct(b.get("rem"))}</td></tr>'
        for b in (ns or {}).get("bloques", []))

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
.nota{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c1);border-radius:8px;padding:10px 14px;margin:12px 0}}
ol li{{margin:4px 0}}
</style></head><body>{aviso}<main>
<h1>Curva de bonos soberanos en pesos</h1>
<div class="mut">Precios de cierre del {cierre} (data912) · CER publicado hasta {fecha(d["cer"] and max(d["cer"]).isoformat())} (BCRA) · {rem_txt} · generado {datetime.now().strftime("%d/%m/%Y %H:%M")}</div>

<h2>Inflación que espera el mercado</h2>
<p>Comparando lo que rinde un bono a tasa fija con lo que rinde un bono que ajusta por inflación (CER) al mismo plazo, sale la inflación
que el mercado tiene "en el precio". Llega hasta <b>{hasta}</b>: más allá no hay LECAP/BONCAP para comparar.</p>
<div class="kpis">{kpi("Próximos 3 meses")}{kpi("Próximos 6 meses")}</div>

<h3>Por período</h3>
<div class="tabla"><table><tr><th>Período</th><th class="n">Mercado acumulada</th><th class="n">Mercado por mes</th><th class="n">REM acumulada</th><th class="n">REM por mes</th></tr>{t_per}</table></div>

<h3>Mes a mes</h3>
{svg_barras(meses)}
<div class="mut">Barra = mercado (más clara si el mes no tiene vencimiento propio y toma el promedio de su bloque) · línea vertical = rango por puntas · línea punteada = REM.</div>
<div class="nota"><b>Meses con el mismo número.</b> Cuando entre dos bonos CER hay más de un mes sin otro bono que venza en el medio,
los precios solo dicen cuánta inflación habrá en esos meses <i>juntos</i>, no cómo se reparte entre ellos. Por eso esos meses muestran
el mismo valor: es el promedio del tramo, no una predicción de que la inflación vaya a ser igual en cada uno. Se marcan con
"promedio de su bloque" y se ven en la tabla de bloques.</div>
<div class="tabla"><table><tr><th>Mes de IPC</th><th class="n">Mercado</th><th class="n">Rango por puntas</th><th class="n">Anualizada</th><th class="n">REM</th><th class="n">Dif. (pp)</th></tr>{t_meses}</table></div>

<h3>Por bloque (lo que los bonos dicen exactamente)</h3>
<div class="tabla"><table><tr><th>Meses</th><th>Bono CER</th><th>Tasa fija de</th><th class="n">Promedio por mes</th><th class="n">Rango por puntas</th><th class="n">REM</th></tr>{t_bl}</table></div>

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
<li><b>Mes a mes</b>: cada mes toma el valor del bloque que lo cubre. Si un bloque abarca varios meses, todos quedan con el mismo promedio (marcado).</li>
<li><b>Regla del CER</b>: la inflación del mes M mueve el CER del 16 de M+1 al 15 de M+2 (BCRA); el bono usa el CER de 10 días hábiles antes de vencer.</li>
<li><b>Rango por puntas</b>: el resultado moviendo cada precio media punta (comprador/vendedor).</li>
</ol>
{nota_puntas}
</main></body></html>"""


def main() -> None:
    # Igual que actualizar.py: la página publicada solo con la rueda cerrada (después de las 17:30 o día no hábil).
    # Antes de las 11 data912 ya muestra la rueda del día con pocas operaciones: tampoco sirve.
    preliminar = not A.rueda_cerrada()
    d = A.cargar()
    rem = R.cargar()
    ns = N.calcular(d["filas"], d["liq"], d["cer"], rem)
    destino = BASE / "docs" / ("pesos_preliminar.html" if preliminar else "pesos.html")
    destino.write_text(armar(d, ns, rem, preliminar), encoding="utf-8")
    print("página:", destino.name, "| meses:", len((ns or {}).get("meses", [])))


if __name__ == "__main__":
    main()
