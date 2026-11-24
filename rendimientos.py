"""¿Quién le ganó a la inflación? Rendimiento en pesos de varias inversiones contra la inflación del mismo período.

Ventanas alineadas con el último dato de inflación publicado (INDEC vía BCRA, variable 27): si el último dato es
octubre, los "últimos 12 meses" van del cierre de octubre del año anterior al cierre de octubre, y "en el año" del
31/12 al cierre de octubre. Así la barra de la inflación y las inversiones miden exactamente lo mismo.

Todo en pesos y sin dividendos:
  - acciones y CEDEARs: precio de cierre (data912), ajustado por splits/cambios de ratio (saltos de un día de más
    de 40% que coinciden con una proporción entera, p. ej. SPY 3:1 en may-26, YPFD 10:1 en ago-26);
  - dólar oficial: A3500 (BCRA, variable 5); dólar MEP: AL30 / AL30D;
  - plazo fijo: tasa a 30 días (BCRA, variable 1207) renovando cada 30 días;
  - bono CER: TX28 con lo que pagó en el período (cupón y cuotas ajustados por CER), reinvertido sin interés.
Correr: python rendimientos.py  (genera la sección; la usa pagina.py)
"""
from __future__ import annotations

import html
import json
import math
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import instrumentos as I

UA = {"User-Agent": "Mozilla/5.0"}
ACCIONES_PANEL = ["GGAL", "YPFD", "PAMP", "BMA", "TXAR", "CEPU", "TGSU2", "LOMA", "BBAR", "CRES", "EDN", "TECO2",
                  "SUPV", "COME", "BYMA", "METR", "TRAN", "ALUA", "VALO", "TGNO4", "CVH", "IRSA", "MIRG", "HARG"]
CEDEARS = [("SPY", "S&P 500 (CEDEAR SPY)"), ("QQQ", "Nasdaq 100 (CEDEAR QQQ)")]
FIJAS = [("GGAL", "Galicia (GGAL)"), ("YPFD", "YPF (YPFD)")]


def _bajar(url: str):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))


def historia(clase: str, ticker: str) -> tuple[dict[date, float], list[tuple[date, str]]]:
    """Cierres por fecha, ajustados por splits. Devuelve también la lista de ajustes hechos."""
    s = _bajar(f"https://data912.com/historical/{clase}/{ticker}")
    if not isinstance(s, list):
        return {}, []
    pts = [(date.fromisoformat(x["date"]), float(x["c"])) for x in s if x.get("c")]
    factor, ajustes, out = 1.0, [], {}
    for (d0, c0), (d1, c1) in reversed(list(zip(pts, pts[1:]))):
        out[d1] = c1 * factor
        r = c0 / c1
        for n, inv in ((round(r), False), (round(1 / r), True)):
            if n >= 2 and abs((1 / r if inv else r) / n - 1) < 0.08:
                factor *= n if inv else 1 / n
                ajustes.append((d1, f"{'agrupamiento' if inv else 'split'} {n}:1 el {d1:%d/%m/%Y}"))
                break
    if pts:
        out[pts[0][0]] = pts[0][1] * factor
    return out, ajustes


def en(serie: dict[date, float], d: date) -> float | None:
    previos = [k for k in serie if k <= d and k > d - timedelta(days=10)]
    return serie[max(previos)] if previos else None


def _fin_mes(y: int, m: int) -> date:
    return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)


def ventanas() -> tuple[dict[date, float], list[dict]]:
    hoy = date.today()
    inf = I.serie_bcra(27, f"{hoy.year - 2}-01-01", hoy.isoformat())
    fin = max(inf)                                    # último mes con inflación publicada (fecha = fin de mes)
    fin = _fin_mes(fin.year, fin.month)
    ini12 = _fin_mes(fin.year - 1, fin.month)
    out = [{"id": "12m", "nombre": "Últimos 12 meses", "desde": ini12, "hasta": fin}]
    if fin.month != 12:
        out.append({"id": "anio", "nombre": f"En {fin.year}", "desde": date(fin.year - 1, 12, 31), "hasta": fin})
    for v in out:
        meses = [k for k in inf if v["desde"] < k <= v["hasta"] + timedelta(days=1)]
        v["inflacion"] = (math.prod(1 + inf[k] / 100 for k in meses) - 1) * 100
        v["meses"] = len(meses)
    return inf, out


def plazo_fijo(desde: date, hasta: date) -> float:
    tna = I.serie_bcra(1207, (desde - timedelta(days=15)).isoformat(), hasta.isoformat())
    d, acum = desde, 1.0
    while d < hasta:
        prox = min(d + timedelta(days=30), hasta)
        t = I.valor_en(tna, d)
        acum *= 1 + t / 100 * (prox - d).days / 365
        d = prox
    return (acum - 1) * 100


def bono_cer(desde: date, hasta: date) -> tuple[float, str]:
    """TX28: precio final + pagos del período (en pesos, ajustados por CER) contra precio inicial."""
    t = next(x for x in json.loads(open(Path(__file__).parent / "datos" / "titulos_manuales.json", encoding="utf-8").read())["titulos"]
             if "2028" in x["fecha_vencimiento"] and x["tipo"] == "bono_cer")
    precios, _ = historia("bonds", "TX28")
    cer = I.serie_bcra(30, "2020-08-01", hasta.isoformat())
    rez = t["dias_habiles_rezago_cer"] or 10
    base = I.valor_en(cer, I.restar_habiles(date.fromisoformat(t["fecha_emision"]), rez))
    pagos = sum(m * I.valor_en(cer, I.restar_habiles(f, rez)) / base
                for f, m in I.flujos_cupon(t, t["tasa_nominal_anual_pct"]) if desde < f <= hasta)
    p0, p1 = en(precios, desde), en(precios, hasta)
    return ((p1 + pagos) / p0 - 1) * 100, f"precio + {pagos:,.1f} pesos cobrados por cada 100 (cupón y cuotas)".replace(",", "X").replace(".", ",").replace("X", ".")


def calcular() -> dict:
    inf, vs = ventanas()
    hasta_max = max(v["hasta"] for v in vs)
    desde_min = min(v["desde"] for v in vs)
    a3500 = I.serie_bcra(5, (desde_min - timedelta(days=15)).isoformat(), hasta_max.isoformat())
    al30, _ = historia("bonds", "AL30")
    al30d, _ = historia("bonds", "AL30D")
    series = {}
    for tk in ACCIONES_PANEL:
        try:
            series[("stocks", tk)] = historia("stocks", tk)
        except Exception:  # noqa: BLE001
            pass
    for tk, _ in CEDEARS:
        series[("cedears", tk)] = historia("cedears", tk)

    for v in vs:
        a, b = v["desde"], v["hasta"]
        rend = lambda s: (en(s, b) / en(s, a) - 1) * 100 if en(s, a) and en(s, b) else None
        dentro = lambda aj: [t for d, t in aj if a < d <= b]   # solo los ajustes que caen en el período
        items = [
            {"nombre": "Dólar oficial (A3500)", "tipo": "dolar", "r": (I.valor_en(a3500, b) / I.valor_en(a3500, a) - 1) * 100, "nota": "BCRA"},
            {"nombre": "Dólar MEP", "tipo": "dolar", "r": ((en(al30, b) / en(al30d, b)) / (en(al30, a) / en(al30d, a)) - 1) * 100,
             "nota": "AL30 en pesos / AL30 en dólares"},
            {"nombre": "Plazo fijo 30 días", "tipo": "pesos", "r": plazo_fijo(a, b), "nota": "tasa BCRA a 30 días, renovando"},
        ]
        try:
            r, nota = bono_cer(a, b)
            items.append({"nombre": "Bono CER (TX28)", "tipo": "pesos", "r": r, "nota": nota})
        except Exception as e:  # noqa: BLE001
            print("bono CER:", e)
        for tk, nombre in CEDEARS:
            s, aj = series[("cedears", tk)]
            if rend(s) is not None:
                items.append({"nombre": nombre, "tipo": "afuera", "r": rend(s), "nota": "en pesos (incluye la suba del dólar)" + ("; ajustado por " + ", ".join(dentro(aj)) if dentro(aj) else "")})
        panel = {tk: (rend(series[("stocks", tk)][0]), dentro(series[("stocks", tk)][1])) for tk in ACCIONES_PANEL if ("stocks", tk) in series}
        panel = {k: x for k, x in panel.items() if x[0] is not None}
        for tk, nombre in FIJAS:
            if tk in panel:
                items.append({"nombre": nombre, "tipo": "accion", "r": panel[tk][0], "nota": "precio en pesos" + ("; ajustado por " + ", ".join(panel[tk][1]) if panel[tk][1] else "")})
        if panel:
            mejor = max(panel, key=lambda k: panel[k][0])
            if mejor not in dict(FIJAS):
                items.append({"nombre": f"{mejor}: la acción del panel líder que más subió", "tipo": "accion", "r": panel[mejor][0],
                              "nota": f"la mejor de {len(panel)} acciones del panel líder" + ("; ajustado por " + ", ".join(panel[mejor][1]) if panel[mejor][1] else "")})
            v["panel"] = sorted(((k, x[0]) for k, x in panel.items()), key=lambda kv: kv[1])
        for it in items:
            it["real"] = ((1 + it["r"] / 100) / (1 + v["inflacion"] / 100) - 1) * 100
        v["items"] = sorted(items, key=lambda it: it["r"])
    return {"ventanas": vs}


def pct(v, dec=1) -> str:
    return "–" if v is None else f"{v:+.{dec}f}%".replace(".", ",")


def svg(v: dict) -> str:
    items, inf = v["items"], v["inflacion"]
    W, H, ml, mr, mt, mb = 700, 360, 46, 10, 22, 92
    vals = [it["r"] for it in items] + [inf, 0]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad if lo < 0 else 0, hi + pad
    Y = lambda x: mt + (hi - x) / (hi - lo) * (H - mt - mb)
    bw = (W - ml - mr) / len(items)
    g = [f'<svg viewBox="0 0 {W} {H}" class="graf" role="img" aria-label="Rendimiento contra inflación">']
    paso = 10 if hi - lo <= 120 else 20 if hi - lo <= 250 else 50
    t = math.ceil(lo / paso) * paso
    while t <= hi:
        g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" class="grid"/>'
                 f'<text x="{ml - 6}" y="{Y(t) + 4:.1f}" class="eje" text-anchor="end">{t:g}%</text>')
        t += paso
    g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" class="cero"/>')
    for i, it in enumerate(items):
        x = ml + i * bw + bw * 0.16
        w = bw * 0.68
        y0, y1 = sorted((Y(0), Y(it["r"])))
        cls = "gana" if it["r"] > inf else "pierde"
        g.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{max(y1 - y0, 1):.1f}" rx="3" class="bar {cls}">'
                 f'<title>{html.escape(it["nombre"])}: {pct(it["r"])} (real {pct(it["real"])})</title></rect>')
        ty = Y(it["r"]) - 5 if it["r"] >= 0 else Y(it["r"]) + 13
        g.append(f'<text x="{x + w / 2:.1f}" y="{ty:.1f}" class="val" text-anchor="middle">{pct(it["r"], 0)}</text>')
        corto = html.escape(it["nombre"].split(":")[0].split(" (")[0])
        cx, cy = x + w / 2, H - mb + 12
        g.append(f'<text x="{cx:.1f}" y="{cy:.1f}" class="eje" text-anchor="end" transform="rotate(-35 {cx:.1f} {cy:.1f})">{corto}</text>')
    g.append(f'<line x1="{ml}" x2="{W - mr}" y1="{Y(inf):.1f}" y2="{Y(inf):.1f}" class="infl"/>'
             f'<text x="{ml + 4}" y="{Y(inf) - 6:.1f}" class="infl-t">inflación {pct(inf)}</text>')
    g.append("</svg>")
    return "".join(g)


def seccion(datos: dict | None = None) -> str:
    try:
        datos = datos or calcular()
    except Exception as e:  # noqa: BLE001  (sin esta sección la página sale igual)
        print("rendimientos:", e)
        return ""
    vs = datos["ventanas"]
    botones = "".join(f'<button type="button" data-g="r-{v["id"]}"{" class=on" if i == 0 else ""}>{v["nombre"]}</button>' for i, v in enumerate(vs))
    graficos, tablas = "", ""
    for i, v in enumerate(vs):
        ganan = [it["nombre"].split(":")[0] for it in v["items"] if it["r"] > v["inflacion"]]
        pierden = [it["nombre"].split(":")[0] for it in v["items"] if it["r"] <= v["inflacion"]]
        lectura = (f'<p>Del {v["desde"]:%d/%m/%Y} al {v["hasta"]:%d/%m/%Y} la inflación fue <b>{pct(v["inflacion"])}</b>. '
                   f'Le ganaron: {", ".join(ganan) or "ninguna"}. Perdieron contra la inflación: {", ".join(pierden) or "ninguna"}.</p>')
        graficos += f'<div id="r-{v["id"]}"{"" if i == 0 else " hidden"}>{lectura}{svg(v)}</div>'
        filas = "".join(f'<tr><td><b>{html.escape(it["nombre"])}</b></td><td class="n">{pct(it["r"])}</td><td class="n">{pct(it["real"])}</td>'
                        f'<td class="mut">{html.escape(it["nota"])}</td></tr>' for it in reversed(v["items"]))
        panel = ""
        if v.get("panel"):
            panel = ('<p class="mut">Todas las acciones del panel líder en el período: ' +
                     ", ".join(f"{k} {pct(r, 0)}" for k, r in reversed(v["panel"])) + ".</p>")
        tablas += (f'<h3>{v["nombre"]} ({v["desde"]:%d/%m/%Y} al {v["hasta"]:%d/%m/%Y}, inflación {pct(v["inflacion"])})</h3>'
                   '<div class="tabla"><table><tr><th>Inversión</th><th class="n">Rendimiento en pesos</th><th class="n">Contra la inflación (real)</th><th>De dónde sale</th></tr>'
                   f'{filas}</table></div>{panel}')
    return f"""<h2 id="inflacion-vs">¿Quién le ganó a la inflación?</h2>
<p>Cuánto rindió en pesos cada inversión, ordenadas de menor a mayor. La línea es la inflación del mismo período: la barra verde le ganó, la roja perdió.
Las fechas terminan en el último mes con inflación publicada por el INDEC, para comparar exactamente lo mismo.</p>
<div class="toggle" role="group" aria-label="Período">{botones}</div>
{graficos}
<details class="detalle"><summary>Ver el detalle: rendimiento nominal y real</summary>
{tablas}
<p class="mut">"Real" = cuánto ganó o perdió por encima de la inflación: (1 + rendimiento) ÷ (1 + inflación) − 1. Acciones y CEDEARs: variación del precio
sin dividendos, ajustada por splits. Los CEDEARs están en pesos, así que incluyen lo que subió el dólar.</p>
</details>"""


ESTILOS = (".bar.gana{fill:#2f8a4e} .bar.pierde{fill:#c8321e} .val{fill:var(--fg);font-size:11px;font-weight:600}"
           " .cero{stroke:var(--mut);stroke-width:1} .infl{stroke:var(--fg);stroke-width:2;stroke-dasharray:6 4}"
           " .infl-t{fill:var(--fg);font-size:12px;font-weight:600}")


if __name__ == "__main__":
    for v in calcular()["ventanas"]:
        print(v["nombre"], v["desde"], v["hasta"], f"inflación {v['inflacion']:.1f}%")
        for it in v["items"]:
            print(f"   {it['r']:7.1f}%  real {it['real']:6.1f}%  {it['nombre']}  ({it['nota']})")
