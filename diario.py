"""Lo que corre una vez por día. Cada paso es independiente: si uno falla, se anota y siguen los demás.

1. Boletín Oficial: baja las resoluciones de emisión nuevas del año.
2. IA: lee solo las resoluciones que todavía no tienen lectura (necesita ANTHROPIC_API_KEY).
3. Comunicados de licitación: baja los nuevos.
4. Tickers: empareja lo que cotiza hoy con los títulos leídos.
5. REM: baja la encuesta mensual de expectativas del BCRA si salió una nueva.
6. Tasas: para LECAP/BONCAP nuevas busca su primera licitación y la IA lee la tasa.
7. Valuación, mercado vs. REM y página (actualizar.py).

Uso: python diario.py      -> registro en datos/log_diario.txt
"""
from __future__ import annotations

import os
import time
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
LOG = BASE / "datos" / "log_diario.txt"


def paso(nombre: str, fn, registro: list[str]) -> None:
    t0 = time.time()
    try:
        fn()
        registro.append(f"ok    {nombre} ({time.time() - t0:.0f} s)")
    except Exception as e:  # noqa: BLE001
        registro.append(f"FALLA {nombre}: {e}")
        traceback.print_exc()


def guardar_normas_hoy(ids) -> None:
    """Qué resoluciones leyó la IA hoy (lo usa el párrafo diario de pesos.py)."""
    import json
    (BASE / "datos" / "normas_hoy.json").write_text(
        json.dumps({"fecha": datetime.now().date().isoformat(), "ids": list(ids or [])}), encoding="utf-8")


def main() -> None:
    import actualizar
    import pesos
    import descargar_resoluciones
    import leer_con_ia
    import rem
    import resultados_licitacion
    import tasas
    import tickers

    hay_clave = bool(os.environ.get("ANTHROPIC_API_KEY"))
    reg: list[str] = []
    paso("Boletín Oficial", lambda: descargar_resoluciones.main(datetime.now().year), reg)
    if hay_clave:
        paso("IA lee resoluciones nuevas", lambda: guardar_normas_hoy(leer_con_ia.auto(leer_con_ia.pendientes())), reg)
    elif leer_con_ia.pendientes():
        reg.append(f"AVISO sin ANTHROPIC_API_KEY: quedan sin leer {', '.join(leer_con_ia.pendientes())}")
    paso("Comunicados de licitación", lambda: (resultados_licitacion.listar(paginas=2, hasta_anio=datetime.now().year),
                                               resultados_licitacion.bajar_todos()), reg)
    paso("Tickers", tickers.generar, reg)
    paso("REM del BCRA", rem.actualizar, reg)
    if hay_clave:
        paso("Tasas de licitación", tasas.main, reg)
    paso("Valuación y página", actualizar.main, reg)
    paso("Página de bonos en pesos", pesos.main, reg)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"--- {datetime.now():%Y-%m-%d %H:%M}\n" + "\n".join(reg) + "\n")
    print("\n".join(reg))
    if any(r.startswith("FALLA") for r in reg):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
