#!/usr/bin/env python3
"""Prueba de interfaz del alta automática de SKUs (AppTest sobre una copia temporal).

Reproduce el incidente real: se subió al repositorio una plantilla vieja del Excel y la
hoja `Inventario` quedó con la columna `SKU` vacía, así que la app mostraba *NONE* en
todas las filas. Con `sku_nemet` la app debe:

1. rellenar los códigos al cargar la hoja (sin tocar el archivo ni pedir permiso);
2. avisar cuántos generó y ofrecer el botón para fijarlos en el Excel;
3. dejar el Excel con un SKU único por producto al pulsar ese botón.

La segunda batería (`TestInventarioMigradoEnLaApp`) revisa el otro extremo: el libro **ya
migrado** (plantilla del proveedor convertida a nombres de `Cat_Productos`, precios por
presentación y `PrecioCompra`) entra a la app sin generar un solo SKU y sin renglones
vacíos, repetidos ni sin línea de producto.

Se ejecuta sobre una copia temporal: nunca toca el Excel ni la base de usuarios reales.

Uso:  python tests_sku_app.py     →  "N/N OK" si todo pasa.
"""
import os
import shutil
import sys
import tempfile
import unittest

import openpyxl
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # sin Streamlit instalado, la prueba no aplica
    AppTest = None

import auth_nemet as auth  # noqa: E402
import sku_nemet as sk  # noqa: E402

EXCEL_NOMBRE = "Sistema_Inventario_NEMET_Final.xlsx"
PASSWORD_SEMILLA = "ClaveAdminInicial2026"
PASSWORD_ADMIN = "ClaveAdminDefinitiva2026"
MENU_INVENTARIO = "📦 Control de Inventario y Edición"


def vaciar_skus(ruta_excel):
    """Deja la hoja Inventario como llegó la plantilla vieja: encabezado SKU, celdas vacías."""
    libro = openpyxl.load_workbook(ruta_excel)
    hoja = libro["Inventario"]
    for fila in range(3, hoja.max_row + 1):   # fila 1 título, fila 2 encabezados
        hoja.cell(row=fila, column=1).value = None
    libro.save(ruta_excel)


def leer_inventario(ruta_excel):
    df = pd.read_excel(ruta_excel, sheet_name="Inventario", header=1)
    return df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")].dropna(how="all")


@unittest.skipIf(AppTest is None, "Streamlit no está instalado")
class TestSkuAutomaticoEnLaApp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app_dir = os.path.join(cls.tmp.name, "nemet")
        os.makedirs(os.path.join(cls.app_dir, "assets"), exist_ok=True)
        for nombre in ("app.py", "auth_nemet.py", "prospeccion.py", "cotizador_calculo.py",
                       "sku_nemet.py", "tema_nemet.py", EXCEL_NOMBRE):
            shutil.copy(os.path.join(BASE, nombre), os.path.join(cls.app_dir, nombre))
        for nombre in os.listdir(os.path.join(BASE, "assets")):
            shutil.copy(os.path.join(BASE, "assets", nombre), os.path.join(cls.app_dir, "assets", nombre))

        cls.ruta_excel = os.path.join(cls.app_dir, EXCEL_NOMBRE)
        vaciar_skus(cls.ruta_excel)          # ← el escenario del reporte: todo en NONE

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

    def _sin_excepciones(self):
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])

    def test_01_la_app_carga_el_inventario_sin_mostrar_none(self):
        self._sin_excepciones()
        inventario = self.at.session_state["inventario"]
        self.assertGreater(len(inventario), 0)
        self.assertTrue(all(str(s).strip() for s in inventario["SKU"]), "ningún renglón sin SKU")
        self.assertNotIn("NONE", " ".join(str(s) for s in inventario["SKU"]).upper())
        generados = self.at.session_state["inv_skus_generados"]
        self.assertEqual(len(generados), len(inventario), "todos los renglones venían sin SKU")

    def test_02_el_dashboard_avisa_que_se_generaron(self):
        self._sin_excepciones()
        avisos = " ".join(str(i.value) for i in self.at.info)
        self.assertIn("no tenían SKU", avisos)

    def test_03_el_modulo_de_inventario_ofrece_guardarlos(self):
        self.at.sidebar.selectbox[0].select(MENU_INVENTARIO)
        self.at.run()
        self._sin_excepciones()
        avisos = " ".join(str(w.value) for w in self.at.warning)
        self.assertIn("SKUs**", avisos)
        botones = [b for b in self.at.button if "Guardar SKUs generados" in str(b.label)]
        self.assertEqual(len(botones), 1, "debe haber un botón para fijar los SKUs en el Excel")

    def test_04_al_guardar_el_excel_queda_con_skus_unicos(self):
        boton = [b for b in self.at.button if "Guardar SKUs generados" in str(b.label)][0]
        boton.click()
        self.at.run()
        self._sin_excepciones()

        df = leer_inventario(self.ruta_excel)
        self.assertTrue(all(str(s).strip() and str(s).upper() != "NONE" for s in df["SKU"]))
        self.assertEqual(sk.skus_duplicados(df), [], "no debe repetirse ningún SKU")
        # Se conserva la nomenclatura histórica de cada familia
        pisos = [s for d, s in zip(df["Descripcion"], df["SKU"])
                 if str(d).strip() == "EPOXY PISOS (A Y B) TRANSPARENTE"]
        self.assertEqual(pisos[:2], ["EPT 01", "EPT 02"])
        self.assertEqual(self.at.session_state["inv_skus_generados"], [])


@unittest.skipIf(AppTest is None, "Streamlit no está instalado")
class TestInventarioMigradoEnLaApp(unittest.TestCase):
    """El libro migrado (31-oct-2026) se abre completo y sin SKUs que generar."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app_dir = os.path.join(cls.tmp.name, "nemet")
        os.makedirs(os.path.join(cls.app_dir, "assets"), exist_ok=True)
        for nombre in ("app.py", "auth_nemet.py", "prospeccion.py", "cotizador_calculo.py",
                       "sku_nemet.py", "tema_nemet.py", EXCEL_NOMBRE):
            shutil.copy(os.path.join(BASE, nombre), os.path.join(cls.app_dir, nombre))
        for nombre in os.listdir(os.path.join(BASE, "assets")):
            shutil.copy(os.path.join(BASE, "assets", nombre), os.path.join(cls.app_dir, "assets", nombre))
        cls.ruta_excel = os.path.join(cls.app_dir, EXCEL_NOMBRE)

        os.environ["NEMET_DB_USUARIOS"] = os.path.join(cls.tmp.name, "usuarios.db")
        os.environ["NEMET_ADMIN_USUARIO"] = "jefe"
        os.environ["NEMET_ADMIN_PASSWORD"] = PASSWORD_SEMILLA
        os.environ["NEMET_ADMIN_NOMBRE"] = "Jefe de Planta"

        conn = auth.conectar(os.environ["NEMET_DB_USUARIOS"])
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

    def test_01_carga_las_88_fichas_sin_generar_codigos(self):
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        inventario = self.at.session_state["inventario"]
        self.assertEqual(len(inventario), 88)
        self.assertEqual(self.at.session_state["inv_skus_generados"], [],
                         "el libro migrado ya trae todos los SKU")
        self.assertEqual(sk.skus_duplicados(inventario), [])

    def test_02_la_migracion_dejo_el_costo_de_compra(self):
        inventario = self.at.session_state["inventario"]
        con_costo = pd.to_numeric(inventario["PrecioCompra"], errors="coerce").fillna(0) > 0
        self.assertGreaterEqual(int(con_costo.sum()), 70,
                                "casi todos los SKU deben traer el costo del proveedor")

    def test_03_cada_ficha_tiene_su_linea_de_producto(self):
        inventario = self.at.session_state["inventario"]
        self.assertIn("Línea", inventario.columns)
        self.assertTrue(all(str(l).strip() for l in inventario["Línea"]))
        self.assertEqual(set(inventario["Línea"]), {"Resina", "Acabado", "Complemento", "Herramienta"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
