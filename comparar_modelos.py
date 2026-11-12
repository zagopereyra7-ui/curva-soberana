"""Compara campo por campo lo que leyeron Opus y Haiku en cada resolución.

Empareja los títulos por resolución + fecha de vencimiento + nombre, y genera
datos/comparacion.csv con una fila por (título, campo) y si coinciden.
"""
import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent
LECT = BASE / "datos" / "lecturas"
CAMPOS = ["tipo", "moneda_denominacion", "ajuste_capital", "fecha_emision", "fecha_vencimiento",
          "tasa_nominal_anual_pct", "tasa_efectiva_mensual_pct", "intereses", "fechas_pago_intereses",
          "base_calculo", "amortizacion", "dias_habiles_rezago_cer"]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def norm_valor(campo, v):
    if campo == "amortizacion":
        return sorted((a["fecha"], round(float(a["porcentaje"]), 4)) for a in v)
    if campo == "fechas_pago_intereses":
        return sorted(v)
    if campo == "base_calculo":
        return norm(v or "").replace(" ", "")
    if isinstance(v, float):
        return round(v, 6)
    return v


def main() -> None:
    filas, solo = [], Counter()
    for f in sorted((LECT / "opus").glob("*.json")):
        o = json.loads(f.read_text(encoding="utf-8"))["titulos"]
        hf = LECT / "haiku" / f.name
        h = json.loads(hf.read_text(encoding="utf-8"))["titulos"] if hf.exists() else []
        clave = lambda t: (t["fecha_vencimiento"], norm(t["nombre"]))  # noqa: E731
        ho = {clave(t): t for t in h}
        for t in o:
            th = ho.pop(clave(t), None)
            if th is None:
                solo["solo_opus"] += 1
                filas.append([f.stem, t["nombre"], t["fecha_vencimiento"], "(título)", "sí", "no", "solo lo encontró Opus"])
                continue
            for c in CAMPOS:
                a, b = norm_valor(c, t[c]), norm_valor(c, th[c])
                filas.append([f.stem, t["nombre"], t["fecha_vencimiento"], c,
                              json.dumps(t[c], ensure_ascii=False), json.dumps(th[c], ensure_ascii=False),
                              "coincide" if a == b else "DIFIERE"])
        for t in ho.values():
            solo["solo_haiku"] += 1
            filas.append([f.stem, t["nombre"], t["fecha_vencimiento"], "(título)", "no", "sí", "solo lo encontró Haiku"])
    with (BASE / "datos" / "comparacion.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["resolucion", "titulo", "vencimiento", "campo", "opus", "haiku", "resultado"])
        w.writerows(filas)
    campos = [r for r in filas if r[3] != "(título)"]
    dif = Counter(r[3] for r in campos if r[6] == "DIFIERE")
    titulos = {(r[0], r[1]) for r in campos}
    tit_dif = {(r[0], r[1]) for r in campos if r[6] == "DIFIERE"}
    print(f"títulos emparejados: {len(titulos)}  |  {dict(solo)}")
    print(f"campos comparados: {len(campos)}, coinciden {len(campos) - sum(dif.values())}, difieren {sum(dif.values())}")
    print(f"títulos con al menos una diferencia: {len(tit_dif)}")
    print("diferencias por campo:", dict(dif.most_common()))


if __name__ == "__main__":
    main()
