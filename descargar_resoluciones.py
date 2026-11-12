"""Baja del Boletín Oficial todas las resoluciones conjuntas de Finanzas/Hacienda
que disponen emisiones de títulos, y las guarda como texto en fuentes/boletin/.

Uso: python descargar_resoluciones.py [año_desde]
"""
import json
import sys
import time
from pathlib import Path

import boletin

BASE = Path(__file__).parent
DEST = BASE / "fuentes" / "boletin"
DEST.mkdir(parents=True, exist_ok=True)
INDICE = DEST / "indice.json"


def main(desde: int = 2020) -> None:
    indice = json.loads(INDICE.read_text(encoding="utf-8")) if INDICE.exists() else {}
    hoy = time.strftime("%d/%m/%Y")
    for anio in range(desde, int(time.strftime("%Y")) + 1):
        tramos = [(f"01/01/{anio}", f"30/06/{anio}"), (f"01/07/{anio}", f"31/12/{anio}")]
        for d, h in tramos:
            for pag in range(1, 20):
                res = boletin.buscar("Resolución Conjunta Dispónese la emisión", d, min(h, hoy, key=lambda x: x[-4:] + x[3:5] + x[:2]), pag)
                nuevos = 0
                for r in res:
                    if r["id"] in indice:
                        continue
                    if "Resolución Conjunta" not in r["texto"]:
                        continue
                    titulo = r["texto"].split("Fecha de Publicacion")[0].split(">")[-1].strip()
                    indice[r["id"]] = {"fecha": r["fecha"], "titulo": titulo, "url": r["url"]}
                    nuevos += 1
                if not res:
                    break
                print(anio, d, "pág", pag, "resultados", len(res), "nuevos", nuevos)
                time.sleep(0.5)
    INDICE.write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    faltan = [k for k in indice if not (DEST / f"{k}.txt").exists() and not indice[k].get("sin_texto")]
    print("normas en índice:", len(indice), "a descargar:", len(faltan))
    for k in faltan:
        for intento in range(5):
            try:
                t = boletin.texto_norma(k, indice[k]["fecha"])
                (DEST / f"{k}.txt").write_text(t, encoding="utf-8")
                break
            except Exception as e:  # noqa: BLE001
                espera = 20 * (intento + 1)
                print("error", k, e, f"- espero {espera}s")
                time.sleep(espera)
        else:  # 5 intentos sin texto: queda marcada para no reintentar todos los días
            indice[k]["sin_texto"] = True
            INDICE.write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(3)
    print("listo")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2020)
