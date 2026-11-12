"""Búsqueda y descarga de normas de la primera sección del Boletín Oficial."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}
BASE = "https://www.boletinoficial.gob.ar"


def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={**UA, "Content-Type": "application/x-www-form-urlencoded"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def buscar(texto: str, desde: str, hasta: str, pagina: int = 1) -> list[dict]:
    """Devuelve [{id, fecha, titulo, resumen, url}] para un texto (fechas dd/mm/aaaa)."""
    params = {
        "busqueda_rubro": False, "hayMasResultadosBusqueda": True,
        "ejecutandoLlamadaAsincronicaBusqueda": False, "ultimaSeccion": "",
        "filtroPorRubrosSeccion": False, "filtroPorRubroBusqueda": False,
        "filtroPorSeccionBusqueda": False, "busquedaOriginal": True,
        "ordenamientoSegunda": False, "seccionesOriginales": [1],
        "ultimoItemExterno": None, "ultimoItemInterno": None, "texto": texto,
        "rubros": [], "nroNorma": "", "anioNorma": "", "denominacion": "",
        "tipoContratacion": "", "anioContratacion": "", "nroContratacion": "",
        "fechaDesde": desde, "fechaHasta": hasta, "todasLasPalabras": True,
        "comienzaDenominacion": True, "seccion": [1], "tipoBusqueda": "Avanzada",
        "numeroPagina": pagina, "ultimoRubro": "",
    }
    r = _post(BASE + "/busquedaAvanzada/realizarBusqueda",
              {"params": json.dumps(params), "array_volver": "[]"})
    h = r["content"]["html"]
    out = []
    for m in re.finditer(r'href="(/detalleAviso/primera/(\d+)/(\d{8})[^"]*)"(.*?)</a>', h, re.S):
        bloque = re.sub(r"<[^>]+>", " ", m.group(4))
        bloque = html.unescape(re.sub(r"\s+", " ", bloque)).strip()
        out.append({"id": m.group(2), "fecha": m.group(3), "texto": bloque,
                    "url": BASE + m.group(1)})
    return out


def texto_norma(id_aviso: str, fecha: str) -> str:
    """Texto completo de una norma (incluye anexos si están en el cuerpo)."""
    url = f"{BASE}/detalleAviso/primera/{id_aviso}/{fecha}"
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")
    i = raw.find('id="cuerpoDetalleAviso"')
    if i < 0:
        raise ValueError(f"No encontré el cuerpo de la norma {id_aviso}")
    cuerpo = raw[i:]
    fin = min([j for j in (cuerpo.find("<script"), cuerpo.find('id="anexos')) if j > 0] or [len(cuerpo)])
    cuerpo = cuerpo[cuerpo.find(">") + 1:fin]
    cuerpo = re.sub(r"<br\s*/?>|</p>", "\n", cuerpo)
    cuerpo = re.sub(r"<[^>]+>", " ", cuerpo)
    cuerpo = html.unescape(cuerpo)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", cuerpo)).strip()


if __name__ == "__main__":
    import sys
    for x in buscar(sys.argv[1], sys.argv[2], sys.argv[3]):
        print(x["fecha"], x["id"], x["texto"][:160])
