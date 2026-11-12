"""Compara bonos.json contra el texto del Decreto 701/2020 (fuentes/decreto_701_2020.txt).

Lee del decreto, para cada bono en dólares: cantidad de cuotas, fecha de la
primera y la última cuota, % de la primera cuota si es distinta, y cada tasa
con la fecha desde la que rige. Imprime OK o la diferencia.
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).parent
MESES = {"enero": 1, "julio": 7, "septiembre": 9}
NUM = {"DIEZ": 10, "TRECE": 13, "VEINTIDOS": 22, "VEINTIOCHO": 28, "CUARENTA Y CUATRO": 44}

texto = (BASE / "fuentes" / "decreto_701_2020.txt").read_text(encoding="utf-8")
texto = re.sub(r"(20\d) (\d)", r"\1\2", texto)          # "202 1" -> "2021" (cortes del PDF)
texto = re.sub(r"\s+", " ", texto)

secciones = re.split(r" [A-L]\. “BONOS GLOBALES", " " + texto)[1:]
fam = json.loads((BASE / "bonos.json").read_text(encoding="utf-8"))["familias"]


def fecha(dia, mes, anio):
    return f"{anio}-{MESES[mes]:02d}-{int(dia):02d}"


errores = 0
for sec in secciones:
    m = re.match(r" DE LA REPÚBLICA ARGENTINA AMORTIZABLES EN (DÓLARES ESTADOUNIDENSES|EUROS) (?:STEP UP |1% |0.50% )?(20\d\d)", sec)
    if not m or not m.group(1).startswith("DÓLARES"):
        continue
    anio = m.group(2)
    leido = {}
    c = re.search(r"en ([A-Z ]+?) \(\d+\) cuotas", sec)
    leido["cuotas"] = NUM[c.group(1).strip()]
    p = re.search(r"primera cuota el (\d+) de (\w+) de (\d{4})", sec)
    u = re.search(r"última cuota el (\d+) de (\w+) de (\d{4})", sec)
    leido["primera"] = fecha(*p.groups())
    leido["ultima"] = fecha(*u.groups())
    pp = re.search(r"primera cuota representativa del (\d+)%", sec)
    leido["primera_pct"] = float(pp.group(1)) if pp else None
    tasas = re.findall(r"Del (\d+) de (\w+) de (\d{4}) \((?:inclusive|exclusive)\) al .*?\((\d+,\d+)%\)", sec)
    leido["tasas"] = [[fecha(d, mes, a), float(t.replace(",", "."))] for d, mes, a, t in tasas]

    cargado = fam[anio]
    difs = []
    for k in ("cuotas", "primera", "ultima", "primera_pct"):
        if cargado["amortizacion"][k] != leido[k]:
            difs.append(f"{k}: cargado {cargado['amortizacion'][k]} / decreto {leido[k]}")
    if cargado["tasas"] != leido["tasas"]:
        difs.append(f"tasas: cargado {cargado['tasas']} / decreto {leido['tasas']}")
    bonos = "/".join(cargado["bonos"])
    if difs:
        errores += 1
        print(f"{bonos}: DIFERENCIA")
        for d in difs:
            print("   ", d)
    else:
        print(f"{bonos}: OK ({leido['cuotas']} cuotas {leido['primera']} a {leido['ultima']}, {len(leido['tasas'])} tasas)")

print("Sin diferencias" if errores == 0 else f"{errores} bono(s) con diferencias")
