"""Arma control/control_ia.xlsx para que el usuario verifique la lectura de la IA.

Hoja 1 "Diferencias": cada campo importante donde Opus y Haiku no coinciden (bonos vivos).
Hoja 2 "Muestra": 10 bonos vivos; los datos que leyó Opus, para comparar con el Boletín Oficial.
"""
import csv
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

import tickers as T

BASE = Path(__file__).parent
IDX = json.loads((BASE / "fuentes" / "boletin" / "indice.json").read_text(encoding="utf-8"))
MUESTRA = ["AO29", "AN29", "S29E7", "T15E7", "TY30P", "TZX27", "TZXM9", "X29E7", "D31M7", "TZV28"]
FORMATO = {"fechas_pago_intereses", "base_calculo"}  # diferencias de formato, no de contenido
EXPLICA = {
    "tipo": "Qué clase de título es (LECAP, CER, dual, etc.)",
    "intereses": "Cómo paga intereses (cero cupón, capitalizable, mensual...)",
    "ajuste_capital": "Si el CAPITAL se ajusta por algo (CER, dólar A3500). Ojo: TAMAR ajusta la tasa, no el capital",
    "(título)": "Si el modelo encontró este título en la resolución",
}
MESES = "ene feb mar abr may jun jul ago sep oct nov dic".split()


def f(d):
    if not d:
        return "—"
    a, m, dd = d.split("-")
    return f"{int(dd)}/{int(m)}/{a}"


def estilo(ws, anchos, amarilla):
    thin = Side(style="thin", color="D9D9D9")
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"].font = Font(italic=True, color="6B7480")
    for row in ws.iter_rows(min_row=5):
        for c in row:
            c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
            c.alignment = Alignment(vertical="center", wrap_text=True)
        row[amarilla].fill = PatternFill("solid", fgColor="FFF7D6")
        row[amarilla].alignment = Alignment(horizontal="center", vertical="center")
    for i, w in enumerate(anchos):
        ws.column_dimensions[chr(65 + i)].width = w
    ws.freeze_panes = "A5"


def main():
    tk = {(r["vencimiento"], r["nombre"]): r for r in csv.DictReader(
        open(BASE / "datos" / "tickers.csv", encoding="utf-8-sig"), delimiter=";")
        if r["estado"] == "ok" and r["vencimiento"] > "2026-11-10"}
    comp = list(csv.DictReader(open(BASE / "datos" / "comparacion.csv", encoding="utf-8-sig"), delimiter=";"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Diferencias"
    ws.append(["Parte 1 – Donde Opus y Haiku no coinciden"])
    ws.append(["Abrí el link, buscá el título y elegí quién tiene razón en la columna amarilla (Opus, Haiku, Los dos o Ninguno)."])
    ws.append([])
    ws.append(["Ticker", "Título", "Campo", "Qué significa", "Leyó Opus", "Leyó Haiku", "¿Quién tiene razón?", "Comentario", "Resolución (link)"])
    n1 = 0
    for r in comp:
        k = (r["vencimiento"], r["titulo"])
        if k not in tk or r["resultado"] == "coincide" or r["campo"] in FORMATO:
            continue
        res = tk[k]["resolucion"]
        ws.append([tk[k]["ticker"], r["titulo"], r["campo"], EXPLICA.get(r["campo"], ""),
                   r["opus"].strip('"'), r["haiku"].strip('"'), "", "", IDX[res]["url"]])
        ws.cell(ws.max_row, 9).hyperlink = IDX[res]["url"]
        n1 += 1
    dv = DataValidation(type="list", formula1='"Opus,Haiku,Los dos,Ninguno"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"G5:G{ws.max_row}")
    estilo(ws, [9, 40, 16, 34, 22, 22, 14, 28, 30], 6)

    ws2 = wb.create_sheet("Muestra")
    ws2.append(["Parte 2 – Muestra de 10 bonos: ¿lo que leyó la IA es lo que dice el Boletín Oficial?"])
    ws2.append(["Abrí el link, buscá el título (Ctrl+F con la fecha de vencimiento) y poné Sí o No. Si es No, anotá qué dice el texto."])
    ws2.append([])
    ws2.append(["Ticker", "Título", "Dato", "Leyó la IA (Opus)", "¿Coincide?", "Si no: qué dice el texto", "Resolución (link)"])
    tit = {(t["tipo"], t["fecha_vencimiento"]): t for t in T.titulos_ia("opus")}
    por_ticker = {r["ticker"]: r for r in tk.values()}
    for tkr in MUESTRA:
        r = por_ticker[tkr]
        t = tit[(r["tipo"], r["vencimiento"])]
        fp = t["fechas_pago_intereses"]
        tasa = t["tasa_nominal_anual_pct"]
        datos = [
            ("Fecha de emisión", f(t["fecha_emision"])),
            ("Fecha de vencimiento", f(t["fecha_vencimiento"])),
            ("Tasa de interés", f"{tasa:g}% nominal anual".replace(".", ",") if tasa is not None
             else ("Se fija en la licitación (no está en el texto)" if t["intereses"] in ("capitalizable_al_vencimiento", "semestral")
                   else "No paga (cero cupón / a descuento)")),
            ("Cómo paga intereses", t["intereses"].replace("_", " ")),
            ("Fechas de pago de intereses", f"{len(fp)} pagos, del {f(fp[0])} al {f(fp[-1])}" if fp else "Ninguna"),
            ("Amortización", " + ".join(f"{a['porcentaje']:g}% el {f(a['fecha'])}" for a in t["amortizacion"])),
            ("Ajuste del capital", t["ajuste_capital"] + (f", CER con {t['dias_habiles_rezago_cer']} días hábiles de rezago"
                                                           if t["dias_habiles_rezago_cer"] else "")),
        ]
        url = IDX[t["resolucion"]]["url"] if t["resolucion"] in IDX else \
            "https://www.boletinoficial.gob.ar/detalleAviso/primera/233685/20200816"  # carga manual (Decreto 676/2020)
        for dato, val in datos:
            ws2.append([tkr, t["nombre"], dato, val, "", "", url])
            ws2.cell(ws2.max_row, 7).hyperlink = url
    dv2 = DataValidation(type="list", formula1='"Sí,No"', allow_blank=True)
    ws2.add_data_validation(dv2)
    dv2.add(f"E5:E{ws2.max_row}")
    estilo(ws2, [9, 42, 24, 44, 12, 34, 30], 4)

    out = BASE / "control" / "control_ia.xlsx"
    wb.save(out)
    print(f"Diferencias: {n1} filas | Muestra: {ws2.max_row - 4} filas -> {out}")


if __name__ == "__main__":
    main()
