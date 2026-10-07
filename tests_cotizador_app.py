#!/usr/bin/env python3
"""Pruebas de extremo a extremo del cotizador por área (la app real, con `AppTest`).

Reproducen la cotización COT-2026-003 (16 m², 1 mm) manejando la interfaz como lo haría
una persona: elegir la línea de producto, capturar el área y agregar el renglón al carrito.
Así se comprueba lo mismo que ve el usuario, no solo la aritmética:

* Las **pastas** y **tintas** se dosifican en g/m² (espesor deshabilitado): 16 m² de pasta
  son 160 g (dos bolsas de 100 g = $224) y la tinta cabe en 1 L ($550). Antes el sistema
  cotizaba 16 kg y 16 L ($22,240).
* Las **resinas** siguen por espesor: PRIMER 5.33 kg y PISOS 19.2 kg (una cubeta de 20 kg).
* Las medidas inician en cero; con área cero o espesor cero para resinas no aparece una cotización.

Corre sobre una **copia temporal** del proyecto (Excel y base de usuarios propios), así que
nunca toca los datos reales.  Uso:  python tests_cotizador_app.py  →  "9/9 OK".
"""
import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import auth_nemet as auth  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # sin Streamlit instalado, la batería no aplica
    AppTest = None

PASSWORD_SEMILLA = "ClaveAdminInicial2026"
PASSWORD_ADMIN = "ClaveAdminDefinitiva2026"

MENU_COTIZADOR = "📏 Cotizador por Área y Milimétrico"
PASTA = "EPOXY PASTA COLORES SÓLIDOS"
TINTA = "EPOXY TINTA TRASLÚCIDOS LÍQUIDOS"
PISOS = "EPOXY PISOS (A Y B) TRANSPARENTE"
PRIMER = "EPOXY PRIMER (A Y B) TRANSPARENTE"
AREA_LADO = 4.0     # 4 m × 4 m = 16 m²


@unittest.skipIf(AppTest is None, "Streamlit no está instalado")
class TestCotizadorPorArea(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app_dir = os.path.join(cls.tmp.name, "nemet")
        os.makedirs(os.path.join(cls.app_dir, "assets"), exist_ok=True)
        for nombre in ("app.py", "auth_nemet.py", "prospeccion.py", "cotizador_calculo.py",
                       "Sistema_Inventario_NEMET_Final.xlsx"):
            shutil.copy(os.path.join(BASE, nombre), os.path.join(cls.app_dir, nombre))
        for nombre in os.listdir(os.path.join(BASE, "assets")):
            shutil.copy(os.path.join(BASE, "assets", nombre), os.path.join(cls.app_dir, "assets", nombre))

        cls.ruta_db = os.path.join(cls.tmp.name, "usuarios.db")
        os.environ["NEMET_DB_USUARIOS"] = cls.ruta_db
        os.environ["NEMET_ADMIN_USUARIO"] = "jefe"
        os.environ["NEMET_ADMIN_PASSWORD"] = PASSWORD_SEMILLA
        os.environ["NEMET_ADMIN_NOMBRE"] = "Jefe de Planta"

        conn = auth.conectar(cls.ruta_db)
        auth.inicializar_db(conn)
        auth.sembrar_admin_inicial(conn, "jefe", PASSWORD_SEMILLA, nombre="Jefe de Planta")
        admin = auth.obtener_por_login(conn, "jefe")
        auth.cambiar_password_propia(conn, admin["id"], PASSWORD_SEMILLA, PASSWORD_ADMIN)
        conn.close()

        cls.at = AppTest.from_file(os.path.join(cls.app_dir, "app.py"), default_timeout=120)
        cls.at.run()
        cls.at.text_input(key="acceso_usuario").set_value("jefe")
        cls.at.text_input(key="acceso_password").set_value(PASSWORD_ADMIN)
        cls.at.button(key="acceso_entrar").click()
        cls.at.run()

    @classmethod
    def tearDownClass(cls):
        for variable in ("NEMET_DB_USUARIOS", "NEMET_ADMIN_USUARIO", "NEMET_ADMIN_PASSWORD",
                         "NEMET_ADMIN_NOMBRE"):
            os.environ.pop(variable, None)
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ utilidades
    def ir_al_cotizador(self):
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        self.at.sidebar.selectbox[0].select(MENU_COTIZADOR)
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])

    def cotizar(self, familia, ancho=AREA_LADO, largo=AREA_LADO, espesor=None):
        self.at.selectbox(key="area_prod").select(familia)
        self.at.number_input(key="area_ancho").set_value(ancho)
        self.at.number_input(key="area_largo").set_value(largo)
        self.at.run()
        if espesor is not None:
            # Se captura después de cambiar de producto: el espesor está deshabilitado
            # mientras el producto seleccionado sea un pigmento.
            self.at.number_input(key="area_espesor").set_value(espesor)
            self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])

    def texto_infos(self):
        return " ".join(str(i.value) for i in self.at.info)

    def widget_dosis(self):
        """El campo «Dosificación del pigmento (g/m²)», sin depender de su clave interna."""
        campo = [n for n in self.at.number_input if n.label.startswith("Dosificación")]
        self.assertEqual(len(campo), 1, "debe haber un solo campo de dosificación")
        return campo[0]

    def assert_sin_cotizacion(self):
        """No hay cálculo, comparativa, recomendación ni botones de agregar al faltar medidas."""
        textos_info = " ".join(str(i.value) for i in self.at.info)
        self.assertNotIn("Material necesario", textos_info)
        self.assertEqual(len(self.at.metric), 0, "no deben mostrarse métricas de la cotización")
        markdown = " ".join(str(m.value) for m in self.at.markdown)
        self.assertNotIn("Comparativa:", markdown)
        self.assertFalse(any("Recomendación" in str(s.value) for s in self.at.success))
        botones_agregar = [b for b in self.at.button
                           if b.key in {"add_kilo", "add_cubeta", "add_pz"} or "Agregar" in str(b.label)]
        self.assertEqual(botones_agregar, [], "no debe haber botones para agregar al carrito")

    # ------------------------------------------------------------------ flujo
    def test_00_las_medidas_inician_en_cero_y_el_pigmento_conserva_su_dosis(self):
        self.ir_al_cotizador()
        self.at.selectbox(key="area_prod").select(PASTA)
        self.at.run()

        self.assertEqual(self.at.number_input(key="area_ancho").value, 0.0)
        self.assertEqual(self.at.number_input(key="area_largo").value, 0.0)
        self.assertEqual(self.at.number_input(key="area_espesor").value, 0.0)
        self.assertEqual(self.widget_dosis().value, 10.0,
                         "la dosis debe seguir precargada desde el catálogo")
        self.assertTrue(self.at.number_input(key="area_espesor").disabled,
                        "el espesor no aplica a un pigmento")
        self.assertIn("Captura", " ".join(str(w.value) for w in self.at.warning))
        self.assert_sin_cotizacion()

    def test_01_la_pasta_de_16m2_son_160_gramos_sin_espesor(self):
        self.ir_al_cotizador()
        self.cotizar(PASTA)
        info = self.texto_infos()
        self.assertIn("Dosificación:** 10 g/m²", info)
        self.assertIn("160 g", info)
        self.assertIn("no depende del espesor", info)
        self.assertNotIn("kg/m² por mm", info)
        self.assertTrue(self.at.number_input(key="area_espesor").disabled,
                        "el espesor no aplica a un pigmento")
        self.assertEqual(self.widget_dosis().value, 10.0)

    def test_02_la_pasta_mas_economica_son_dos_bolsas_de_100g(self):
        self.assertIn("100 g × 2", [m.value for m in self.at.metric])
        self.assertTrue(all(b.disabled for b in self.at.button if b.key is None and "Kilo Exacto" in b.label),
                        "los pigmentos no se cotizan a granel por kg")

    def test_03_la_pasta_entra_al_carrito_con_224_pesos_y_sin_mm(self):
        self.at.button(key="add_cubeta").click()
        self.at.run()
        carrito = self.at.session_state["carrito_area"]
        self.assertEqual(len(carrito), 1)
        fila = carrito.iloc[0]
        self.assertEqual(fila["SKU"], "EP 02")           # bolsa de 100 g
        self.assertEqual(int(fila["Cantidad"]), 2)
        self.assertEqual(float(fila["Subtotal"]), 224.0)
        self.assertEqual(float(fila["Espesor_mm"]), 0.0)
        self.assertEqual(float(fila["Kg_Necesarios"]), 0.16)
        self.assertIn("10 g/m²", fila["Detalle"])
        self.assertIn("160 g", fila["Detalle"])

    def test_04_la_tinta_cabe_en_un_litro_de_550(self):
        self.cotizar(TINTA)
        self.assertEqual(self.widget_dosis().value, 60.0,
                         "al cambiar de pigmento el campo debe traer la dosis del catálogo, no la del anterior")
        self.assertIn("960 g", self.texto_infos())
        self.assertIn("1 L × 1", [m.value for m in self.at.metric])
        self.at.button(key="add_cubeta").click()
        self.at.run()
        carrito = self.at.session_state["carrito_area"]
        tinta = carrito[carrito["Descripcion"] == TINTA].iloc[0]
        self.assertEqual(tinta["SKU"], "ET 06")           # 1 L
        self.assertEqual(float(tinta["Subtotal"]), 550.0)

    def test_05_los_pigmentos_del_area_no_llegan_a_mil_pesos(self):
        carrito = self.at.session_state["carrito_area"]
        pigmentos = carrito[carrito["Descripcion"].isin([PASTA, TINTA])]
        self.assertEqual(len(pigmentos), 2)
        subtotal = float(pigmentos["Subtotal"].sum())
        self.assertAlmostEqual(subtotal, 224.0 + 550.0, places=2)
        self.assertLess(subtotal, 1000.0,
                        "con el error anterior estos dos renglones costaban $22,240")

    def test_06_los_pisos_siguen_calculandose_por_espesor(self):
        self.cotizar(PISOS, ancho=4.0, largo=4.0, espesor=1.0)
        self.assertFalse(self.at.number_input(key="area_espesor").disabled)
        info = self.texto_infos()
        self.assertIn("kg/m² por mm", info)
        self.assertIn("19.20 kg", info)
        self.assertIn("20 kg × 1", [m.value for m in self.at.metric])

    def test_07_el_primer_conserva_su_rendimiento_del_manual(self):
        self.cotizar(PRIMER, ancho=4.0, largo=4.0, espesor=1.0)
        info = self.texto_infos()
        self.assertIn("5.33 kg", info)
        self.assertIn("0.3333", info)

    def test_08_resinas_sin_espesor_no_muestran_cotizacion(self):
        self.ir_al_cotizador()
        self.cotizar(PISOS, ancho=4.0, largo=4.0)
        self.at.number_input(key="area_espesor").set_value(0.0)
        self.at.run()

        self.assertIn("espesor mayor que 0 mm", " ".join(str(w.value) for w in self.at.warning))
        self.assert_sin_cotizacion()


if __name__ == "__main__":
    resultado = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    n = resultado.testsRun
    if resultado.wasSuccessful() and n == 9:
        print(f"\n{n}/{n} OK — cotizador por área verificado en la interfaz")
        sys.exit(0)
    print(f"\n{n - len(resultado.failures) - len(resultado.errors)}/{n} (esperadas 9/9)")
    sys.exit(1)
