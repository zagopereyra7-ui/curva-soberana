"""La página única (docs/index.html): todo en un solo lugar, en este orden.

1. Curva soberana en dólares (TIR contra duration modificada).
2. Dólar que espera el mercado (LECAP/BONCAP contra dollar linked).
3. Inflación que espera el mercado (LECAP/BONCAP contra CER).
4. ¿Quién le ganó a la inflación? (rendimientos.py).
5. Qué hizo la IA.
El detalle de cada sección va detrás de un botón. Arma las secciones con las mismas funciones de dolares.py y
pesos.py (no recalcula nada distinto) y las junta. La página técnica completa de actualizar.py queda en tecnico.html.

Correr: python pagina.py  -> docs/index.html (o docs/preliminar.html con la rueda abierta)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import actualizar as A
import dolares as D
import nelson_siegel as N
import pesos as P
import rem as R
import rendimientos as RN

BASE = Path(__file__).parent

# Un solo script para todos los botones de dos opciones: cada grupo maneja solo sus propios gráficos.
SCRIPT = ("<script>document.querySelectorAll('.toggle').forEach(t=>t.querySelectorAll('button').forEach(b=>b.onclick=()=>"
          "t.querySelectorAll('button').forEach(x=>{x.classList.toggle('on',x===b);document.getElementById(x.dataset.g).hidden=x!==b})))</script>")


def partes(pagina: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Estilos, lo que va antes del primer <h2> y las secciones (título, html) de una página ya armada."""
    estilo = re.search(r"<style>(.*?)</style>", pagina, re.S).group(1)
    main = re.search(r"<main>(.*)</main>", pagina, re.S).group(1)
    main = re.sub(r"<script>.*?</script>", "", main, flags=re.S)
    trozos = re.split(r"(?=<h2[ >])", main)
    secciones = [(re.sub(r"<.*?>", "", re.search(r"<h2[^>]*>(.*?)</h2>", t, re.S).group(1)).strip(), t) for t in trozos[1:]]
    return estilo, trozos[0], secciones


def armar(d: dict, ns, rem, preliminar: bool, ia, res) -> str:
    est_d, _, sec_d = partes(D.armar(d, preliminar))
    est_p, cabeza_p, sec_p = partes(P.armar(d, ns, rem, preliminar, ia, res))
    parrafo = re.sub(r"^.*?</div>\s*", "", cabeza_p.split("<h1>", 1)[1], count=1, flags=re.S)  # saca h1 y la línea de fuentes
    por_titulo = dict(sec_p)
    dolar_mercado = por_titulo.get("Dólar que espera el mercado", "")
    inflacion = por_titulo.get("Inflación que espera el mercado", "")
    ia_html = "".join(h for t, h in sec_p if t not in ("Dólar que espera el mercado", "Inflación que espera el mercado"))
    curva_usd = "".join(h for _, h in sec_d)
    curva_usd = curva_usd.replace("<h2>Rendimiento contra duration</h2>", '<h2 id="dolares">Bonos en dólares: rendimiento contra duration</h2>', 1)
    dolar_mercado = dolar_mercado.replace("<h2>Dólar que espera", '<h2 id="dolar">Dólar que espera', 1)
    inflacion = inflacion.replace("<h2>Inflación que espera", '<h2 id="inflacion">Inflación que espera', 1)
    comparacion = RN.seccion()
    cierre = date.fromisoformat(d["cierre"]).strftime("%d/%m/%Y")
    rem_txt = f'REM del BCRA, edición {rem["edicion"]}' if rem else "REM no disponible"
    aviso = '<div class="aviso">Vista preliminar con precios de una rueda abierta: no es la versión publicada.</div>' if preliminar else ""
    nav = ('<nav class="nav"><a href="#dolares">Bonos en dólares</a><a href="#dolar">Dólar esperado</a>'
           '<a href="#inflacion">Inflación esperada</a>' + ('<a href="#inflacion-vs">¿Quién le ganó a la inflación?</a>' if comparacion else "") + "</nav>")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curva soberana argentina</title>
<style>{est_p}
{est_d}
{RN.ESTILOS}
.nav{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px}} .nav a{{font-size:13px;padding:5px 10px;border:1px solid var(--line);border-radius:16px;text-decoration:none;color:var(--fg);background:var(--card)}}
h2[id]{{scroll-margin-top:12px}}
.intro{{font-size:16px;max-width:760px;margin:10px 0 4px}}
</style></head><body>{aviso}<main>
<h1>Bonos argentinos: curvas y expectativas</h1>
<p class="intro">Este monitor toma todos los días los precios de los bonos del Tesoro y del BCRA y calcula cuánto rinden, qué dólar y qué inflación espera el mercado, y cómo se comparan con lo que proyectan los economistas del REM.
La IA lee las resoluciones del Boletín Oficial y los resultados de las licitaciones para cargar las condiciones de cada bono, y todos los números salen de fórmulas fijas que se controlan contra los textos oficiales.</p>
<div class="mut">Precios de cierre del {cierre} (data912) · CER y dólar oficial: BCRA · {rem_txt} · se actualiza solo cada día hábil a las 19:30 · generado {datetime.now():%d/%m/%Y %H:%M}</div>
{nav}
{parrafo}
{curva_usd}
{dolar_mercado}
{inflacion}
{comparacion}
{ia_html}
</main>{SCRIPT}</body></html>"""


def main() -> None:
    preliminar = not A.rueda_cerrada()
    d = A.cargar()
    rem = R.cargar()
    ns = N.calcular(d["filas"], d["liq"], d["cer"], rem)
    import controles_ia
    controles_ia.correr()
    ia = P.datos_ia()
    res = None
    if not preliminar:                      # el párrafo de la IA solo con precios de cierre (sin crédito, se saltea)
        import resumen_ia
        res = P.resumen_ia_del_dia(resumen_ia, d, ns, rem)
    destino = BASE / "docs" / ("preliminar.html" if preliminar else "index.html")
    destino.write_text(armar(d, ns, rem, preliminar, ia, res), encoding="utf-8")
    print("página:", destino.name)


if __name__ == "__main__":
    main()
