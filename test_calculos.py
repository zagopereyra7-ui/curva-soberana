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

    def test_curvas_planas_dan_la_inflacion_exacta(self):
        # tasa fija 30% y CER 5% en todos los plazos -> inflación (1,30/1,05)^(1/12)-1 en cada mes
        import nelson_siegel as N
        plano = lambda y: {"b0": __import__("math").log1p(y), "b1": 0.0, "b2": 0.0, "tau": 1.0, "plazo_max": 2.0}
        meses = N.inflacion_mensual(plano(0.30), plano(0.05), date(2026, 11, 13), date(2026, 12, 15), meses=6)
        self.assertEqual(meses[0]["mes"], "2026-11")          # octubre ya está en el CER publicado
        for m in meses:
            dias = (date.fromisoformat(m["cer_hasta"]) - date.fromisoformat(m["cer_desde"])).days
            esperado = ((1.30 / 1.05) ** (dias / 365) - 1) * 100
            self.assertAlmostEqual(m["mercado"], esperado, places=9)

    def test_no_extrapola_mas_alla_del_ultimo_bono(self):
        import nelson_siegel as N
        plano = {"b0": 0.2, "b1": 0.0, "b2": 0.0, "tau": 1.0, "plazo_max": 0.5}
        meses = N.inflacion_mensual(plano, dict(plano, b0=0.02), date(2026, 11, 13), date(2026, 12, 15))
        self.assertTrue(meses and all(m["t1"] <= 0.5 for m in meses))


if __name__ == "__main__":
    unittest.main()
