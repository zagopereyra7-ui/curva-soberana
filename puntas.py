"""Guarda las puntas (mejor compra y mejor venta) de los bonos DURANTE la rueda.

Por qué: con precios de cierre, las puntas que quedan después de las 17:00 no miden nada (la T30A7 tuvo 1.113
operaciones y quedó con 8,6% entre comprador y vendedor). Se toman 3 fotos (16:00, 16:20 y 16:40, hora de
Argentina) y se usa la MEDIANA del spread de cada bono: una foto rara no mueve el resultado.

Correr: python puntas.py            (guarda una foto si la rueda está abierta)
Datos:  datos/puntas/AAAA-MM-DD_HHMM.json
"""
from __future__ import annotations

import json
import statistics
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import feriados as F

BASE = Path(__file__).parent
CARPETA = BASE / "datos" / "puntas"
UA = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}
ART = timezone(timedelta(hours=-3))
DESDE, HASTA = (15, 45), (17, 0)          # ventana válida para una foto (rueda abierta y ya con volumen)
MIN_FOTOS = 1                             # con al menos una foto de la rueda se usan estas puntas


def ahora_art() -> datetime:
    return datetime.now(ART)


def foto() -> Path | None:
    """Baja bonos y letras de data912 y guarda símbolo, compra, venta y operaciones. Solo con la rueda abierta."""
    t = ahora_art()
    if not F.es_habil(t.date()) or not (DESDE <= (t.hour, t.minute) < HASTA):
        print(f"{t:%d/%m %H:%M}: fuera de la ventana {DESDE[0]}:{DESDE[1]:02d}-{HASTA[0]}:{HASTA[1]:02d} de un día hábil, no se guarda.")
        return None
    filas = {}
    for ep in ("arg_bonds", "arg_notes"):
        req = urllib.request.Request(f"https://data912.com/live/{ep}", headers=UA)
        for r in json.load(urllib.request.urlopen(req, timeout=60)):
            filas[r["symbol"]] = {k: r.get(k) for k in ("px_bid", "px_ask", "q_bid", "q_ask", "c", "q_op")}
    CARPETA.mkdir(parents=True, exist_ok=True)
    destino = CARPETA / f"{t:%Y-%m-%d_%H%M}.json"
    destino.write_text(json.dumps({"hora": t.isoformat(timespec="minutes"), "bonos": filas}, ensure_ascii=False), encoding="utf-8")
    print("foto guardada:", destino.name, f"({len(filas)} símbolos)")
    return destino


def spread(p: dict) -> float | None:
    bid, ask = p.get("px_bid") or 0, p.get("px_ask") or 0
    return (ask - bid) / ((ask + bid) / 2) * 100 if bid > 0 and ask > 0 and ask >= bid else None


def del_dia(d: date) -> dict[str, dict]:
    """Por bono: mediana del spread de las fotos del día y cuántas fotos lo tuvieron. Vacío si no hay fotos."""
    fotos = sorted(CARPETA.glob(f"{d.isoformat()}_*.json")) if CARPETA.exists() else []
    por_bono: dict[str, list[float]] = {}
    for f in fotos:
        for sym, p in json.loads(f.read_text(encoding="utf-8"))["bonos"].items():
            s = spread(p)
            if s is not None:
                por_bono.setdefault(sym, []).append(s)
    if len(fotos) < MIN_FOTOS:
        return {}
    return {sym: {"spread": statistics.median(v), "fotos": len(v), "de": len(fotos)} for sym, v in por_bono.items()}


if __name__ == "__main__":
    foto()
