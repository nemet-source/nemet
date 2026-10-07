#!/usr/bin/env python3
"""Pruebas del cotizador NEMET: dosificación de pigmentos contra rendimiento por espesor.

Fijan el error que infló la cotización COT-2026-003 —16 m² de pasta cotizados como
16 kg y 16 L de tinta como 16 L, cuando la receta pide **160 g** y **~1 L**— y protegen
los cálculos que ya eran correctos (EPOXY PRIMER 0.3333 y EPOXY PISOS 1.2 kg/m²·mm).

No hace falta Streamlit: prueba `cotizador_calculo` a fondo y valida los datos reales
del Excel (`Cat_Productos`, `Inventario` y `Escalas_Precios_KG`).

Uso:  python tests_cotizador.py     →  "N/N OK" si todo pasa.
"""
import os
import sys
import unittest

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import cotizador_calculo as cc  # noqa: E402

EXCEL = os.path.join(BASE, "Sistema_Inventario_NEMET_Final.xlsx")

AREA = 16.0      # m² del caso COT-2026-003
ESPESOR = 1.0    # mm

PASTA = "EPOXY PASTA COLORES SÓLIDOS"
TINTA = "EPOXY TINTA TRASLÚCIDOS LÍQUIDOS"   # familia del inventario
TINTA_CATALOGO = "EPOXY TINTA"               # nombre en Cat_Productos
METAL = "EPOXY METAL PERLADO Y METÁLICOS EN POLVO"
FOTOLUMINISCENTES = "PIGMENTOS FOTOLUMINISCENTES"
PRIMER = "EPOXY PRIMER (A Y B) TRANSPARENTE"
PISOS = "EPOXY PISOS (A Y B) TRANSPARENTE"


# ============================================================ lectura de datos
def leer_catalogo():
    return pd.read_excel(EXCEL, sheet_name="Cat_Productos")


def leer_inventario():
    df = pd.read_excel(EXCEL, sheet_name="Inventario", header=1)
    return df.loc[:, ~df.columns.duplicated()].dropna(subset=["SKU"])


def leer_escalas():
    return pd.read_excel(EXCEL, sheet_name="Escalas_Precios_KG")


def catalogo_efectivo(df_catalogo):
    """Réplica mínima de `cargar_catalogo_rendimientos`: pigmentos -> dosis, resinas -> rendimiento."""
    catalogo = {}
    for _, fila in df_catalogo.iterrows():
        nombre = cc.normalizar_clave(fila.get("Producto"))
        if not nombre or nombre in catalogo:
            continue
        rendimiento = pd.to_numeric(fila.get("Rendimiento"), errors="coerce")
        dosis = pd.to_numeric(fila.get("Dosis_g_m2"), errors="coerce")
        if cc.es_familia_dosificada(nombre):
            rendimiento = float("nan")   # un pigmento jamás se calcula por espesor
        catalogo[nombre] = {
            "rendimiento": float(rendimiento) if pd.notna(rendimiento) and rendimiento > 0 else None,
            "prop_a": 100.0,
            "prop_b": 0.0,
            "dosis_g_m2": float(dosis) if pd.notna(dosis) and dosis > 0 else None,
        }
    return catalogo


def info_de(catalogo, nombre):
    """Mismo emparejamiento que la app: nombre exacto normalizado o por prefijo."""
    clave = cc.normalizar_clave(nombre)
    if clave in catalogo:
        return catalogo[clave]
    candidatos = [k for k in catalogo if clave.startswith(k) or k.startswith(clave)]
    return catalogo[max(candidatos, key=len)] if candidatos else None


# ============================================================ catálogo
class TestDatosCatalogo(unittest.TestCase):
    """La hoja `Cat_Productos` ya no puede confundir un pigmento con una resina."""

    @classmethod
    def setUpClass(cls):
        cls.df = leer_catalogo()
        cls.pigmentos = cls.df[cls.df["Dosis_g_m2"].notna()]

    def test_01_los_pigmentos_declaran_su_dosis_en_g_m2(self):
        familias = {cc.normalizar_clave(n) for n in self.pigmentos["Producto"]}
        for esperada in (PASTA, TINTA_CATALOGO, METAL, FOTOLUMINISCENTES):
            self.assertIn(cc.normalizar_clave(esperada), familias)
        for _, fila in self.pigmentos.iterrows():
            self.assertGreater(float(fila["Dosis_g_m2"]), 0,
                               f"{fila['Producto']}: la dosis debe ser mayor que cero")

    def test_02_las_dosis_son_de_pigmento_y_no_de_resina(self):
        for _, fila in self.pigmentos.iterrows():
            dosis = float(fila["Dosis_g_m2"])
            self.assertGreaterEqual(dosis, 1.0, f"{fila['Producto']}: dosis sospechosamente baja")
            self.assertLessEqual(dosis, 100.0,
                                 f"{fila['Producto']}: {dosis} g/m² no es una dosis de pigmento; "
                                 "revisa que no se haya colado un rendimiento de resina (kg/m²)")

    def test_03_ningun_pigmento_conserva_rendimiento_por_mm(self):
        for _, fila in self.pigmentos.iterrows():
            self.assertTrue(pd.isna(fila["Rendimiento"]),
                            f"{fila['Producto']}: deja vacío «Rendimiento» (kg/m²·mm); su dosis va en Dosis_g_m2")

    def test_04_los_rendimientos_de_resina_siguen_intactos(self):
        esperados = {PRIMER: 0.3333, PISOS: 1.2}
        for nombre, rendimiento in esperados.items():
            valor = float(self.df[self.df["Producto"] == nombre]["Rendimiento"].iloc[0])
            self.assertAlmostEqual(valor, rendimiento, places=4,
                                   msg=f"{nombre} debe conservar su rendimiento del manual")

    def test_05_la_dosis_manda_sobre_cualquier_rendimiento_heredado(self):
        """Con la hoja vieja (Rendimiento = 1 kg/m²·mm y sin dosis) igual se dosifica por área."""
        catalogo = catalogo_efectivo(self.df)
        info_vieja = dict(catalogo[cc.normalizar_clave(TINTA_CATALOGO)])
        info_vieja["dosis_g_m2"] = None      # simula el catálogo sin la columna nueva
        info_vieja["rendimiento"] = 1.0      # el valor que provocó el error
        resultado = cc.calcular_material(AREA, ESPESOR, info_vieja, TINTA)
        self.assertTrue(resultado["es_dosificacion"])
        self.assertAlmostEqual(resultado["gramos"], cc.DOSIS_PIGMENTO_DEFAULT_G_M2 * AREA, places=6)
        self.assertIsNone(resultado["rendimiento"])


# ============================================================ cálculo de material
class TestCalculoMaterial(unittest.TestCase):

    def test_06_pasta_16m2_son_160_gramos(self):
        info = {"dosis_g_m2": 10.0, "rendimiento": None}
        resultado = cc.calcular_material(AREA, ESPESOR, info, PASTA)
        self.assertEqual(resultado["modalidad"], "dosificacion")
        self.assertAlmostEqual(resultado["gramos"], 160.0, places=6)
        self.assertAlmostEqual(resultado["kg_necesarios"], 0.16, places=6)

    def test_07_el_espesor_no_cambia_el_pigmento(self):
        info = {"dosis_g_m2": 10.0}
        fino = cc.calcular_material(AREA, 0.5, info, PASTA)
        grueso = cc.calcular_material(AREA, 5.0, info, PASTA)
        self.assertAlmostEqual(fino["gramos"], grueso["gramos"], places=9)

    def test_08_tinta_16m2_cabe_en_un_litro(self):
        info = {"dosis_g_m2": 60.0}
        resultado = cc.calcular_material(AREA, ESPESOR, info, TINTA)
        self.assertLessEqual(resultado["gramos"], 1000.0)
        self.assertGreater(resultado["gramos"], 0.0)

    def test_09_metal_8g_por_m2(self):
        info = {"dosis_g_m2": 8.0}
        self.assertAlmostEqual(cc.calcular_material(AREA, ESPESOR, info, METAL)["gramos"], 128.0, places=6)

    def test_10_primer_y_pisos_siguen_por_espesor(self):
        primer = cc.calcular_material(AREA, ESPESOR, {"rendimiento": 0.3333}, PRIMER)
        pisos = cc.calcular_material(AREA, ESPESOR, {"rendimiento": 1.2}, PISOS)
        self.assertAlmostEqual(primer["kg_necesarios"], 16 * 0.3333, places=6)
        self.assertAlmostEqual(pisos["kg_necesarios"], 19.2, places=6)
        self.assertFalse(primer["es_dosificacion"])
        self.assertFalse(pisos["es_dosificacion"])

    def test_11_un_pigmento_sin_dosis_usa_el_respaldo_y_no_una_resina(self):
        resultado = cc.calcular_material(AREA, ESPESOR, {"rendimiento": 1.0}, "EPOXY PASTA NUEVA")
        self.assertTrue(resultado["es_dosificacion"])
        self.assertTrue(resultado["dosis_por_defecto"])
        self.assertAlmostEqual(resultado["gramos"], 160.0, places=6)

    def test_12_la_dosis_capturada_en_pantalla_gana(self):
        info = {"dosis_g_m2": 10.0}
        resultado = cc.calcular_material(AREA, ESPESOR, info, PASTA, dosis_manual_g_m2=25.0)
        self.assertAlmostEqual(resultado["dosis_g_m2"], 25.0, places=6)
        self.assertAlmostEqual(resultado["gramos"], 400.0, places=6)

    def test_13_un_producto_desconocido_cae_al_rendimiento_por_defecto(self):
        resultado = cc.calcular_material(AREA, ESPESOR, {}, "SISTEMA SIN CATALOGO")
        self.assertFalse(resultado["es_dosificacion"])
        self.assertAlmostEqual(resultado["kg_necesarios"], AREA * ESPESOR * cc.RENDIMIENTO_DEFAULT_KG_M2_MM, places=6)

    def test_14_unidades_para_cubrir_redondea_hacia_arriba(self):
        self.assertEqual(cc.unidades_para_cubrir(0.16, 0.1), 2)     # 2 × 100 g
        self.assertEqual(cc.unidades_para_cubrir(0.16, 0.25), 1)
        self.assertEqual(cc.unidades_para_cubrir(0.16, 1.0), 1)
        self.assertEqual(cc.unidades_para_cubrir(19.2, 20.0), 1)
        self.assertEqual(cc.unidades_para_cubrir(0.0, 1.0), 0)

    def test_15_la_presentacion_mas_economica_gana_y_desempata_por_unidades(self):
        opciones = [
            {"presentacion": "60 g", "precio": 225.0, "unidades": 3},
            {"presentacion": "100 g", "precio": 224.0, "unidades": 2},
            {"presentacion": "1 kg", "precio": 840.0, "unidades": 1},
            {"presentacion": "1 kg", "precio": 224.0, "unidades": 4},
        ]
        mejor = cc.elegir_opcion_mas_economica(opciones, "precio", "unidades")
        self.assertEqual(mejor["unidades"], 2)
        self.assertEqual(mejor["precio"], 224.0)


# ============================================================ cotización de 16 m²
class TestCotizacion16m2(unittest.TestCase):
    """El caso COT-2026-003 completo, con los precios reales del inventario."""

    @classmethod
    def setUpClass(cls):
        cls.catalogo = catalogo_efectivo(leer_catalogo())
        cls.inv = leer_inventario()
        cls.escalas = leer_escalas()

    def material(self, nombre, espesor=ESPESOR):
        return cc.calcular_material(AREA, espesor, info_de(self.catalogo, nombre) or {}, nombre)

    def cubetas(self, familia, kg):
        filas = self.inv[self.inv["Descripcion"].astype(str).str.strip() == familia]
        opciones = []
        for _, fila in filas.iterrows():
            kg_por_unidad = cc.parsear_presentacion_kg(fila["Presentacion"])
            if not kg_por_unidad:
                continue
            unidades = cc.unidades_para_cubrir(kg, kg_por_unidad)
            opciones.append({
                "sku": fila["SKU"],
                "presentacion": str(fila["Presentacion"]),
                "kg_por_unidad": kg_por_unidad,
                "unidades": unidades,
                "kg_cubiertos": unidades * kg_por_unidad,
                "precio": unidades * float(fila["PrecioPublicoIVA"]),
            })
        return cc.elegir_opcion_mas_economica(opciones, "precio", "unidades")

    def granel(self, proveedor, kg):
        filas = self.escalas[self.escalas["Producto_Proveedor"] == proveedor]
        for _, fila in filas.iterrows():
            hasta = fila["Rango_Hasta_Kg"]
            if kg >= float(fila["Rango_Desde_Kg"]) and (pd.isna(hasta) or kg <= float(hasta)):
                return kg * float(fila["Precio_Publico_por_Kg"])
        return None

    def test_16_la_pasta_se_cotiza_en_gramos_y_no_en_kilos(self):
        pasta = self.material(PASTA)
        self.assertAlmostEqual(pasta["gramos"], 160.0, places=6)
        mejor = self.cubetas(PASTA, pasta["kg_necesarios"])
        self.assertLessEqual(mejor["kg_cubiertos"], 0.3)                 # nunca 16 kg
        self.assertLessEqual(mejor["precio"], 270.0)                     # ni la mitad de 1 kg ($840)
        self.assertIn("100 g", [mejor["presentacion"]])                  # 2 × 100 g = $224

    def test_17_la_tinta_se_cotiza_en_un_litro(self):
        tinta = self.material(TINTA_CATALOGO)
        mejor = self.cubetas(TINTA, tinta["kg_necesarios"])
        self.assertEqual(mejor["unidades"], 1)
        self.assertEqual(mejor["presentacion"], "1 L")
        self.assertEqual(mejor["precio"], 550.0)                         # y no 16 L = $8,800

    def test_18_los_metalizados_son_gramos_no_kilos(self):
        metal = self.material(METAL)
        self.assertAlmostEqual(metal["gramos"], 128.0, places=6)
        self.assertLess(metal["kg_necesarios"], 0.2)

    def test_19_primer_y_pisos_cubren_el_area_como_en_el_manual(self):
        primer = self.material(PRIMER)
        pisos = self.material(PISOS)
        self.assertAlmostEqual(primer["kg_necesarios"], 5.3328, places=4)
        self.assertAlmostEqual(pisos["kg_necesarios"], 19.2, places=6)
        mejor_pisos = self.cubetas(PISOS, pisos["kg_necesarios"])
        self.assertEqual(mejor_pisos["presentacion"], "20 kg")           # cubre los 19.2 kg
        self.assertEqual(mejor_pisos["precio"], 8000.0)

    def test_20_el_total_corregido_baja_mas_de_20_mil_pesos(self):
        primer = self.material(PRIMER)
        pisos = self.material(PISOS)
        pasta = self.material(PASTA)
        tinta = self.material(TINTA_CATALOGO)

        opciones_primer = [x for x in (self.granel("EPOXY PRIMER", primer["kg_necesarios"]),
                                       self.cubetas(PRIMER, primer["kg_necesarios"])["precio"]) if x]
        opciones_pisos = [x for x in (self.granel("EPOXY PISOS", pisos["kg_necesarios"]),
                                      self.cubetas(PISOS, pisos["kg_necesarios"])["precio"]) if x]
        total = (min(opciones_primer) + min(opciones_pisos)
                 + self.cubetas(PASTA, pasta["kg_necesarios"])["precio"]
                 + self.cubetas(TINTA, tinta["kg_necesarios"])["precio"])
        self.assertLess(total, 11000.0)

        # Lo que se cotizó con el error: 16 kg de pasta y 16 L de tinta.
        inflado = (min(opciones_primer) + min(opciones_pisos)
                   + 16 * 840.0      # 16 × 1 kg de pasta
                   + 16 * 550.0)     # 16 × 1 L de tinta
        self.assertAlmostEqual(inflado - total, 22240.0 - 774.0, places=2)
        self.assertGreaterEqual(inflado - total, 20000.0,
                                "la corrección debe devolver más de $20,000 al cliente")


if __name__ == "__main__":
    resultado = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    n = resultado.testsRun
    if resultado.wasSuccessful() and n == 20:
        print(f"\n{n}/{n} OK — cotizador verificado: pigmentos en g/m², resinas por espesor")
        sys.exit(0)
    print(f"\n{n - len(resultado.failures) - len(resultado.errors)}/{n} (esperadas 20/20)")
    sys.exit(1)
