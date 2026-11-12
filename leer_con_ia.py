"""Lectura con IA de las resoluciones del Boletín Oficial.

Cada resolución se manda a Claude Opus (Haiku se probó y se descartó).
El modelo devuelve, con un formato fijo (JSON), las condiciones de cada título NUEVO
que la resolución emite. Las respuestas quedan en datos/lecturas/<modelo>/<id>.json.

Uso:
  python leer_con_ia.py seleccionar   # qué resoluciones faltan leer (gratis)
  python leer_con_ia.py estimar       # cuenta tokens y estima el costo de las que faltan (gratis)
  python leer_con_ia.py enviar        # manda las que faltan por lote, mitad de precio (cuesta)
  python leer_con_ia.py traer         # baja los resultados del lote cuando termina
  python leer_con_ia.py auto          # lo que corre cada día: lee las que faltan en el momento

Solo se leen resoluciones que todavía no tienen lectura guardada: nunca se paga dos veces la misma.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

BASE = Path(__file__).parent
FUENTES = BASE / "fuentes" / "boletin"
SALIDA = BASE / "datos" / "lecturas"
ESTADO = BASE / "datos" / "lotes.json"
MODELOS = {"opus": "claude-opus-5-5"}
PRECIOS = {"opus": (4.00, 20.00)}  # US$ por millón (entrada, salida), sin descuento de lote

MESES = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}

FECHA = {"type": "string", "description": "AAAA-MM-DD"}
NUM_O_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}
INT_O_NULL = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "articulo": {"type": "string", "description": "Ej.: 'ARTÍCULO 2º'"},
                    "nombre": {"type": "string", "description": "Nombre completo del título, tal como figura entre comillas"},
                    "tipo": {"type": "string", "enum": [
                        "letra_capitalizable_pesos", "bono_capitalizable_pesos", "bono_tasa_fija_pesos",
                        "letra_cer", "bono_cer", "letra_dolar_linked", "bono_dolar_linked",
                        "bono_dolares", "tamar", "dual", "otro"]},
                    "moneda_denominacion": {"type": "string", "enum": ["ARS", "USD"]},
                    "ajuste_capital": {"type": "string", "enum": ["ninguno", "CER", "tipo_cambio_A3500", "TAMAR", "otro"]},
                    "fecha_emision": FECHA,
                    "fecha_vencimiento": FECHA,
                    "tasa_nominal_anual_pct": {**NUM_O_NULL, "description": "Tasa de interés nominal anual en %, si la hay"},
                    "tasa_efectiva_mensual_pct": {**NUM_O_NULL, "description": "Para títulos capitalizables: TEM en %"},
                    "intereses": {"type": "string", "enum": [
                        "cero_cupon_descuento", "capitalizable_al_vencimiento", "mensual", "trimestral",
                        "semestral", "anual", "otro"]},
                    "fechas_pago_intereses": {"type": "array", "items": FECHA},
                    "base_calculo": {"type": "string", "description": "Ej.: 30/360, actual/365"},
                    "amortizacion": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"fecha": FECHA, "porcentaje": {"type": "number"}},
                        "required": ["fecha", "porcentaje"], "additionalProperties": False}},
                    "dias_habiles_rezago_cer": {**INT_O_NULL, "description": "Ej.: 10 si usa el CER de 10 días hábiles antes"},
                    "ley_aplicable": {"type": "string", "enum": ["argentina", "nueva_york", "otra", "no_figura"]},
                    "notas": {"type": "string", "description": "Algo que no entre en los campos anteriores; vacío si no hay"},
                },
                "required": ["articulo", "nombre", "tipo", "moneda_denominacion", "ajuste_capital",
                             "fecha_emision", "fecha_vencimiento", "tasa_nominal_anual_pct",
                             "tasa_efectiva_mensual_pct", "intereses", "fechas_pago_intereses",
                             "base_calculo", "amortizacion", "dias_habiles_rezago_cer", "ley_aplicable", "notas"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["titulos"],
    "additionalProperties": False,
}

SISTEMA = """Sos un analista de renta fija argentina. Vas a recibir el texto de una resolución conjunta \
de la Secretaría de Finanzas y la Secretaría de Hacienda publicada en el Boletín Oficial.

Extraé las condiciones financieras de cada título que la resolución EMITE POR PRIMERA VEZ \
("Dispónese la emisión de..."). No incluyas ampliaciones o reaperturas de títulos ya emitidos, \
ni conversiones o canjes de títulos existentes, salvo que el artículo emita un título nuevo.

Reglas:
- Copiá los datos tal como están en el texto; no completes con conocimiento propio.
- Fechas en formato AAAA-MM-DD.
- Si la amortización es "íntegra al vencimiento", devolvé una sola cuota de 100 en la fecha de vencimiento.
- fechas_pago_intereses: todas las fechas que figuren en el texto; si el texto sólo da una regla \
(p. ej. "semestralmente los días 15 de junio y 15 de diciembre"), generá la lista completa hasta el vencimiento.
- Si un dato no figura, usá null (o lista vacía) y explicalo en notas.
- Si la resolución no emite ningún título nuevo, devolvé "titulos": []."""


def _fecha_nombre(nombre: str) -> date | None:
    """Vencimiento escrito en el nombre del título, en cualquiera de las formas que usa el Boletín:
    '15 de diciembre de 2026', '30/11/2031' o solo el año ('VTO. 2028', se toma el 31/12 para no descartarlo)."""
    m = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", nombre, re.I)
    if m and m.group(2).lower() in MESES:
        return date(int(m.group(3)), MESES[m.group(2).lower()], int(m.group(1)))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", nombre)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"(?:vto\.?|vencimiento)\s*(20\d{2})", nombre, re.I)
    if m:
        return date(int(m.group(1)), 12, 31)
    return None


def seleccionar(hoy: date | None = None) -> list[str]:
    """Resoluciones que emiten por primera vez algún título que todavía no venció."""
    hoy = hoy or date.today()
    elegidas = []
    for f in sorted(FUENTES.glob("*.txt")):
        t = f.read_text(encoding="utf-8")
        nombres = re.findall(r"Disp[óo]nese la emisi[óo]n de(?:l| la| los| las) [“\"]([^”\"]+)[”\"]", t)
        # canjes y conversiones que crean un título: "... de un nuevo instrumento denominado “...”"
        nombres += re.findall(r"nuevos? instrumentos? denominados? [“\"]([^”\"]+)[”\"]", t, re.I)
        vivos = [n for n in nombres if (d := _fecha_nombre(n)) and d > hoy]
        if vivos:
            elegidas.append(f.stem)
    return elegidas


def pendientes() -> list[str]:
    ya = {f.stem for f in (SALIDA / "opus").glob("*.json")}
    return [i for i in seleccionar() if i not in ya]


def _guardar_uso(m: str, ent: int, sal: int, factor: float) -> float:
    """Suma el gasto al registro (no lo pisa). factor = 0,5 en lote, 1 en el momento."""
    archivo = SALIDA / "uso.json"
    uso = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}
    p_ent, p_sal = PRECIOS[m]
    costo = (ent * p_ent + sal * p_sal) / 1e6 * factor
    u = uso.setdefault(m, {"tokens_entrada": 0, "tokens_salida": 0, "costo_usd": 0.0})
    u["tokens_entrada"] += ent
    u["tokens_salida"] += sal
    u["costo_usd"] = round(u["costo_usd"] + costo, 4)
    archivo.write_text(json.dumps(uso, indent=1), encoding="utf-8")
    return costo


def _guardar_lectura(m: str, id_: str, msg) -> bool:
    if msg.stop_reason != "end_turn":
        print(m, id_, "terminó por", msg.stop_reason, "- no se guarda")
        return False
    txt = next(b.text for b in msg.content if b.type == "text")
    (SALIDA / m).mkdir(parents=True, exist_ok=True)
    (SALIDA / m / f"{id_}.json").write_text(json.dumps(json.loads(txt), ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def auto(ids: list[str]) -> list[str]:
    """Lee en el momento (sin lote) las resoluciones pendientes. Devuelve las leídas."""
    if not ids:
        return []
    client = anthropic.Anthropic()
    leidas = []
    for i in ids:
        with client.messages.stream(**_pedido("opus", _texto(i))) as st:
            msg = st.get_final_message()
        costo = _guardar_uso("opus", msg.usage.input_tokens, msg.usage.output_tokens, 1.0)
        if _guardar_lectura("opus", i, msg):
            leidas.append(i)
        print(f"opus leyó {i}: US$ {costo:.3f}")
    return leidas


def _pedido(modelo: str, texto: str) -> dict:
    return dict(
        model=MODELOS[modelo],
        max_tokens=16000,
        system=SISTEMA,
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
        messages=[{"role": "user", "content": f"<resolucion>\n{texto}\n</resolucion>"}],
    )


def _texto(id_: str) -> str:
    t = (FUENTES / f"{id_}.txt").read_text(encoding="utf-8")
    i = t.find("ARTÍCULO 1")
    return t[max(0, t.find("Resolución Conjunta")):] if i < 0 else t[max(0, i - 3000):]


def estimar(ids: list[str]) -> None:
    client = anthropic.Anthropic()
    tokens = {m: 0 for m in MODELOS}
    for i in ids:
        for m in MODELOS:
            p = _pedido(m, _texto(i))
            r = client.messages.count_tokens(model=p["model"], system=p["system"], messages=p["messages"])
            tokens[m] += r.input_tokens
    total = 0.0
    for m, tk in tokens.items():
        ent, sal = PRECIOS[m]
        salida = len(ids) * 3000  # supuesto: ~3.000 tokens de salida (con razonamiento) por resolución
        costo = (tk * ent + salida * sal) / 1e6 / 2  # lote = mitad de precio
        total += costo
        print(f"{m}: {len(ids)} resoluciones, {tk:,} tokens de entrada, costo estimado US$ {costo:.2f}")
    print(f"total estimado: US$ {total:.2f}")


def enviar(ids: list[str]) -> None:
    client = anthropic.Anthropic()
    estado = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    for m in MODELOS:
        pedidos = [Request(custom_id=f"{m}-{i}", params=MessageCreateParamsNonStreaming(**_pedido(m, _texto(i))))
                   for i in ids]
        lote = client.messages.batches.create(requests=pedidos)
        estado[m] = lote.id
        print(m, "lote", lote.id, lote.processing_status)
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(estado, indent=1), encoding="utf-8")


def traer() -> None:
    client = anthropic.Anthropic()
    estado = json.loads(ESTADO.read_text(encoding="utf-8"))
    for m, lote_id in list(estado.items()):
        lote = client.messages.batches.retrieve(lote_id)
        if lote.processing_status != "ended":
            print(m, "todavía procesando:", lote.request_counts)
            continue
        ent = sal = 0
        for r in client.messages.batches.results(lote_id):
            id_ = r.custom_id.split("-", 1)[1]
            if r.result.type != "succeeded":
                print(m, id_, "falló:", r.result.type)
                continue
            msg = r.result.message
            ent += msg.usage.input_tokens
            sal += msg.usage.output_tokens
            _guardar_lectura(m, id_, msg)
        print(m, f"US$ {_guardar_uso(m, ent, sal, 0.5):.3f}")
        del estado[m]  # el lote ya se bajó: no volver a sumarlo
    ESTADO.write_text(json.dumps(estado, indent=1), encoding="utf-8")


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else "seleccionar"
    ids = pendientes()
    if accion == "seleccionar":
        print(len(ids), "resoluciones:", ", ".join(ids))
    elif accion == "estimar":
        estimar(ids)
    elif accion == "enviar":
        enviar(ids)
    elif accion == "traer":
        traer()
    elif accion == "auto":
        auto(ids)
