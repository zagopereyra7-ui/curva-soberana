# Metodología — Curva soberana argentina

**Versión vigente: 1.0** (desde el 13/11/2026)

Regla: los números pueden cambiar porque cambia el mercado, **no** porque cambia la cuenta. Ningún parámetro de
este documento se toca sin decidirlo, subir la versión y anotarlo en el registro de cambios del final. Las críticas
y mejoras nuevas van a la lista de pendientes y se aplican juntas, en una versión nueva.

## Qué se publica

- La página publicada (`docs/index.html`) se arma **solo con precios de cierre** (corrida automática de las 19:30,
  o cualquier corrida después de las 17:30 o en un día no hábil).
- Si se corre con la rueda abierta, la página se guarda aparte como `docs/preliminar.html` (con un cartel) y no se
  guarda nada en los historiales.

## Inflación implícita: un único método

**Bootstrapping de curvas nominal y real** (el que usa el BCRA, Nota Técnica 3/2019). Pasos:

1. **Tasa fija al plazo de cada bono CER**: la LECAP/BONCAP que vence el mismo día (gemela); si no hay,
   interpolación lineal de ln(1 + TIR) entre las dos LECAP/BONCAP vecinas. Sin extrapolar.
2. **Inflación futura que paga cada bono CER**: suba total del CER = (1 + tasa fija)^t ÷ (1 + TIR real)^t,
   dividida por la parte del CER ya publicada. Ventana: del último CER publicado al CER que usa el bono al vencer
   (vencimiento − 10 días hábiles).
3. **Bloques**: entre dos vencimientos CER consecutivos, inflación promedio por mes de IPC
   = (suba futura hasta el bono ÷ suba futura hasta el anterior)^(1/meses) − 1.
4. **Mes a mes**: interpolación lineal de la curva acumulada (ln de la suba) en meses de IPC. Un mes sin
   vencimiento propio toma el promedio de su bloque y se marca "promedio de su bloque"; no se compara con el REM.
5. **Ritmo anualizado** = (1 + inflación del mes)^12 − 1.
6. **Puntas**: ± media punta (spread comprador/vendedor) de cada bono que delimita el bloque.
7. **Zigzag**: si bloques seguidos se contradicen más allá de sus puntas y en más de 0,5 pp, se busca el bono que
   lo explica (el que, al sacarlo, deja menos contradicciones) y se avisa.
8. **Regla del CER (BCRA)**: la inflación del mes M sube el CER del 16 de M+1 al 15 de M+2.

Por qué este y no otros: es exacto (con los mismos precios da siempre lo mismo, sin parámetros que elegir) y es el
estándar cuando hay pocos bonos. Comparar pares del mismo vencimiento solo da números donde hay gemelos; las curvas
suaves Nelson-Siegel/Svensson (Fed, bancos centrales) están pensadas para decenas de bonos y con 4–6 CER cortos
sobreajustan (Svensson tiene 6 parámetros).

## Parámetros fijos (v1.0)

| Parámetro | Valor | Dónde |
|---|---|---|
| Operaciones mínimas en el día para usar un precio | 10 | `actualizar.py` MIN_OPERACIONES |
| Spread máximo entre puntas | 2% | `actualizar.py` MAX_SPREAD_PCT |
| Bono "lejos de su curva" (pp de TIR contra sus vecinos) | dólares 4; tasa fija 5; CER 4; dollar linked 6 | `actualizar.py` UMBRAL_PP |
| Días mínimos al vencimiento para entrar a una curva | 7 | `nelson_siegel.py` |
| Días mínimos entre vencimientos CER para armar bloque | 20 | `nelson_siegel.py` MIN_DIAS_BLOQUE |
| Spread mínimo usado como peso | 0,05% | `nelson_siegel.py` |
| Rezago del CER | 10 días hábiles | resoluciones del Tesoro |
| Zigzag | bandas que no se tocan y diferencia > 0,5 pp | `nelson_siegel.py` |
| Bonos con cupón (TY30P, TX28, TX31) | fuera de la inflación mes a mes | `nelson_siegel.py` |
| Comparación con el REM | solo meses que la encuesta pregunta uno por uno | `nelson_siegel.py` |
| Puntaje "quién le pega": foto del mercado | último cierre ≤ día de cierre de la encuesta (hasta 4 días antes) | `nelson_siegel.py` MAX_DIAS_ANTES |
| Ediciones mínimas para declarar ganador | 6 | `quien_acierta.py` |
| TAMAR: serie | BCRA id 44 (bancos privados) | `tamar.py` |
| TAMAR: márgenes de emisión | TMF27 6,50 · TML27 5,40 · TMG27 6,00 · TMF28 6,50 · TMG28 6,90 · M30A7 2,50 | `tamar.py` |
| TAMAR TEM | [(1 + (TAMAR + margen) × 32/365)^(365/32)]^(1/12) − 1 (supuesto) | `tamar.py` |
| Dollar linked | A3500 del 3er día hábil antes del vencimiento | `expectativas.py` |

## Pendientes (para una versión futura; se deciden juntos)

- Estacionalidad dentro de cada bloque (Banco Central de Brasil, WP 493, 2022).
- Valuar TY30P, TX28 y TX31 flujo por flujo dentro del método (hoy solo el tramo largo indicativo).
- Curva en dólares separada por ley y antes/después de diciembre 2027 (AO27 vs AO28: riesgo electoral).
- Backtest: leer las resoluciones de bonos ya vencidos (hoy hay una sola ventana: no permite concluir).
- Que un bono no entre y salga por un solo día de poca liquidez (por ejemplo, excluir solo si falla 2 días seguidos).
- Duales CER/TAMAR y TAMAR/dólar linked (requieren modelo de opciones).

## Registro de cambios

- **1.0 (13/11/2026)**: primera versión congelada. Inflación por bootstrapping (bloques + mes a mes con
  interpolación de la curva acumulada), TAMAR implícita, dólar implícito, publicación solo con precios de cierre.
  Antes de esta versión el método cambió varias veces el mismo día (pares → curva suave → por tramo → bloques);
  los números anteriores al 13/11/2026 no son comparables.
