"""Une cada ticker de mercado con el título que leyó la IA, usando el tipo y el vencimiento
que el ticker lleva codificado (S13N6 = LECAP 13/11/2026; TZXD7 = CER dic-2027).

Genera datos/tickers.csv con el emparejamiento y cuáles quedaron sin resolver.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

BASE = Path(__file__).parent
MES = dict(zip("EFMAYJLGSOND", range(1, 13)))

# (regex, tipo de la IA, cómo leer el vencimiento)
REGLAS = [
    (r"^S(\d{2})([EFMAYJLGSOND])(\d)$", "letra_capitalizable_pesos", "dia"),
    (r"^T(\d{2})([EFMAYJLGSOND])(\d)$", "bono_capitalizable_pesos", "dia"),
    (r"^X(\d{2})([EFMAYJLGSOND])(\d)$", "letra_cer", "dia"),
    (r"^D(\d{2})([EFMAYJLGSOND])(\d)$", "letra_dolar_linked", "dia"),
    (r"^TZX([EFMAYJLGSOND])(\d)$", "bono_cer", "mes"),
    (r"^TZX(\d{2})$", "bono_cer", "junio"),
    (r"^TZV([EFMAYJLGSOND])(\d)$", "bono_dolar_linked", "mes"),
    (r"^TZV(\d{2})$", "bono_dolar_linked", "junio"),
    (r"^TM([EFMAYJLGSOND])(\d{2})$", "tamar", "mes2"),
    (r"^TXM([EFMAYJLGSOND])(\d)$", "dual", "mes"),
    (r"^TMV([EFMAYJLGSOND])(\d)$", "dual", "mes"),
    (r"^A([EFMAYJLGSOND])(\d{2})$", "bono_dolares", "mes2"),
    (r"^TY(\d{2})P$", "bono_tasa_fija_pesos", "anio"),
    (r"^TX(\d{2})$", "bono_cer", "anio"),                 # BONCER con cupón: TX28, TX31
]


def decodificar(t: str):
    for rx, tipo, modo in REGLAS:
        m = re.match(rx, t)
        if not m:
            continue
        g = m.groups()
        if modo == "dia":
            return tipo, f"202{g[2]}-{MES[g[1]]:02d}-{int(g[0]):02d}"
        if modo == "mes":
            return tipo, f"20{'2' if g[1] != '0' else '3'}{g[1]}-{MES[g[0]]:02d}"
        if modo == "mes2":
            return tipo, f"20{g[1]}-{MES[g[0]]:02d}"
        if modo == "junio":
            return tipo, f"20{g[0]}-06"
        if modo == "anio":
            return tipo, f"20{g[0]}"
    return None


def titulos_ia(modelo: str = "opus") -> list[dict]:
    """Primera emisión de cada título (si aparece en dos resoluciones, la de fecha de emisión más vieja)."""
    vistos: dict[tuple, dict] = {}
    for f in sorted((BASE / "datos" / "lecturas" / modelo).glob("*.json")):
        for t in json.loads(f.read_text(encoding="utf-8"))["titulos"]:
            k = (t["tipo"], t["fecha_vencimiento"])
            t = {**t, "resolucion": f.stem}
            if k not in vistos or t["fecha_emision"] < vistos[k]["fecha_emision"]:
                vistos[k] = t
    # títulos creados por ley o decreto, cargados a mano con cita (p. ej. TX28 del Decreto 676/2020)
    manual = BASE / "datos" / "titulos_manuales.json"
    if manual.exists():
        for t in json.loads(manual.read_text(encoding="utf-8"))["titulos"]:
            vistos.setdefault((t["tipo"], t["fecha_vencimiento"]), t)
    return list(vistos.values())


def emparejar(tickers: list[str], modelo: str = "opus") -> list[dict]:
    tit = titulos_ia(modelo)
    out = []
    for tk in sorted(tickers):
        d = decodificar(tk)
        if not d:
            continue
        tipo, venc = d
        cand = [t for t in tit if t["tipo"] == tipo and t["fecha_vencimiento"].startswith(venc)]
        if re.match(r"^TX\d{2}$", tk):  # TX = BONCER con cupón: descarta los cero cupón del mismo año
            cand = [t for t in cand if t.get("tasa_nominal_anual_pct")]
        estado = "ok" if len(cand) == 1 else ("sin_titulo" if not cand else "ambiguo")
        out.append({"ticker": tk, "tipo": tipo, "vencimiento_ticker": venc, "estado": estado,
                    "vencimiento": cand[0]["fecha_vencimiento"] if len(cand) == 1 else "",
                    "resolucion": cand[0]["resolucion"] if len(cand) == 1 else "",
                    "nombre": cand[0]["nombre"] if len(cand) == 1 else " | ".join(c["nombre"] for c in cand)})
    return out


def generar(mostrar: bool = False) -> list[dict]:
    """Baja los tickers que cotizan hoy (data912), los empareja y guarda datos/tickers.csv."""
    import urllib.request
    ua = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}
    tk = set()
    for ep in ("arg_bonds", "arg_notes"):
        for r in json.load(urllib.request.urlopen(urllib.request.Request(f"https://data912.com/live/{ep}", headers=ua), timeout=60)):
            tk.add(r["symbol"])
    filas = emparejar(sorted(tk))
    with (BASE / "datos" / "tickers.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]), delimiter=";")
        w.writeheader()
        w.writerows(filas)
    if mostrar:
        for f in filas:
            print(f"{f['ticker']:7} {f['estado']:10} {f['vencimiento'] or f['vencimiento_ticker']:11} {f['nombre'][:80]}")
    return filas


if __name__ == "__main__":
    generar(mostrar=True)
