import json
import unittest
from datetime import date
from pathlib import Path

import calculos as c

FAM = json.loads((Path(__file__).parent / "bonos.json").read_text(encoding="utf-8"))["familias"]


class TestFlujos(unittest.TestCase):
    def test_amortiza_100(self):
        for k, f in FAM.items():
            self.assertAlmostEqual(sum(x.amortizacion for x in c.flujos(f)), 100, places=9, msg=k)

    def test_gd30_cuotas(self):
        am = c.cronograma_amortizacion(FAM["2030"]["amortizacion"])
        self.assertEqual(am[date(2024, 7, 9)], 4.0)
        self.assertAlmostEqual(am[date(2030, 7, 9)], 8.0)
        self.assertEqual(len(am), 13)

    def test_gd30_cupon_enero_2027(self):
        # residual 64 (pagó 4 + 4x8), tasa 0,75% semestral -> 64 * 0,375% = 0,24
        f = next(x for x in c.flujos(FAM["2030"]) if x.fecha == date(2027, 1, 9))
        self.assertAlmostEqual(f.residual_antes, 64.0)
        self.assertAlmostEqual(f.interes, 0.24)

    def test_primer_cupon_periodo_corto(self):
        # 4/9/2020 a 9/7/2021 = 305 días 30/360
        f = c.flujos(FAM["2030"])[0]
        self.assertAlmostEqual(f.interes, 100 * 0.00125 * 305 / 360)

    def test_step_up_gd35(self):
        fl = {x.fecha: x for x in c.flujos(FAM["2035"])}
        self.assertAlmostEqual(fl[date(2028, 1, 9)].interes, 4.75 / 2)
        self.assertAlmostEqual(fl[date(2029, 1, 9)].interes, 5.0 / 2)

    def test_tir_ida_y_vuelta(self):
        liq = date(2026, 11, 11)
        for k, f in FAM.items():
            fut = [x for x in c.flujos(f) if x.fecha_pago > liq]
            t = [(x.fecha_pago - liq).days / 365 for x in fut]
            precio = c._vp([x.total for x in fut], t, 0.10)
            r = c.valuar(f, precio, liq)
            self.assertAlmostEqual(r.tir, 0.10, places=7, msg=k)

    def test_dia_habil(self):
        self.assertEqual(c.dia_habil(date(2027, 1, 9)), date(2027, 1, 11))  # sábado



class TestFeriados(unittest.TestCase):
    def test_pago_en_feriado_pasa_al_habil(self):
        import feriados
        feriados.FERIADOS.add(date(2030, 7, 9))  # 9 de julio siempre es feriado
        self.assertEqual(c.dia_habil(date(2030, 7, 9)), date(2030, 7, 10))

    def test_liquidacion_saltea_feriado(self):
        import feriados
        feriados.FERIADOS.add(date(2026, 11, 23))
        self.assertEqual(c.liquidacion_t1(date(2026, 11, 20)), date(2026, 11, 24))  # viernes -> martes


class TestBoncer(unittest.TestCase):
    def test_tx28_residual_y_cuotas(self):
        import instrumentos as I
        t = json.loads((Path(__file__).parent / "datos" / "titulos_manuales.json").read_text(encoding="utf-8"))["titulos"][0]
        fl = dict(I.flujos_cupon(t, 2.25))
        # 9/11/2026: cuota 10 + interés semestral sobre residual 50 (ya pagó 5 cuotas) = 10 + 50*2,25%/2
        self.assertAlmostEqual(fl[date(2026, 11, 9)], 10 + 50 * 0.0225 / 2)
        self.assertAlmostEqual(sum(a["porcentaje"] for a in t["amortizacion"]), 100)
        self.assertEqual(max(fl), date(2028, 11, 9))


class TestNelsonSiegel(unittest.TestCase):
    def test_recupera_una_curva_conocida(self):
        # se inventa una curva NS, se leen 8 "bonos" sobre ella y el ajuste tiene que volver a pasar por ellos
        import nelson_siegel as N
        real = {"b0": 0.20, "b1": 0.06, "b2": -0.04, "tau": 0.8}
        pts = [(t, N.tir_en(real, t)) for t in (0.05, 0.15, 0.3, 0.5, 0.8, 1.1, 1.5, 2.0)]
        c = N.ajustar(pts)
        self.assertLess(c["error_medio_pb"], 1.0)
        for t, y in pts:
            self.assertAlmostEqual(N.tir_en(c, t), y, places=2)

    def test_bloques_exactos_y_sin_separar_lo_que_no_se_puede(self):
        # inflación inventada mes a mes; bonos CER "perfectos" que vencen el 15/1, 15/3 y 15/4.
        # Nov sale solo; dic y ene NO se pueden separar (ningún bono vence en el medio): un bloque con el
        # promedio geométrico de los dos; feb solo.
        import math
        from datetime import timedelta
        import nelson_siegel as N
        pub = date(2026, 12, 15)
        verdad = {"2026-11": 1.8, "2026-12": 2.4, "2027-01": 1.6, "2027-02": 1.9}
        def suba(hasta):
            g = 1.0
            for mes, pi in verdad.items():
                d0, d1 = N.ventana_mes(mes)
                dentro = (min(d1, hasta) - max(d0, pub)).days
                if dentro > 0:
                    g *= (1 + pi / 100) ** (dentro / (d1 - d0).days)
            return g
        fines = [date(2027, 1, 15), date(2027, 3, 15), date(2027, 4, 15)]
        pts = [{"ticker": f"B{i}", "cer_hasta": f, "suba_futura": suba(f), "spread": 0.1, "tasa_fija_de": "curva"}
               for i, f in enumerate(fines)]
        bl = N.bloques(pts, {pub: 1.0, pub - timedelta(days=40): 0.98})
        self.assertEqual([b["nombre"] for b in bl], ["nov-26", "dic-26 a ene-27", "feb-27"])
        self.assertAlmostEqual(bl[0]["mercado"], 1.8, places=9)
        self.assertAlmostEqual(bl[1]["mercado"], (math.sqrt(1.024 * 1.016) - 1) * 100, places=9)
        self.assertAlmostEqual(bl[2]["mercado"], 1.9, places=9)
        # banda = media punta de cada bono que delimita el bloque
        self.assertTrue(bl[1]["banda_min"] < bl[1]["mercado"] < bl[1]["banda_max"])

    def test_ventana_del_mes_regla_bcra(self):
        # la inflación de noviembre sube el CER del 16/12 al 15/1 (ventana 15/12 exclusive → 15/1 inclusive)
        import nelson_siegel as N
        self.assertEqual(N.ventana_mes("2026-11"), (date(2026, 12, 15), date(2027, 1, 15)))
        self.assertEqual(N.ventana_mes("2026-12"), (date(2027, 1, 15), date(2027, 2, 15)))

    def test_inflacion_futura_saca_lo_ya_publicado(self):
        # tasa fija plana 30%, un CER cero cupón a 1 año que paga real 5%, CER ya publicado +3%:
        # suba total = 1,30/1,05; la futura tiene que ser eso dividido 1,03
        import math
        from datetime import timedelta
        import nelson_siegel as N
        import instrumentos as I
        liq = date(2026, 11, 13)
        a = I.restar_habiles(liq, 10)
        pub = a + timedelta(days=46)
        cer = {a: 100.0, pub: 103.0}
        fija = {"b0": math.log(1.30), "b1": 0.0, "b2": 0.0, "tau": 1.0, "plazo_max": 2.0}
        b = I.restar_habiles(liq + timedelta(days=365), 10)
        pts = N.inflacion_futura(fija, [{"ticker": "X", "plazo": 1.0, "tir": 5.0, "spread": 0.1, "cer_hasta": b}], liq, cer)
        self.assertAlmostEqual(pts[0]["suba_futura"], 1.30 / 1.05 / 1.03, places=12)
        self.assertEqual(pts[0]["dias_futuros"], (b - pub).days)

    def test_ponderar_por_duration_ignora_el_ruido_corto(self):
        # una letra de 2 semanas con 3 puntos de error de tasa casi no debe mover una curva de 7 bonos
        import nelson_siegel as N
        verdad = {"b0": 0.25, "b1": 0.02, "b2": 0.0, "tau": 1.0}
        pts = [(t, N.tir_en(verdad, t), t) for t in (0.3, 0.5, 0.8, 1.1, 1.5, 2.0)]
        pts.append((0.04, N.tir_en(verdad, 0.04) + 3.0, 0.04))
        c = N.ajustar(pts)
        self.assertAlmostEqual(N.tir_en(c, 1.0), N.tir_en(verdad, 1.0), delta=0.05)

    def test_bonte_flujo_por_flujo_en_curva_plana(self):
        # curva plana al 25% continuo hasta 0,6 años; un bono con cupones hasta 3 años valuado a esa misma tasa
        # tiene que devolver una forward de 25% (si no, la valuación flujo por flujo está mal)
        import math
        import nelson_siegel as N
        fija = {"b0": 0.25, "b1": 0.0, "b2": 0.0, "tau": 1.0, "plazo_max": 0.6}
        flujos = [(0.5 * k, 14.75) for k in range(1, 6)] + [(3.0, 114.75)]
        precio = sum(c * math.exp(-0.25 * t) for t, c in flujos)
        self.assertAlmostEqual(N._forward_que_valua(fija, flujos, precio), 0.25, places=9)

    def test_rem_reparte_lo_que_falta_sin_pasarse(self):
        # 12 meses = 21,8%; los meses explícitos ya se llevan una parte: los otros se reparten SOLO lo que falta
        import math, json, glob
        import expectativas as X
        r = json.load(open(sorted(glob.glob("datos/rem/*.json"))[-1], encoding="utf-8"))["total"]
        h = r["ipc"]["12m"]["hasta"]
        meses = ["2026-11", "2026-12"] + [f"2027-{k:02d}" for k in range(1, 11)]
        if h == "oct-27":
            tot = math.prod(1 + X.inflacion_rem(r, m) / 100 for m in meses)
            self.assertAlmostEqual((tot - 1) * 100, r["ipc"]["12m"]["valor"], places=9)

    def test_no_usa_cer_mas_largos_que_la_curva_fija(self):
        # sin tasa fija a ese plazo no hay contra qué comparar: el bono CER queda fuera (no se extrapola)
        import nelson_siegel as N
        fija = {"b0": 0.2, "b1": 0.0, "b2": 0.0, "tau": 1.0, "plazo_max": 0.5}
        cer = {date(2026, 10, 29): 100.0, date(2026, 12, 15): 103.0}
        p = {"ticker": "LARGO", "plazo": 1.5, "tir": 5.0, "spread": 0.1, "cer_hasta": date(2028, 4, 1)}
        self.assertEqual(N.inflacion_futura(fija, [p], date(2026, 11, 13), cer), [])


if __name__ == "__main__":
    unittest.main()
