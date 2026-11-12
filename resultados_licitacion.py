"""Comunicados oficiales de resultado de licitación (argentina.gob.ar/economia/finanzas/noticias).

listar(): recorre el listado de noticias y guarda {url, titulo, fecha} de cada resultado.
texto(url): baja el comunicado y devuelve el texto (incluye las tablas).
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}
BASE = Path(__file__).parent
DEST = BASE / "fuentes" / "resultados"
LISTADO = "https://www.argentina.gob.ar/economia/finanzas/noticias"
MESES = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}


def _get(url: str) -> str:
    for i in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"No pude bajar {url}")


def texto(url: str) -> tuple[str, str]:
    """(fecha AAAA-MM-DD, texto) del comunicado."""
    raw = _get(url)
    m = re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})', raw)
    fecha = m.group(1) if m else ""
    i = raw.find("<article")
    cuerpo = raw[i:raw.find("</article>", i)] if i >= 0 else raw
    cuerpo = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", cuerpo, flags=re.S)
    cuerpo = re.sub(r"</(p|tr|h\d|li|div)>|<br\s*/?>", "\n", cuerpo)
    cuerpo = re.sub(r"</t[dh]>", " | ", cuerpo)
    cuerpo = html.unescape(re.sub(r"<[^>]+>", " ", cuerpo))
    cuerpo = re.sub(r"[ \t]+", " ", cuerpo)
    return fecha, re.sub(r"\n\s*\n+", "\n", cuerpo).strip()


def listar(paginas: int = 40, hasta_anio: int = 2024) -> dict:
    DEST.mkdir(parents=True, exist_ok=True)
    idx_f = DEST / "indice.json"
    idx = json.loads(idx_f.read_text(encoding="utf-8")) if idx_f.exists() else {}
    for p in range(paginas):
        raw = _get(f"{LISTADO}?page={p}")
        links = sorted(set(re.findall(r'href="(/noticias/resultado[^"#?]+)"', raw)))
        anios = [int(a) for a in re.findall(r"de (20\d\d)", raw)]
        for l in links:
            idx.setdefault("https://www.argentina.gob.ar" + l, {})
        print("pág", p, "resultados", len(links), "años", min(anios) if anios else "?")
        if anios and max(anios) < hasta_anio:
            break
        time.sleep(1.5)
    idx_f.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return idx


def bajar_todos() -> None:
    idx_f = DEST / "indice.json"
    idx = json.loads(idx_f.read_text(encoding="utf-8"))
    for url, meta in idx.items():
        nombre = url.rsplit("/", 1)[-1]
        f = DEST / f"{nombre}.txt"
        if f.exists() and meta.get("fecha"):
            continue
        fecha, t = texto(url)
        f.write_text(t, encoding="utf-8")
        meta.update({"fecha": fecha, "archivo": f.name})
        time.sleep(1.5)
    idx_f.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    print("comunicados:", len(idx))


if __name__ == "__main__":
    listar()
    bajar_todos()
