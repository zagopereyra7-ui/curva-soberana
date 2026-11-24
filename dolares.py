"""Curva de bonos soberanos en dólares: TIR contra duration modificada, una curva por legislación.

Formato pedido por el usuario (20/11/2026), como el gráfico habitual de los informes:
  - eje x = duration modificada (MD), eje y = TIR;
  - curva ley Nueva York (GD) y curva ley argentina (AL + AE38), cada una con su ajuste;
  - Bonares nuevos (AO/AN) como grupo aparte: no se excluyen por "lejos de su curva", se muestran;
  - marca de los bonos que cobran TODO antes del cambio de gobierno (10/12/2027): su TIR casi no tiene
    riesgo político, es la referencia de "riesgo país sin riesgo electoral". El AO27 vence el 29/10/2027, después
    de las elecciones (24/10) pero antes de que asuma el gobierno nuevo: lo paga el gobierno actual.
Ajuste: parábola en MD por mínimos cuadrados (como la línea de tendencia polinómica de Excel), solo con
bonos líquidos. Es una guía visual, no un modelo: no se extrapola fuera del rango de cada ley.

Correr: python dolares.py  -> docs/dolares.html (o docs/dolares_preliminar.html con la rueda abierta)
"""
from __future__ import annotations

import html
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np

import actualizar as A

BASE = Path(__file__).parent
CAMBIO_GOBIERNO = date(2027, 12, 10)
GRUPOS = [  # (grupo de actualizar.py, nombre, clase css, se ajusta curva)
    ("GD (ley NY)", "Globales · ley Nueva York", "ny", True),
    ("AL/AE (ley arg.)", "Bonares del canje · ley argentina", "ar", True),
    ("AO/AN (ley arg.)", "Bonares nuevos (AO/AN) · ley argentina", "ao", False),
    ("BOPREAL (BCRA)", "BOPREAL · bonos del BCRA", "bc", False),
]
# BOPREAL: condiciones de las Com. "A" 7918 (Serie 1) y 8233 (Serie 4) del BCRA. Pagan en dólares, ley argentina.
# Serie 1: 5% anual, cupones 30/4 y 31/10, amortiza 50% el 30/04/2027 y 50% el 31/10/2027.
#   Strips A/B/C tienen rescate anticipado en pesos (A3500) a opción del tenedor; el D no, por eso se grafica el D.
# Serie 4: 3% anual, cupones 30/4 y 31/10, amortiza todo el 31/10/2028 (bullet).
BOPREAL = {
    "Serie 1": {"tasa": 5.0, "amort": {date(2027, 4, 30): 50.0, date(2027, 10, 31): 50.0},
                "grafico": "BPD7", "strips": ["BPA7", "BPB7", "BPC7", "BPD7"]},
    "Serie 4": {"tasa": 3.0, "amort": {date(2028, 10, 31): 100.0},
                "grafico": "BPB8", "strips": ["BPA8", "BPB8"]},
}


def flujos_bopreal(serie: dict) -> list[tuple[date, float]]:
    """Cupones semestrales 30/4 y 31/10 sobre el residual, más amortizaciones."""
    fin = max(serie["amort"])
    fechas, y = [], 2024
    while True:
        for d in (date(y, 4, 30), date(y, 10, 31)):
            if d <= fin:
                fechas.append(d)
        if y >= fin.year:
            break
        y += 1
    residual, out = 100.0, []
    for d in fechas:
        cupon = residual * serie["tasa"] / 100 / 2
        am = serie["amort"].get(d, 0.0)
        out.append((d, cupon + am))
        residual -= am
    return out


def filas_bopreal(d: dict) -> list[dict]:
    import instrumentos as I
    filas = []
    for nombre, serie in BOPREAL.items():
        fl = flujos_bopreal(serie)
        for strip in serie["strips"]:
            p = d["precios"].get(strip + "D")
            if not p or not p.get("c"):
                continue
            try:
                tir, md = I._tir(fl, p["c"], d["liq"])
            except ValueError:
                continue
            f = {"ticker": strip + "D", "curva": "dolares", "grupo": "BOPREAL (BCRA)", "precio": p["c"],
                 "tir": tir * 100, "md": md, "vto": max(serie["amort"]).isoformat(), "var": p.get("pct_change") or 0.0}
            f["spread"], f["ops"], _, _ = A.liquidez(p)
            # En el gráfico va un solo strip por serie (el resto rinde casi igual y se superpondría); todos van en la tabla.
            f["revisar"] = strip != serie["grafico"]
            f["motivo"] = None if not f["revisar"] else ("mismo bono con rescate anticipado en pesos" if nombre == "Serie 1" else "otro strip de la misma serie")
            f["solo_tabla"] = f["revisar"]
            filas.append(f)
    return filas
PARES = [("GD29", "AL29"), ("GD30", "AL30"), ("GD35", "AL35"), ("GD38", "AE38"), ("GD41", "AL41")]


def pct(v, dec=2) -> str:
    return "–" if v is None else f"{v:.{dec}f}%".replace(".", ",")


def num(v, dec=2) -> str:
    return "–" if v is None else f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def ultimo_pago(f: dict) -> date:
    return date.fromisoformat(f["vto"])


def ajuste(puntos: list[tuple[float, float]]):
    """Parábola TIR = a + b·MD + c·MD². Con menos de 3 bonos, recta."""
    if len(puntos) < 2:
        return None
    x = np.array([p[0] for p in puntos])
    y = np.array([p[1] for p in puntos])
    grado = 2 if len(puntos) >= 4 else 1
    coef = np.polyfit(x, y, grado)
    return {"coef": coef, "desde": float(x.min()), "hasta": float(x.max()),
            "rmse": float(np.sqrt(np.mean((np.polyval(coef, x) - y) ** 2)))}


def svg(series: list[dict]) -> str:
    W, H, ml, mr, mt, mb = 700, 380, 52, 18, 16, 44
    pts = [(f["md"], f["tir"]) for s in series for f in s["bonos"] if not f.get("solo_tabla")]
    if not pts:
        return ""
    xmax = math.ceil(max(p[0] for p in pts) + 0.4)
    ymin = math.floor(min(min(p[1] for p in pts) - 1, 0))
    ymax = math.ceil(max(p[1] for p in pts) + 1)
    X = lambda v: ml + v / xmax * (W - ml - mr)
    Y = lambda v: mt + (ymax - v) / (ymax - ymin) * (H - mt - mb)
    g = [f'<svg viewBox="0 0 {W} {H}" class="graf" role="img" aria-label="TIR contra duration modificada">']
    paso = 2 if ymax - ymin > 10 else 1
    v = ymin
    while v <= ymax:
        g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                 f'<text x="{ml - 6}" y="{Y(v) + 4:.1f}" class="eje" text-anchor="end">{v:g}%</text>')
        v += paso
    for d in range(0, xmax + 1):
        g.append(f'<text x="{X(d):.1f}" y="{H - mb + 16}" class="eje" text-anchor="middle">{d}</text>')
    g.append(f'<text x="{(ml + W - mr) / 2}" y="{H - 8}" class="eje" text-anchor="middle">duration modificada (años)</text>')
    for s in series:
        a = s.get("ajuste")
        if a:
            xs = np.linspace(a["desde"], a["hasta"], 40)
            g.append(f'<polyline class="linea {s["clase"]}" points="' +
                     " ".join(f"{X(x):.1f},{Y(float(np.polyval(a['coef'], x))):.1f}" for x in xs) + '"/>')
    puestas: list[tuple[float, float]] = []

    def lugar(x: float, y: float) -> tuple[float, float, str]:
        """Etiqueta al costado del punto, en la primera posición que no pisa a otra."""
        for dx, dy, anc in ((7, -7, "start"), (7, 13, "start"), (-7, -7, "end"), (-7, 13, "end"), (7, 25, "start"), (-7, 25, "end")):
            cx = x + dx + (18 if anc == "start" else -18)
            if all(abs(cx - px) > 34 or abs(y + dy - py) > 11 for px, py in puestas):
                puestas.append((cx, y + dy))
                return x + dx, y + dy, anc
        return x + 7, y - 7, "start"

    for s in series:
        for f in s["bonos"]:
            if f.get("solo_tabla"):
                continue
            fuera = " fuera" if f["revisar"] else ""
            elec = f'<circle cx="{X(f["md"]):.1f}" cy="{Y(f["tir"]):.1f}" r="9" class="elec"/>' if f["antes_elecciones"] else ""
            lx, ly, anc = lugar(X(f["md"]), Y(f["tir"]))
            g.append(f'{elec}<circle cx="{X(f["md"]):.1f}" cy="{Y(f["tir"]):.1f}" r="5" class="pt {s["clase"]}{fuera}">'
                     f'<title>{f["ticker"]}: TIR {f["tir"]:.2f}%, MD {f["md"]:.2f}{" (poca liquidez)" if f["revisar"] else ""}</title></circle>'
                     f'<text x="{lx:.1f}" y="{ly:.1f}" class="lbl" text-anchor="{anc}">{f["ticker"]}</text>')
    g.append("</svg>")
    return "".join(g)


def armar(d: dict, preliminar: bool) -> str:
    filas = [f for f in d["filas"] if f["curva"] == "dolares" and f.get("tir") is not None and f.get("md")]
    try:
        filas += filas_bopreal(d)
    except Exception as e:  # noqa: BLE001  (sin BOPREAL la página sale igual)
        print("BOPREAL:", e)
    for f in filas:
        f["antes_elecciones"] = ultimo_pago(f) < CAMBIO_GOBIERNO
        # AO/AN: grupo propio, no se los saca por "lejos de su curva" (es justamente lo que se quiere mostrar)
        if f["grupo"].startswith("AO") and (f.get("motivo") or "").startswith("Lejos"):
            f["revisar"], f["motivo"] = False, None
    series = []
    for grupo, nombre, clase, ajusta in GRUPOS:
        bonos = sorted([f for f in filas if f["grupo"] == grupo], key=lambda f: f["md"])
        a = ajuste([(f["md"], f["tir"]) for f in bonos if not f["revisar"]]) if ajusta else None
        series.append({"grupo": grupo, "nombre": nombre, "clase": clase, "bonos": bonos, "ajuste": a})
    tir = {f["ticker"]: f for f in filas}

    def fila(f, s):
        estado = ("No se grafica: " + html.escape(f["motivo"])) if f.get("solo_tabla") else "Fuera del ajuste: " + html.escape(f.get("motivo") or "") if f["revisar"] else ("Cobra todo antes del cambio de gobierno (10/12/2027)" if f["antes_elecciones"] else "")
        a = s.get("ajuste")
        dist = "" if not a or f["revisar"] else f'{(f["tir"] - float(np.polyval(a["coef"], f["md"]))) * 100:+.0f} pb'
        return (f'<tr class="{"gris" if f["revisar"] else ""}"><td><b>{f["ticker"]}</b></td><td>{date.fromisoformat(f["vto"]):%d/%m/%Y}</td>'
                f'<td class="n">{num(f["precio"])}</td><td class="n">{pct(f["tir"])}</td><td class="n">{num(f["md"])}</td>'
                f'<td class="n">{dist}</td><td class="n">{f.get("ops") or 0}</td><td class="n">{pct(f.get("spread"))}</td><td>{estado}</td></tr>')

    tablas = ""
    for s in series:
        if not s["bonos"]:
            continue
        r = f' · ajuste con error medio de {s["ajuste"]["rmse"] * 100:.0f} pb' if s.get("ajuste") else " · sin curva ajustada: pocos bonos y vencimientos muy distintos"
        tablas += (f'<h3><span class="dot {s["clase"]}"></span>{s["nombre"]}<span class="mut">{r}</span></h3>'
                   '<div class="tabla"><table><tr><th>Bono</th><th>Vence</th><th class="n">Precio (US$)</th><th class="n">TIR</th>'
                   '<th class="n">MD</th><th class="n">vs. su curva</th><th class="n">Operaciones</th><th class="n">Entre puntas</th><th></th></tr>'
                   + "".join(fila(f, s) for f in s["bonos"]) + "</table></div>")

    pares = ""
    for ny, ar in PARES:
        if ny in tir and ar in tir:
            dif = (tir[ar]["tir"] - tir[ny]["tir"]) * 100
            pares += f'<tr><td>{ny} / {ar}</td><td class="n">{pct(tir[ny]["tir"])}</td><td class="n">{pct(tir[ar]["tir"])}</td><td class="n">{dif:+.0f} pb</td></tr>'

    antes = [f for f in filas if f["antes_elecciones"]]
    nota_elec = ""
    if antes:
        lista = ", ".join(f'{f["ticker"]} ({pct(f["tir"])})' for f in sorted(antes, key=lambda f: f["md"]))
        verbo, su = ("cobra", "su") if len(antes) == 1 else ("cobran", "su")
        nota_elec = (f'<div class="nota"><b>Antes y después del cambio de gobierno de diciembre de 2027.</b> {lista} {verbo} todo antes de que asuma el próximo gobierno (10/12/2027): '
                     f'{su} TIR mide el riesgo de pago de corto plazo, casi sin riesgo político. Los que vencen después pagan una prima por la '
                     'incertidumbre electoral. La distancia entre los dos grupos es una forma de medir cuánto pesa la elección en los precios.</div>')

    leyenda = "".join(f'<span class="ley"><span class="dot {s["clase"]}"></span>{s["nombre"]}</span>' for s in series if s["bonos"])
    cierre = date.fromisoformat(d["cierre"]).strftime("%d/%m/%Y")
    aviso = '<div class="aviso">Vista preliminar con precios de una rueda abierta: no es la versión publicada.</div>' if preliminar else ""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curva soberana en dólares</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#e4e1da;--card:#fff;--ny:#c8321e;--ar:#1f5fa8;--ao:#7b3fa0;--bc:#0e8fa3;--warn:#b45309}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151514;--fg:#ecebe7;--mut:#9d9b95;--line:#2f2e2b;--card:#1d1c1a;--ny:#ef6a55;--ar:#6ea6e6;--ao:#b98ae0;--bc:#4cc3d4}}}}
:root[data-theme="dark"]{{--bg:#151514;--fg:#ecebe7;--mut:#9d9b95;--line:#2f2e2b;--card:#1d1c1a;--ny:#ef6a55;--ar:#6ea6e6;--ao:#b98ae0;--bc:#4cc3d4}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:960px;margin:0 auto;padding:24px 16px 60px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:36px 0 6px;padding-top:8px;border-top:1px solid var(--line)}} h3{{font-size:16px;margin:22px 0 4px}}
h3 .mut{{font-weight:400;margin-left:6px}} .mut{{color:var(--mut);font-size:13px}}
.tabla{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-size:12px;color:var(--mut);font-weight:600}}
td.n,th.n{{text-align:right;white-space:nowrap}} tr.gris td{{color:var(--mut)}}
.graf{{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}}
.grid{{stroke:var(--line)}} .eje{{fill:var(--mut);font-size:11px}} .lbl{{fill:var(--fg);font-size:10px}}
.linea{{fill:none;stroke-width:2.2}} .linea.ny{{stroke:var(--ny)}} .linea.ar{{stroke:var(--ar)}}
.pt.ny,.dot.ny{{fill:var(--ny);background:var(--ny)}} .pt.ar,.dot.ar{{fill:var(--ar);background:var(--ar)}} .pt.ao,.dot.ao{{fill:var(--ao);background:var(--ao)}} .pt.bc,.dot.bc{{fill:var(--bc);background:var(--bc)}}
.pt.fuera{{fill:var(--bg);stroke:var(--mut);stroke-width:1.5}} .elec{{fill:none;stroke:var(--warn);stroke-width:1.5;stroke-dasharray:3 2}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}}
.ley{{margin-right:16px;font-size:13px;white-space:nowrap}} .ley .anillo{{display:inline-block;width:12px;height:12px;border-radius:50%;border:1.5px dashed var(--warn);margin-right:6px;vertical-align:middle}}
.aviso{{background:var(--warn);color:#fff;padding:8px 16px;font-weight:600}}
.nota{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:8px;padding:10px 14px;margin:12px 0}}
</style></head><body>{aviso}<main>
<h1>Curva de bonos soberanos en dólares</h1>
<div class="mut">Precios de cierre del {cierre} (data912, especie en dólares MEP) · generado {datetime.now():%d/%m/%Y %H:%M}</div>

<h2>Rendimiento contra duration</h2>
<div>{leyenda}<span class="ley"><span class="anillo"></span>cobra todo antes del cambio de gobierno (dic-2027)</span></div>
{svg(series)}
<p class="mut">Cada curva es una parábola ajustada a los bonos líquidos de esa ley (como la línea de tendencia de Excel): sirve para ver
qué bono rinde más o menos que sus vecinos, no para extrapolar. Círculo vacío = bono fuera del ajuste por poca liquidez.</p>
{nota_elec}

<h2>Ley Nueva York contra ley argentina</h2>
<p>El mismo flujo de fondos rinde distinto según la ley: el AL paga más porque en un default se reestructura más fácil, y el AL30 además
se usa para operar dólar MEP. La diferencia de TIR entre pares es la prima por legislación.</p>
<div class="tabla"><table><tr><th>Par</th><th class="n">TIR ley NY</th><th class="n">TIR ley arg.</th><th class="n">Prima ley arg.</th></tr>{pares}</table></div>

<h2>Bonos</h2>
{tablas}
</main></body></html>"""


def main() -> None:
    preliminar = not A.rueda_cerrada()
    d = A.cargar()
    destino = BASE / "docs" / ("dolares_preliminar.html" if preliminar else "dolares.html")
    destino.write_text(armar(d, preliminar), encoding="utf-8")
    print("página:", destino.name)


if __name__ == "__main__":
    main()
