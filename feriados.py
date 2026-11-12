"""Feriados nacionales de Argentina (fuente: api.argentinadatos.com), guardados en datos/feriados.json.

- Los años pasados se bajan una sola vez.
- El año actual y el siguiente se vuelven a bajar en cada corrida (el Gobierno decreta feriados nuevos).
- Si la fuente no responde, se usa lo guardado.
- Años sin datos (lejos en el futuro): solo se saltean sábados y domingos.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path

ARCHIVO = Path(__file__).parent / "datos" / "feriados.json"
UA = {"User-Agent": "Mozilla/5.0 (curva-soberana)"}
DESDE = 2021


def _bajar(anio: int) -> list[str]:
    url = f"https://api.argentinadatos.com/v1/feriados/{anio}"
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
    return sorted({x["fecha"] for x in d})


def actualizar(hoy: date | None = None) -> dict[str, list[str]]:
    hoy = hoy or date.today()
    guardado = json.loads(ARCHIVO.read_text(encoding="utf-8")) if ARCHIVO.exists() else {}
    for anio in range(DESDE, hoy.year + 2):
        if str(anio) in guardado and anio < hoy.year:
            continue
        try:
            fechas = _bajar(anio)
            if fechas:
                guardado[str(anio)] = fechas
        except Exception as e:  # sin conexión o fuente caída: seguimos con lo guardado
            print(f"aviso: no se pudieron bajar los feriados {anio} ({e}); uso lo guardado")
    ARCHIVO.parent.mkdir(exist_ok=True)
    ARCHIVO.write_text(json.dumps(guardado, indent=1), encoding="utf-8")
    cargar()
    return guardado


FERIADOS: set[date] = set()


def cargar() -> None:
    FERIADOS.clear()
    if ARCHIVO.exists():
        for fechas in json.loads(ARCHIVO.read_text(encoding="utf-8")).values():
            FERIADOS.update(date.fromisoformat(f) for f in fechas)


def es_habil(d: date) -> bool:
    return d.weekday() < 5 and d not in FERIADOS


cargar()
