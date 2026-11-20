"""Controles automáticos de lo que leyó la IA, contra el texto oficial. Sin IA y sin trabajo manual.

Cada lectura se revisa con reglas que no dependen del modelo:
  Resoluciones del Boletín (condiciones de cada bono):
    1. el vencimiento escrito en el nombre del título es la fecha_vencimiento que devolvió la IA;
    2. la emisión es anterior al vencimiento;
    3. las cuotas de amortización suman 100% y ninguna cae después del vencimiento;
    4. las fechas de pago de intereses caen entre la emisión y el vencimiento;
    5. cada tasa que devolvió (TNA, TEM) aparece escrita en el texto de la resolución.
  Comunicados de licitación (tasa de cada LECAP/BONCAP):
    6. el ticker corresponde a la fecha de vencimiento (S29E7 = 29 de enero de 2027);
    7. la TEM, el precio de corte y la TIREA aparecen escritos en el comunicado.

Uso: python controles_ia.py   -> datos/controles_ia.json (lo usa pesos.py para la sección "Qué hizo la IA")
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import leer_con_ia as L

BASE = Path(__file__).parent
SALIDA = BASE / "datos" / "controles_ia.json"
LETRA_MES = dict(zip("EFMAYJLGSOND", range(1, 13)))   # código de mes de los tickers del Tesoro


def numeros_en(texto: str) -> set[float]:
    """Todos los números del texto, en formato argentino (1.202,40) o con punto decimal."""
    out = set()
    for t in re.findall(r"\d[\d.]*,\d+|\d+\.\d+|\d+", texto):
        try:
            out.add(round(float(t.replace(".", "").replace(",", ".") if "," in t else t), 4))
        except ValueError:
            pass
    return out


def aparece(v: float | None, nums: set[float]) -> bool | None:
    if v is None:
        return None
    return any(abs(v - n) < 1e-6 for n in nums)


def control_resolucion(rid: str, lectura: dict, texto: str) -> list[dict]:
    nums = numeros_en(texto)
    res = []
    for t in lectura.get("titulos", []):
        nombre = t["nombre"]
        def r(regla, ok, detalle=""):
            if ok is not None:
                res.append({"fuente": f"Resolución {rid}", "titulo": nombre, "regla": regla, "ok": bool(ok), "detalle": detalle})
        vto = t.get("fecha_vencimiento")
        completa = re.search(r"\d{1,2} de \w+ de \d{4}|\d{1,2}/\d{1,2}/\d{4}", nombre)   # no solo el año
        en_nombre = L._fecha_nombre(nombre) if completa else None
        if en_nombre:
            r("Vencimiento del nombre = vencimiento leído", en_nombre.isoformat() == vto, f"nombre {en_nombre}, IA {vto}")
        em = t.get("fecha_emision")
        if em and vto:
            r("Emisión anterior al vencimiento", em < vto, f"{em} → {vto}")
        am = t.get("amortizacion") or []
        if am:
            total = sum(c["porcentaje"] for c in am)
            r("Amortización suma 100%", abs(total - 100) < 0.01, f"suma {total:g}%")
            r("Ninguna cuota después del vencimiento", all(c["fecha"] <= vto for c in am) if vto else None)
        fp = t.get("fechas_pago_intereses") or []
        if fp and em and vto:
            r("Pagos de interés entre emisión y vencimiento", all(em <= f <= vto for f in fp), f"{len(fp)} fechas")
        for campo, rotulo in (("tasa_nominal_anual_pct", "TNA"), ("tasa_efectiva_mensual_pct", "TEM")):
            if t.get(campo) is not None:
                r(f"La {rotulo} leída figura en el texto", aparece(t[campo], nums), f"{t[campo]:g}%")
    return res


def control_licitacion(archivo: str, lectura: dict, texto: str) -> list[dict]:
    nums = numeros_en(texto)
    res = []
    for t in lectura.get("titulos", []):
        tk, vto = t.get("ticker") or "", t.get("fecha_vencimiento")
        def r(regla, ok, detalle=""):
            if ok is not None:
                res.append({"fuente": "Licitación: " + archivo[:60], "titulo": tk or t["nombre"], "regla": regla, "ok": bool(ok), "detalle": detalle})
        m = re.fullmatch(r"[A-Z](\d{2})([EFMAYJLGSOND])(\d)", tk)
        if m and vto:
            d = date.fromisoformat(vto)
            r("El ticker corresponde al vencimiento", int(m.group(1)) == d.day and LETRA_MES[m.group(2)] == d.month
              and int(m.group(3)) == d.year % 10, f"{tk} vs {vto}")
        for campo, rotulo in (("tem_capitalizacion_pct", "TEM"), ("precio_corte", "precio de corte"), ("tirea_pct", "TIREA")):
            if t.get(campo) is not None:
                r(f"El {rotulo} leído figura en el comunicado" if rotulo != "TEM" else "La TEM leída figura en el comunicado",
                  aparece(t[campo], nums), f"{t[campo]:g}")
    return res


def correr() -> dict:
    res = []
    for f in sorted((L.SALIDA / "opus").glob("*.json")):
        fuente = L.FUENTES / f"{f.stem}.txt"
        if fuente.exists():
            res += control_resolucion(f.stem, json.loads(f.read_text(encoding="utf-8")), fuente.read_text(encoding="utf-8"))
    for f in sorted((L.SALIDA / "resultados").glob("*.json")):
        fuente = BASE / "fuentes" / "resultados" / f"{f.stem}.txt"
        if fuente.exists():
            res += control_licitacion(f.stem, json.loads(f.read_text(encoding="utf-8")), fuente.read_text(encoding="utf-8"))
    out = {"fecha": date.today().isoformat(), "total": len(res), "ok": sum(c["ok"] for c in res),
           "fallas": [c for c in res if not c["ok"]],
           "por_regla": {}}
    for c in res:
        g = out["por_regla"].setdefault(c["regla"], {"total": 0, "ok": 0})
        g["total"] += 1
        g["ok"] += c["ok"]
    SALIDA.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = correr()
    print(f"{o['ok']}/{o['total']} controles OK")
    for regla, g in o["por_regla"].items():
        print(f"  {g['ok']:>4}/{g['total']:<4} {regla}")
    for c in o["fallas"][:30]:
        print("FALLA", c["fuente"], "|", c["titulo"][:70], "|", c["regla"], "|", c["detalle"])
