"""La IA lee los comunicados de resultado de licitación y saca, para cada título NUEVO,
la tasa que se fijó en la licitación (TEM de capitalización de LECAP/BONCAP o cupón).

Uso: python leer_resultados.py   -> datos/lecturas/resultados/<archivo>.json
"""
import json
from pathlib import Path

import anthropic

BASE = Path(__file__).parent
FUENTES = BASE / "fuentes" / "resultados"
SALIDA = BASE / "datos" / "lecturas" / "resultados"
NUM_O_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}

ESQUEMA = {
    "type": "object",
    "properties": {"titulos": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "nombre": {"type": "string"},
            "ticker": {"type": "string", "description": "Si figura (ej. S13N6); vacío si no"},
            "fecha_vencimiento": {"type": "string", "description": "AAAA-MM-DD"},
            "es_nuevo": {"type": "boolean", "description": "true si el comunicado lo marca como nuevo / primera emisión"},
            "tem_capitalizacion_pct": {**NUM_O_NULL, "description": "Tasa efectiva mensual capitalizable desde la emisión hasta el vencimiento, en %"},
            "tasa_cupon_pct": {**NUM_O_NULL, "description": "Tasa de cupón nominal anual fijada en la licitación, en %"},
            "precio_corte": {**NUM_O_NULL, "description": "Precio de corte tal como figura"},
            "tirea_pct": {**NUM_O_NULL, "description": "TIREA de corte en %"},
        },
        "required": ["nombre", "ticker", "fecha_vencimiento", "es_nuevo", "tem_capitalizacion_pct",
                     "tasa_cupon_pct", "precio_corte", "tirea_pct"],
        "additionalProperties": False}}},
    "required": ["titulos"], "additionalProperties": False,
}

SISTEMA = """Sos un analista de renta fija argentina. Vas a recibir el comunicado oficial de la Secretaría \
de Finanzas con el resultado de una licitación del Tesoro. Listá todos los títulos de la licitación \
a tasa fija en pesos (LECAP, BONCAP, BONTE) con los datos de corte. Copiá los valores tal como figuran \
en el texto; no completes con conocimiento propio. Si un dato no figura, usá null. \
Ojo: si el comunicado distingue entre la TEM de corte y la "tasa efectiva mensual capitalizable desde \
la fecha de emisión hasta el vencimiento", en tem_capitalizacion_pct va esta última."""


def _registrar(ent: int, sal: int) -> None:
    """Suma el gasto a datos/lecturas/uso.json, rubro "resultados" (Opus, precio normal)."""
    archivo = BASE / "datos" / "lecturas" / "uso.json"
    uso = json.loads(archivo.read_text(encoding="utf-8"))
    u = uso.setdefault("resultados", {"tokens_entrada": 0, "tokens_salida": 0, "costo_usd": 0.0})
    u["tokens_entrada"] = (u["tokens_entrada"] or 0) + ent
    u["tokens_salida"] = (u["tokens_salida"] or 0) + sal
    u["costo_usd"] = round(u["costo_usd"] + (ent * 4 + sal * 20) / 1e6, 4)
    archivo.write_text(json.dumps(uso, indent=1), encoding="utf-8")


def main() -> None:
    elegidos = json.loads((BASE / "datos" / "resultados_elegidos.json").read_text(encoding="utf-8"))
    archivos = sorted({a for lista in elegidos.values() for a in lista})
    client = anthropic.Anthropic()
    SALIDA.mkdir(parents=True, exist_ok=True)
    ent = sal = 0
    for a in archivos:
        out = SALIDA / a.replace(".txt", ".json")
        if out.exists():
            continue
        texto = (FUENTES / a).read_text(encoding="utf-8")
        msg = client.messages.create(
            model="claude-opus-5-5", max_tokens=16000, system=SISTEMA,
            output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
            messages=[{"role": "user", "content": f"<comunicado>\n{texto}\n</comunicado>"}],
        )
        ent += msg.usage.input_tokens
        sal += msg.usage.output_tokens
        _registrar(msg.usage.input_tokens, msg.usage.output_tokens)
        if msg.stop_reason != "end_turn":
            print(a, "terminó por", msg.stop_reason)
            continue
        datos = json.loads(next(b.text for b in msg.content if b.type == "text"))
        out.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        print(a, "->", len(datos["titulos"]), "títulos")
    print(f"tokens {ent} / {sal}, costo US$ {(ent * 4 + sal * 20) / 1e6:.3f}")


if __name__ == "__main__":
    main()
