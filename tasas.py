"""Tasas de licitación de los títulos a tasa fija en pesos (LECAP, BONCAP, BONTE).

La resolución del Boletín dice cuándo vence una LECAP, pero la tasa (TEM) se fija en la licitación.
Este programa, para cada título a tasa fija que todavía no tiene tasa:
1. busca el comunicado oficial más viejo que lo menciona (= su primera licitación),
2. si ese comunicado no fue leído, se lo pasa a la IA (leer_resultados.py),
3. guarda la tasa en datos/tasas_licitacion.json.

Uso: python tasas.py            (lo llama diario.py)
     python tasas.py --probar   (recalcula todo sin IA y compara con lo guardado)
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent
FUENTES = BASE / "fuentes" / "resultados"
LECTURAS = BASE / "datos" / "lecturas" / "resultados"
ELEGIDOS = BASE / "datos" / "resultados_elegidos.json"
TASAS = BASE / "datos" / "tasas_licitacion.json"
TIPOS = {"letra_capitalizable_pesos", "bono_capitalizable_pesos", "bono_tasa_fija_pesos"}
MESES = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()


def _formas(venc: str) -> list[str]:
    d = date.fromisoformat(venc)
    return [f"{d.day} de {MESES[d.month - 1]} de {d.year}", f"{d.day:02d}/{d.month:02d}/{d.year}",
            f"{d.day}/{d.month}/{d.year}"]


def titulos_a_tasa_fija() -> list[dict]:
    return [r for r in csv.DictReader(open(BASE / "datos" / "tickers.csv", encoding="utf-8-sig"), delimiter=";")
            if r["estado"] == "ok" and r["tipo"] in TIPOS]


def primer_comunicado(ticker: str, venc: str) -> str | None:
    """Comunicado de la primera licitación del título.

    Busca el ticker entre paréntesis ("(S26F7 - nuevo)") y exige que el nombre que lo precede diga
    "capitalizable" o "tasa fija": una misma fecha la comparten LECAP, TAMAR y dollar linked, y hasta
    un ticker se reutilizó (T31Y7 fue primero un TAMAR desierto). Prioridad:
    1) comunicado que lo marca "nuevo/nueva" por ticker o por fecha (el más viejo), 2) el más viejo que lo menciona,
    3) si ninguno tiene el ticker, la fecha escrita junto a "capitalizable" o "tasa fija"."""
    idx = json.loads((FUENTES / "indice.json").read_text(encoding="utf-8"))
    rx_tk = re.compile(r"(?P<antes>[^|\n(]{0,200})\(\s*" + re.escape(ticker) + r"\b(?P<despues>[^)]{0,20})\)", re.I)
    rx_tipo = re.compile(r"capitalizable|tasa fija", re.I)
    rx_fecha = re.compile(r"(capitalizable|tasa fija)[^|\n]{0,120}(" + "|".join(map(re.escape, _formas(venc))) + r")(?P<despues>\s*\([^)]{0,20}\))?", re.I)
    nuevo, menciona, por_fecha = [], [], []
    for meta in idx.values():
        a = meta.get("archivo")
        if not a or not (FUENTES / a).exists():
            continue
        t = (FUENTES / a).read_text(encoding="utf-8")
        clave = (meta.get("fecha", ""), a)
        hits = [m for m in rx_tk.finditer(t) if rx_tipo.search(m.group("antes"))]
        fechas = list(rx_fecha.finditer(t))
        if any("nuev" in (m.group("despues") or "").lower() for m in hits + fechas):
            nuevo.append(clave)
        elif hits:
            menciona.append(clave)
        elif fechas:
            por_fecha.append(clave)
    cand = nuevo or menciona or por_fecha
    return min(cand)[1] if cand else None


def tasa_de(archivo: str, venc: str) -> dict | None:
    f = LECTURAS / archivo.replace(".txt", ".json")
    if not f.exists():
        return None
    for t in json.loads(f.read_text(encoding="utf-8"))["titulos"]:
        if t["fecha_vencimiento"] == venc:
            return t
    return None


def main(probar: bool = False) -> None:
    elegidos = json.loads(ELEGIDOS.read_text(encoding="utf-8")) if ELEGIDOS.exists() else {}
    tasas = json.loads(TASAS.read_text(encoding="utf-8")) if TASAS.exists() else {}
    faltan = [r for r in titulos_a_tasa_fija() if probar or r["ticker"] not in tasas]
    nuevos = {}
    for r in faltan:
        a = primer_comunicado(r["ticker"], r["vencimiento"])
        if a:
            nuevos[r["ticker"]] = a
        else:
            print(f"aviso: {r['ticker']} sin comunicado de licitación todavía")
    if probar:
        for tk, a in nuevos.items():
            ok_a = elegidos.get(tk) == [a]
            t = tasa_de(a, next(r["vencimiento"] for r in faltan if r["ticker"] == tk))
            ok_t = t is not None and t == tasas.get(tk)
            print(f"{tk:6} comunicado {'igual' if ok_a else 'DISTINTO'} · tasa {'igual' if ok_t else 'DISTINTA'}")
        return
    if not nuevos:
        return
    for tk, a in nuevos.items():
        elegidos[tk] = [a]
    ELEGIDOS.write_text(json.dumps(elegidos, ensure_ascii=False, indent=1), encoding="utf-8")
    import leer_resultados  # lee con IA solo los comunicados que todavía no tienen lectura
    leer_resultados.main()
    for r in faltan:
        if r["ticker"] in nuevos and (t := tasa_de(nuevos[r["ticker"]], r["vencimiento"])):
            tasas[r["ticker"]] = t
            print(f"{r['ticker']}: TEM {t['tem_capitalizacion_pct']} · cupón {t['tasa_cupon_pct']}")
    TASAS.write_text(json.dumps(tasas, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(probar="--probar" in sys.argv)
