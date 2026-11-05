#!/usr/bin/env python3
"""Pruebas del Cotizador General de Catálogo (el presupuesto en el formato del CSV).

Cubren el paquete de los archivos del proveedor (`Cotizador_Catalogo_Epoxy_Art_Completo.csv`
y el `.xlsx` que era un `.xlsx` renombrado):

* El presupuesto de la muestra —Epoxy pisos 5K + Epoxy Primer 9KG + Jalador Epóxico
  Premium + Rodillo de Picos— suma **$1,800.00** con la lista del proveedor tal cual
  (sin pisos de margen: los flacos se quedan como vienen, decisión registrada en
  `cotizador_general_catalogo.md`).
* `csv_presupuesto` reproduce el CSV del proveedor **byte a byte** (cp1252, CRLF).
* Las fichas quedan actualizadas con lo que trajeron los archivos conservando los
  88 SKU (`Inventario`), las 39 escalas (`Escalas_Precios_KG`) y las dosis y
  rendimientos de `Cat_Productos`.
* El PDF lleva el mismo formato (Ítem / Unidad-Presentación / Cantidad / Precio
  unitario / Subtotal / TOTAL GENERAL) y no filtra costo ni margen.

Corre sobre los archivos reales del repo (no los modifica).  Uso:
python tests_cotizador_general.py  →  "NN/NN OK".
"""
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
import zlib

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import cotizador_general as cogen  # noqa: E402

RUTA_EXCEL = os.path.join(BASE, "Sistema_Inventario_NEMET_Final.xlsx")
RUTA_CSV_MUESTRA = os.path.join(BASE, "Cotizador_Catalogo_Epoxy_Art_Completo.csv")

# La muestra del proveedor: 4 ítems que deben dar $1,800.00
DEMO = [
    ("Epoxy pisos 5K", 1.0),          # 0 - 5.999 KG   $480.00
    ("Epoxy Primer 9KG", 1.0),        # 0 - 9,999 KG   $400.00
    ("Jalador Epóxico Premium", 1.0), # PZA            $425.00
    ("Rodillo de Picos", 1.0),        # PZA            $495.00
]


def items_demo():
    cat = cogen.cargar_catalogo_presupuestos(RUTA_EXCEL)
    return cogen.items_desde_catalogo(cat, DEMO)


# ============================================================ formato del presupuesto
class TestFormatoPresupuesto(unittest.TestCase):

    def test_01_la_muestra_son_4_items_que_dan_1800(self):
        items = items_demo()
        self.assertEqual([it["subtotal"] for it in items], [480.0, 400.0, 425.0, 495.0])
        self.assertEqual([it["precio_unitario"] for it in items], [480.0, 400.0, 425.0, 495.0])
        self.assertEqual([it["unidad"] for it in items],
                         ["0 - 5.999 KG", "0 - 9,999 KG", "PZA", "PZA"])
        cantidad, total = cogen.totales_presupuesto(items)
        self.assertEqual(cogen.cantidad_txt(cantidad), "4.00")
        self.assertEqual(cogen.importe_txt(total), "$1,800.00")

    def test_02_el_csv_exportado_es_identico_al_del_proveedor(self):
        generado = cogen.csv_presupuesto(items_demo())
        with open(RUTA_CSV_MUESTRA, "rb") as fh:
            esperado = fh.read()
        self.assertEqual(generado, esperado,
                         "el CSV exportado debe ser byte a byte el del proveedor")

    def test_03_la_plantilla_reserva_15_renglones_y_cierra_con_total_general(self):
        filas = cogen.filas_presupuesto(items_demo())
        self.assertEqual(len(filas), 5 + 15 + 1)  # encabezado + huecos + total
        self.assertEqual(filas[0][0], cogen.TITULO_PRESUPUESTO)
        self.assertEqual(filas[4], cogen.COLUMNAS_PRESUPUESTO)
        huecos = filas[9:20]
        self.assertEqual(len(huecos), 11)
        for hueco in huecos:
            self.assertEqual(hueco, ["", "", "", "", "$0.00"])
        self.assertEqual(filas[-1], ["TOTAL GENERAL", "", "4.00", "", "$1,800.00"])

    def test_04_con_mas_de_15_renglones_la_plantilla_crece_sin_huecos(self):
        items = [{"nombre": f"Producto {i}", "unidad": "PZA", "cantidad": 1, "precio_unitario": 10}
                 for i in range(18)]
        filas = cogen.filas_presupuesto(items)
        self.assertEqual(len(filas), 5 + 18 + 1)
        self.assertEqual(filas[-1][4], "$180.00")

    def test_05_la_tabla_en_pantalla_muestra_la_fila_total_general(self):
        tabla = cogen.presupuesto_dataframe(items_demo())
        self.assertEqual(list(tabla.columns), cogen.COLUMNAS_PRESUPUESTO)
        ultima = tabla.iloc[-1]
        self.assertEqual(ultima[cogen.COLUMNAS_PRESUPUESTO[0]], "TOTAL GENERAL")
        self.assertEqual(ultima[cogen.COLUMNAS_PRESUPUESTO[2]], "4.00")
        self.assertEqual(ultima[cogen.COLUMNAS_PRESUPUESTO[4]], "$1,800.00")


# ============================================================ fichas del Sistema
class TestFichasActualizadas(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.inventario = pd.read_excel(RUTA_EXCEL, "Inventario", header=1).dropna(how="all")
        cls.escalas = pd.read_excel(RUTA_EXCEL, "Escalas_Precios_KG").dropna(how="all")
        cls.catalogo = pd.read_excel(RUTA_EXCEL, "Cat_Productos").dropna(how="all")
        cls.presupuestos = cogen.cargar_catalogo_presupuestos(RUTA_EXCEL)

    def test_06_inventario_conserva_los_88_sku_y_escalas_las_39(self):
        self.assertEqual(len(self.inventario), 88, "los 88 SKU se conservan")
        self.assertEqual(len(self.escalas), 39, "las 39 escalas del proveedor se conservan")

    def test_07_cat_productos_con_los_precios_que_trajeron_los_archivos(self):
        precios = {(str(f["Producto"]), float(f["Presentacion"])): float(f["PrecioPublico"])
                   for _, f in self.catalogo.iterrows()}
        for pres, esperado in [(60.0, 80.0), (100.0, 120.0), (250.0, 280.0), (500.0, 540.0), (1.0, 860.0)]:
            self.assertEqual(precios[("EPOXY PASTA COLORES SÓLIDOS", pres)], esperado,
                             f"pasta {pres} debe traer el precio nuevo de los archivos")
        for pres, esperado in [(5.0, 2250.0), (10.0, 4400.0), (20.0, 8600.0)]:
            self.assertEqual(precios[("POLIURETANO (A Y B) TRANSPARENTE", pres)], esperado,
                             f"poliuretano {pres} debe cuadrar con las escalas por litro")

    def test_08_el_catalogo_tiene_70_renglones_unicos_con_spikes_premium_restaurado(self):
        self.assertEqual(len(self.presupuestos), 70)
        nombres = self.presupuestos["Producto / Item"].astype(str)
        self.assertEqual(int(nombres.duplicated().sum()), 0, "sin nombres duplicados")
        fila = self.presupuestos[nombres == "Epoxy Spikes Premium"].iloc[0]
        self.assertEqual(float(fila["Costo Proveedor (MXN)"]), 2373.11,
                         "el renglón corrupto era el precio $2,373.11 de Spikes Premium")
        self.assertEqual(float(fila["Precio Público Sugerido (MXN)"]), 2498.0)

    def test_09_los_flacos_se_dejan_como_vienen_en_la_lista_del_proveedor(self):
        # Decisión: SIN piso de margen de 15 % — los precios son los del proveedor.
        inv = {str(f["SKU"]): f for _, f in self.inventario.iterrows()}
        self.assertEqual(float(inv["EPRT 01"]["PrecioPublicoIVA"]), 540.0)
        self.assertEqual(float(inv["RP 01"]["PrecioPublicoIVA"]), 495.0)
        self.assertEqual(float(inv["R 01"]["PrecioPublicoIVA"]), 80.0)
        self.assertEqual(float(inv["R 02"]["PrecioPublicoIVA"]), 200.0)
        self.assertEqual(float(inv["JEP 01"]["PrecioPublicoIVA"]), 425.0)
        self.assertEqual(float(inv["EC 02"]["PrecioPublicoIVA"]), 30.0)
        # ...pero su margen real queda visible en Escalas_Precios_KG.
        primer = self.escalas[(self.escalas["Producto_Proveedor"] == "EPOXY PRIMER")
                              & (self.escalas["Rango_Desde_Kg"] == 0.0)].iloc[0]
        self.assertAlmostEqual(float(primer["Margen_Porcentaje_Sobre_Venta"]), 0.5025, places=4)


# ============================================================ PDF y carrito
class TestPresupuestoPDF(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Carga app.py solo hasta la sección de ACCESO (como tests_bateria): funciones sin interfaz.
        with open(os.path.join(BASE, "app.py"), encoding="utf-8") as fh:
            lineas = fh.read().splitlines(keepends=True)
        corte = next(i for i, l in enumerate(lineas) if l.startswith("# ACCESO (se ejecuta antes"))
        cls.app = types.ModuleType("app_sin_interfaz")
        cls.app.__file__ = os.path.join(BASE, "app.py")
        exec(compile("".join(lineas[:corte]), cls.app.__file__, "exec"), cls.app.__dict__)

    @staticmethod
    def _item_con_datos_internos():
        return {
            "SKU": "EM-001",
            "Descripcion": "Epoxy-Mica Gris",
            "Presentacion": "Cubeta 20 L",
            "Area_m2": 120.0,
            "Espesor_mm": 3,
            "Kg_Necesarios": 36.0,
            "Cantidad": 2,
            "Subtotal": 8450.0,
            "Detalle": ("Epoxy-Mica Gris\n120.00 m² x 3 mm (36.000 kg)\n"
                        "Modalidad: CUBETAS - 2x Cubeta 20 L = 40.000 kg\n"
                        "Precio: $8,450.00 | Costo compra: $6,120.00\n"
                        "Margen: $2,330.00 (27.6% venta / 38.1% markup)"),
        }

    @staticmethod
    def texto_de_pdf(pdf_bytes):
        """Texto legible de un PDF: descomprime sus flujos con zlib (solo stdlib)."""
        partes = [pdf_bytes]
        for flujo in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
            try:
                partes.append(zlib.decompress(flujo))
            except zlib.error:
                partes.append(flujo)
        return b"".join(partes)

    def test_10_items_desde_carrito_deriva_precio_unitario_y_etiqueta(self):
        items = cogen.items_desde_carrito(pd.DataFrame([self._item_con_datos_internos()]))
        self.assertEqual(items[0]["nombre"], "Epoxy-Mica Gris [120.00 m² x 3 mm]")
        self.assertEqual(items[0]["unidad"], "Cubeta 20 L")
        self.assertEqual(items[0]["precio_unitario"], 4225.0)
        self.assertEqual(items[0]["subtotal"], 8450.0)

    def test_11_el_pdf_lleva_el_formato_del_csv_y_no_filtraciones(self):
        items = pd.DataFrame([self._item_con_datos_internos()])
        pdf = self.app.generar_pdf_cotizacion(
            "COT-2026-014", "Constructora del Norte S.A.", items,
            "Cotización por Área y Sistemas Epóxicos")
        texto = self.texto_de_pdf(pdf)
        for interno in (b"Costo compra", b"Margen", b"markup", b"6,120", b"2,330", b"27.6", b"38.1"):
            self.assertNotIn(interno, texto, f"el PDF filtra el dato interno {interno!r}")
        for visible in (b"Epoxy-Mica Gris", b"8,450.00", b"TOTAL GENERAL", b"4,225.00", b"2.00"):
            self.assertIn(visible, texto, f"el PDF perdió el dato del cliente {visible!r}")

    def test_12_el_pdf_de_la_muestra_cierra_en_1800(self):
        filas = [dict(it) for it in items_demo()]
        marco = pd.DataFrame([{
            "SKU": "", "Descripcion": it["nombre"], "Presentacion": it["unidad"],
            "Cantidad": it["cantidad"], "Subtotal": it["subtotal"],
        } for it in filas])
        pdf = self.app.generar_pdf_cotizacion("COT-2026-002", "Cliente General", marco, "Cotización de Catálogo")
        texto = self.texto_de_pdf(pdf)
        for visible in (b"Epoxy pisos 5K", b"Epoxy Primer 9KG", b"480.00", b"400.00",
                        b"425.00", b"495.00", b"TOTAL GENERAL", b"1,800.00"):
            self.assertIn(visible, texto, f"el PDF de la muestra perdió {visible!r}")


# ============================================================ la app, como la usa una persona
try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # sin Streamlit instalado, la batería no aplica
    AppTest = None

PASSWORD_SEMILLA = "ClaveAdminInicial2026"
PASSWORD_ADMIN = "ClaveAdminDefinitiva2026"
MENU_COTIZADOR = "📝 Cotizador Comercial Profesional"


@unittest.skipIf(AppTest is None, "Streamlit no está instalado")
class TestCotizadorGeneralEnLaApp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app_dir = os.path.join(cls.tmp.name, "nemet")
        os.makedirs(os.path.join(cls.app_dir, "assets"), exist_ok=True)
        for nombre in ("app.py", "auth_nemet.py", "prospeccion.py", "cotizador_calculo.py",
                       "cotizador_general.py", "sku_nemet.py", "Sistema_Inventario_NEMET_Final.xlsx"):
            shutil.copy(os.path.join(BASE, nombre), os.path.join(cls.app_dir, nombre))
        for nombre in os.listdir(os.path.join(BASE, "assets")):
            shutil.copy(os.path.join(BASE, "assets", nombre), os.path.join(cls.app_dir, "assets", nombre))

        cls.ruta_db = os.path.join(cls.tmp.name, "usuarios.db")
        os.environ["NEMET_DB_USUARIOS"] = cls.ruta_db
        os.environ["NEMET_ADMIN_USUARIO"] = "jefe"
        os.environ["NEMET_ADMIN_PASSWORD"] = PASSWORD_SEMILLA
        os.environ["NEMET_ADMIN_NOMBRE"] = "Jefe de Planta"

        import auth_nemet as auth
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

    def test_13_la_muestra_se_cotiza_desde_el_catalogo_y_da_1800(self):
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        self.at.sidebar.selectbox[0].select(MENU_COTIZADOR)
        self.at.run()
        self.at.radio(key="com_fuente").set_value("🧾 Catálogo de presupuestos")
        self.at.run()

        catalogo = cogen.cargar_catalogo_presupuestos(
            os.path.join(self.app_dir, "Sistema_Inventario_NEMET_Final.xlsx"))
        indices = {str(f["Producto / Item"]): i for i, f in catalogo.iterrows()}
        for nombre, _cantidad in DEMO:
            self.at.selectbox(key="com_cat_sel").select(indices[nombre])
            self.at.number_input(key="com_cat_cant").set_value(1.0)
            self.at.run()
            self.at.button(key="com_cat_add").click()
            self.at.run()
            self.assertFalse(self.at.exception, [e.value for e in self.at.exception])

        carrito = self.at.session_state["carrito_comercial"]
        self.assertEqual(len(carrito), 4)
        self.assertAlmostEqual(float(carrito["Subtotal"].sum()), 1800.0, places=2)

        # Y el CSV del carrito es exactamente el del proveedor.
        with open(RUTA_CSV_MUESTRA, "rb") as fh:
            esperado = fh.read()
        self.assertEqual(cogen.csv_presupuesto(carrito), esperado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
