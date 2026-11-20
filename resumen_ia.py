"""Párrafo diario "Qué pasó hoy", escrito por la IA y controlado número por número.

1. El programa (no la IA) arma los HECHOS del día: inflación implícita por período, diferencia con el REM,
   cambio contra la rueda anterior, bonos que más movieron su TIR, bonos fuera de la curva, normas leídas hoy.
2. Claude Opus escribe 3 a 5 oraciones con esos hechos y nada más.
3. Control automático: cada número del párrafo tiene que estar en los hechos. Si uno no está (la IA inventó o
   calculó por su cuenta), se le pide corregirlo una vez; si vuelve a fallar, ese día no se publica párrafo.
4. Se guarda en datos/resumen/<fecha de cierre>.json con el costo. Nunca se paga dos veces el mismo cierre.

Lo llama pesos.py solo con la rueda cerrada. Sin ANTHROPIC_API_KEY guarda los hechos y no escribe párrafo.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import leer_con_ia as L

BASE = Path(__file__).parent
CARPETA = BASE / "datos" / "resumen"
MODELO = "claude-opus-5-5"
L.PRECIOS.setdefault("resumen", L.PRECIOS["opus"])

SISTEMA = """Sos analista de renta fija en pesos en un equipo de research de Buenos Aires. Escribís el párrafo \
diario que abre el tablero de bonos soberanos en pesos.

Reglas:
- Entre 3 y 5 oraciones, en castellano rioplatense, tono de informe (sin exclamaciones ni adjetivos de color).
- Usá SOLO los números que vienen en <hechos>, con la misma cantidad de decimales y coma decimal (1,96%). \
No calcules números nuevos (ni sumas, ni restas, ni promedios): si una diferencia no está en los hechos, no la menciones.
- Empezá por lo más importante del día: la inflación implícita y su cambio contra la rueda anterior, y la \
comparación con el REM. Después, si hay, los movimientos de TIR más grandes y las novedades (normas, bonos fuera).
- "Inflación implícita" es lo que descuentan los precios e incluye prima de riesgo y de liquidez: no la \
presentes como pronóstico del mercado.
- No recomiendes comprar ni vender, ni anticipes lo que va a pasar.
- Devolvé solo el párrafo, sin título."""


def _num(tok: str) -> float:
    return float(tok.replace(".", "").replace(",", "."))


def numeros_parrafo(texto: str) -> list[tuple[str, float, int]]:
    """Números escritos en formato argentino: (texto, valor, decimales)."""
    out = []
    for t in re.findall(r"\d+(?:\.\d{3})*(?:,\d+)?", texto):
        dec = len(t.split(",")[1]) if "," in t else 0
        out.append((t, _num(t), dec))
    return out


def permitidos(hechos: dict) -> set[float]:
    vals: set[float] = set()
    def rec(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            vals.add(abs(float(x)))
        elif isinstance(x, str):
            vals.update(float(n) for n in re.findall(r"\d+", x))
        elif isinstance(x, dict):
            for k, v in x.items():
                rec(k), rec(v)
        elif isinstance(x, list):
            for v in x:
                rec(v)
    rec(hechos)
    return vals


def controlar(parrafo: str, hechos: dict) -> list[str]:
    """Números del párrafo que no salen de los hechos que vio la IA (con el redondeo que usó el párrafo)."""
    ok = permitidos({k: v for k, v in hechos.items() if k != "tir"})
    return [t for t, v, dec in numeros_parrafo(parrafo) if not any(round(p, dec) == round(v, dec) for p in ok)]


def anterior(cierre: str) -> dict | None:
    previos = sorted(f for f in CARPETA.glob("*.json") if f.stem < cierre) if CARPETA.exists() else []
    return json.loads(previos[-1].read_text(encoding="utf-8")) if previos else None


def armar_hechos(cierre: str, pers: list[dict], filas: list[dict], rem_edicion: str | None, normas_hoy: list[str]) -> dict:
    prev = anterior(cierre)
    pp = (prev or {}).get("hechos", {})
    r2 = lambda v: None if v is None else round(v, 2)
    periodos = []
    for p in pers[:2]:                                    # próximos 3 y 6 meses
        antes = next((q for q in pp.get("periodos", []) if q["nombre"] == p["nombre"]), None)
        periodos.append({
            "nombre": p["nombre"], "meses": p["meses"], "mensual_pct": r2(p["mensual"]), "acumulada_pct": r2(p["acumulada"]),
            "rem_mensual_pct": r2(p["rem_mensual"]),
            "diferencia_con_rem_pp": r2(p["mensual"] - p["rem_mensual"]) if p["rem_mensual"] is not None else None,
            "cambio_vs_rueda_anterior_pp": r2(p["mensual"] - antes["mensual_pct"]) if antes else None})
    tir = {f["ticker"]: round(f["tir"], 2) for f in filas if f["curva"] in ("pesos_fija", "cer") and not f["revisar"]}
    movs = []
    if pp.get("tir"):
        for tk, v in tir.items():
            if tk in pp["tir"]:
                movs.append({"bono": tk, "tir_pct": v, "cambio_pb": round((v - pp["tir"][tk]) * 100)})
        movs = sorted(movs, key=lambda m: -abs(m["cambio_pb"]))[:3]
    return {
        "cierre": cierre, "rueda_anterior": (prev or {}).get("hechos", {}).get("cierre"),
        "rem_edicion": rem_edicion, "periodos": periodos, "mayores_movimientos_tir": movs,
        "bonos_fuera_de_la_curva": [{"bono": f["ticker"], "motivo": f.get("motivo")} for f in filas
                                    if f["curva"] in ("pesos_fija", "cer") and f["revisar"]],
        "normas_leidas_hoy_por_la_ia": normas_hoy, "tir": tir}


def _pedir(client, hechos: dict, extra: list[dict]) -> tuple[str, int, int]:
    visibles = {k: v for k, v in hechos.items() if k != "tir"}      # la tabla completa de TIR no hace falta
    msgs = [{"role": "user", "content": "<hechos>\n" + json.dumps(visibles, ensure_ascii=False, indent=1) + "\n</hechos>"}] + extra
    msg = client.beta.messages.create(
        model=MODELO, max_tokens=16000, system=SISTEMA, messages=msgs,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if msg.stop_reason == "refusal":
        raise RuntimeError("el modelo rechazó el pedido")
    texto = "".join(b.text for b in msg.content if b.type == "text").strip()
    return texto, msg.usage.input_tokens, msg.usage.output_tokens


def generar(hechos: dict) -> dict:
    """Devuelve {"hechos", "parrafo" (o None), "control", "costo_usd"} y lo guarda. No repite un cierre ya escrito."""
    CARPETA.mkdir(parents=True, exist_ok=True)
    destino = CARPETA / f"{hechos['cierre']}.json"
    if destino.exists():
        viejo = json.loads(destino.read_text(encoding="utf-8"))
        if viejo.get("parrafo") or viejo.get("control", {}).get("intentos"):
            return viejo
    out = {"hechos": hechos, "parrafo": None, "control": {"intentos": 0, "fallas": []}, "costo_usd": 0.0, "modelo": MODELO}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        out["control"]["nota"] = "sin ANTHROPIC_API_KEY: solo se guardan los hechos"
        destino.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        return out
    import anthropic
    client = anthropic.Anthropic()
    extra: list[dict] = []
    for intento in (1, 2):
        texto, ent, sal = _pedir(client, hechos, extra)
        out["costo_usd"] = round(out["costo_usd"] + L._guardar_uso("resumen", ent, sal, 1.0), 4)
        malos = controlar(texto, hechos)
        out["control"]["intentos"] = intento
        out["control"]["fallas"].append(malos)
        if not malos:
            out["parrafo"] = texto
            break
        extra = [{"role": "assistant", "content": texto},
                 {"role": "user", "content": f"Estos números no están en los hechos: {', '.join(malos)}. "
                                             "Reescribí el párrafo usando solo números de <hechos>."}]
    destino.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
