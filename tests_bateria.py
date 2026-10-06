#!/usr/bin/env python3
"""Batería de pruebas del sistema de marca NEMET (29 pruebas).

Cubre la herramienta de procesado (herramientas/procesar_logo.py), los 3 assets
generados, la integración de marca en app.py y la configuración del repo.

Uso:  python tests_bateria.py     →  "24/24 OK" si todo pasa.
"""
import os
import re
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "herramientas"))
import procesar_logo as pl  # noqa: E402


def img_claro_sintetica(alto=300, ancho=400):
    """Foto sintética: muro crema texturizado, logo oscuro al centro, decoración pegada al borde."""
    arr = np.zeros((alto, ancho, 3), np.uint8)
    arr[:, :] = (200, 183, 165)
    rng = np.random.default_rng(7)
    arr = arr.astype(np.int16) + rng.integers(-12, 13, size=arr.shape)
    arr[110:190, 140:260] = (70, 68, 64)                       # logo central oscuro
    arr[:, 0:30] = (150, 90, 40)                                # decoración que toca el borde
    return np.clip(arr, 0, 255).astype(np.uint8)


# ============================================================ herramienta
class TestHerramienta(unittest.TestCase):

    def test_01_estimar_color_fondo_es_la_mediana_del_borde(self):
        arr = np.zeros((40, 50, 3), np.float64)
        arr[:, :] = (200, 180, 160)
        arr[20:24, 10:40] = (0, 0, 0)  # contenido interno no debe afectar
        fondo = pl.estimar_color_fondo(arr)
        np.testing.assert_allclose(fondo, (200, 180, 160), atol=1)

    def test_02_modo_entrada_alpha(self):
        arr = np.zeros((60, 60, 4), np.uint8)
        arr[..., 3] = 255
        arr[:, :40, 3] = 0  # 2/3 del lienzo transparente
        self.assertEqual(pl.modo_entrada(arr), "alpha")

    def test_03_modo_entrada_claro_fondo_opaco(self):
        arr = np.dstack([np.full((50, 50, 3), (210, 200, 190), np.uint8), np.full((50, 50), 255, np.uint8)])
        self.assertEqual(pl.modo_entrada(arr), "claro")

    def test_04_modo_entrada_oscuro_fondo_negro(self):
        arr = np.dstack([np.full((50, 50, 3), (4, 4, 5), np.uint8), np.full((50, 50), 255, np.uint8)])
        self.assertEqual(pl.modo_entrada(arr), "oscuro")

    def test_05_matar_componentes_deco_y_ruido(self):
        mask = np.zeros((100, 200), bool)
        mask[40:60, 90:110] = True          # contenido central: se queda
        mask[:, 0:8] = True                 # pega al borde: se va
        mask[95:100, 150:155] = True        # isla diminuta: se va
        limpio = pl._matar_componentes(mask, min_size=30, margin=5)
        self.assertTrue(limpio[50, 100])
        self.assertFalse(limpio[50, 2])
        self.assertFalse(limpio[97, 152])

    def test_06_bandas_separan_filas_de_contenido(self):
        mask = np.zeros((60, 40), bool)
        mask[5:12, :] = True
        mask[30:40, :] = True
        bandas = pl._bandas(mask, min_grosor=2, min_pix=1)
        self.assertEqual(len(bandas), 2)
        self.assertEqual(bandas[0][1] - bandas[0][0], 6)

    def test_07_procesar_claro_recorta_con_transparencia(self):
        arr = np.dstack([img_claro_sintetica(), np.full((300, 400), 255, np.uint8)])
        logo, modo = pl.procesar(arr)
        self.assertEqual(modo, "claro")
        a = np.array(logo)
        self.assertEqual(a.shape[2], 4)
        self.assertEqual(int(a[0, 0, 3]), 0)            # esquina: fondo eliminado
        ys, xs = np.nonzero(a[..., 3] > 200)
        self.assertGreater(len(ys), 500)                # contenido conservado
        # la decoración pegada al borde no debe quedar como región grande aislada
        self.assertLess(a[..., 3].mean(), 255)

    def test_08_ruta_alpha_conserva_y_recorta(self):
        arr = np.zeros((120, 160, 4), np.uint8)
        arr[30:90, 40:120, :3] = (160, 90, 50)
        arr[30:90, 40:120, 3] = 255
        arr[5, 5, :] = (200, 200, 200, 255)             # mota mínima que debe limpiarse
        logo, modo = pl.procesar(arr)
        self.assertEqual(modo, "alpha")
        self.assertLessEqual(logo.width, 150)
        a = np.array(logo)
        self.assertGreater(float((a[..., 3] > 200).mean()), 0.2)

    def test_09_favicon_cuadrado_con_esquinas_redondeadas(self):
        foto = img_claro_sintetica(alto=200, ancho=280)
        rgba = np.dstack([foto, np.full(foto.shape[:2], 255, np.uint8)])
        logo = Image.fromarray(rgba, "RGBA")
        fav = pl.generar_favicon(logo, tam=64, radio=14)
        self.assertEqual(fav.size, (64, 64))
        fa = np.array(fav)
        self.assertLess(int(fa[0, 0, 3]), 60)            # esquina redondeada casi transparente
        self.assertGreater(int(fa[32, 32, 3]), 200)     # centro opaco

    def test_10_hero_dimension_y_claro(self):
        logo = Image.new("RGBA", (400, 200), (38, 35, 31, 255))
        hero = pl.generar_hero(logo, ancho=800, alto=450)
        self.assertEqual(hero.size, (800, 450))
        ha = np.array(hero)[..., :3]
        self.assertGreater(float(ha[4, 4].mean()), 220)      # borde crema (viñeta suave)
        self.assertLess(float(ha[225, 400].mean()), 80)      # el logo sí va compuesto al centro
        self.assertGreater(float(ha.mean()), 150)            # lienzo claro, no letterpress

    def test_11_cli_genera_los_assets_claros_y_rechaza_el_oscuro(self):
        with tempfile.TemporaryDirectory() as tdir:
            claro_in = os.path.join(tdir, "claro.png")
            Image.fromarray(img_claro_sintetica(), "RGB").save(claro_in)
            sal = os.path.join(tdir, "assets")
            rc = pl.main([claro_in, "--salida", sal])
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(os.listdir(sal)),
                             ["favicon.png", "hero_claro.png", "logo_claro.png", "logo_claro_400.png"])
            # la marca ya no genera letterpress: una entrada con fondo negro se rechaza
            osc_in = os.path.join(tdir, "oscuro.png")
            arr = np.dstack([np.full((100, 140, 3), (4, 4, 5), np.uint8),
                             np.full((100, 140), 255, np.uint8)])
            Image.fromarray(arr, "RGBA").save(osc_in)
            with self.assertRaises(SystemExit):
                pl.main([osc_in, "--salida", os.path.join(tdir, "assets2")])
            self.assertFalse(os.path.exists(os.path.join(tdir, "assets2")))

    def test_12_lado_maximo_respetado(self):
        foto = img_claro_sintetica(alto=600, ancho=800)
        arr = np.dstack([foto, np.full(foto.shape[:2], 255, np.uint8)])
        logo, _ = pl.procesar(arr, max_side=150)
        self.assertLessEqual(max(logo.size), 150)


# ============================================================ assets del repo
class TestAssets(unittest.TestCase):

    ASSETS = os.path.join(BASE, "assets")

    def test_13_existen_los_tres_assets(self):
        for n in ("logo_claro.png", "favicon.png", "hero_claro.png"):
            self.assertTrue(os.path.exists(os.path.join(self.ASSETS, n)), f"falta assets/{n}")
        # el letterpress oscuro se retiró de la interfaz
        for n in ("logo_oscuro.png", "hero_oscuro.png"):
            self.assertFalse(os.path.exists(os.path.join(self.ASSETS, n)), f"assets/{n} ya no debe existir")

    def test_14_logo_claro_transparente_con_contenido(self):
        a = np.array(Image.open(os.path.join(self.ASSETS, "logo_claro.png")))
        self.assertEqual(a.shape[2], 4)
        self.assertEqual(int(a[0, 0, 3]), 0)
        self.assertGreater(float((a[..., 3] > 200).mean()), 0.15)

    def test_15_logo_claro_lado_maximo(self):
        im = Image.open(os.path.join(self.ASSETS, "logo_claro.png"))
        self.assertLessEqual(max(im.size), 1400)
        a = np.array(im)
        self.assertEqual(int(a[0, 0, 3]), 0)

    def test_16_favicon_128_redondeado(self):
        im = Image.open(os.path.join(self.ASSETS, "favicon.png"))
        self.assertEqual(im.size, (128, 128))
        a = np.array(im)
        self.assertLess(int(a[0, 0, 3]), 100)
        self.assertGreater(int(a[64, 64, 3]), 150)

    def test_17_hero_claro_1600x900(self):
        im = Image.open(os.path.join(self.ASSETS, "hero_claro.png"))
        self.assertEqual(im.size, (1600, 900))
        a = np.array(im)[..., :3].astype(np.float64)
        luma = a @ np.array([0.299, 0.587, 0.114])
        self.assertGreater(float(a.mean()), 150)             # portada crema, no letterpress
        self.assertGreater(float((luma < 200).mean()), 0.05)  # el logo ocupa el centro

    def test_18_originales_fuera_del_repo(self):
        for n in ("Logo NEMET.png", "Logo NEMET 3.png"):
            self.assertFalse(os.path.exists(os.path.join(BASE, n)), f"{n} ya no debe estar en la raíz")

    def test_25_logo_para_pdf_de_400px_y_ligero(self):
        # el PDF se descarga y se manda por correo: no puede cargar el logo original (~900 KB)
        ruta_400 = os.path.join(self.ASSETS, "logo_claro_400.png")
        ruta_full = os.path.join(self.ASSETS, "logo_claro.png")
        self.assertTrue(os.path.exists(ruta_400), "falta assets/logo_claro_400.png")
        im = Image.open(ruta_400)
        self.assertEqual(im.width, 400)
        orig = Image.open(ruta_full)
        self.assertEqual(round(im.height / im.width * orig.width), orig.height)  # misma proporción
        self.assertLess(os.path.getsize(ruta_400), os.path.getsize(ruta_full) / 5)
        a = np.array(im.convert("RGBA"))
        self.assertEqual(int(a[0, 0, 3]), 0)                        # conserva la transparencia
        self.assertGreater(float((a[..., 3] > 200).mean()), 0.15)   # y el contenido del logo


# ============================================================ app.py
class TestApp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BASE, "app.py"), encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_19_config_pagina_es_la_primera_llamada_st(self):
        # a nivel de módulo (columna 0) la primera llamada st.* debe ser la configuración de página
        m = re.search(r"^st\.(\w+)\s*\(", self.src, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "set_page_config")

    def test_20_funciones_y_constantes_de_marca(self):
        for token in ("PALETA_NEMET", "LEMA_NEMET", "DIR_ASSETS", "def _imagen_asset", "def logo_marca",
                      "def estilos_marca", "def cabecera_marca", "def portada_marca"):
            self.assertIn(token, self.src, f"falta {token} en app.py")

    def test_21_app_referencia_solo_assets_claros(self):
        for n in ("logo_claro.png", "favicon.png", "hero_claro.png"):
            self.assertIn(n, self.src, f"app.py no referencia {n}")
        for n in ("logo_oscuro.png", "hero_oscuro.png"):
            self.assertNotIn(n, self.src, f"app.py ya no debe referenciar {n}")

    def test_22_lema_en_franja_y_pie(self):
        self.assertIn("nemet-lema", self.src)
        self.assertIn("st.caption(f\"© {ahora_local().year} NEMET", self.src)

    def test_27_el_pdf_no_lee_la_columna_detalle(self):
        # «Detalle» guarda costo de compra y margen para el desglose interno: si el PDF
        # volviera a leer esa columna, el cliente vería de cuánto le sale al vendedor.
        fn = self.src[self.src.index("def generar_pdf_cotizacion"):self.src.index("def enviar_cotizacion_por_correo")]
        self.assertNotIn('"Detalle"', fn, "el PDF no debe leer la columna Detalle del carrito")
        self.assertIn("descripcion_para_cliente(fila)", fn)

    def test_26_el_pdf_lleva_el_logo_de_marca(self):
        self.assertIn("logo_claro_400.png", self.src, "el PDF debe usar la versión de 400 px")
        self.assertIn("def dibujar_cabecera_pdf", self.src)
        self.assertIn("def ruta_logo_pdf", self.src)
        # la cabecera del PDF se dibuja antes del folio y la tabla de artículos
        cabecera = self.src.index("def dibujar_cabecera_pdf")
        pdf_fn = self.src.index("def generar_pdf_cotizacion")
        self.assertIn("dibujar_cabecera_pdf(pdf)", self.src[pdf_fn:])
        self.assertLess(cabecera, pdf_fn)


# ============================================================ PDF para el cliente
def modulo_app_sin_interfaz():
    """Carga app.py solo hasta la sección de ACCESO: deja las funciones disponibles sin
    arrancar Streamlit ni tocar la base de usuarios ni el Excel."""
    import types

    with open(os.path.join(BASE, "app.py"), encoding="utf-8") as fh:
        lineas = fh.read().splitlines(keepends=True)
    corte = next(i for i, l in enumerate(lineas) if l.startswith("# ACCESO (se ejecuta antes"))
    modulo = types.ModuleType("app_sin_interfaz")
    modulo.__file__ = os.path.join(BASE, "app.py")
    exec(compile("".join(lineas[:corte]), modulo.__file__, "exec"), modulo.__dict__)
    return modulo


def texto_de_pdf(pdf_bytes):
    """Texto legible de un PDF: descomprime sus flujos con zlib (solo stdlib)."""
    import re
    import zlib

    partes = [pdf_bytes]
    for flujo in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
        try:
            partes.append(zlib.decompress(flujo))
        except zlib.error:  # flujo sin comprimir o con otro filtro: se busca en crudo
            partes.append(flujo)
    return b"".join(partes)


class TestPDFParaCliente(unittest.TestCase):
    """El PDF que ve el cliente no debe revelar el costo de compra ni el margen."""

    @classmethod
    def setUpClass(cls):
        cls.app = modulo_app_sin_interfaz()

    @staticmethod
    def _item_con_datos_internos():
        # Renglón tal cual lo arma la app: «Detalle» trae costo de compra y margen.
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

    def test_28_descripcion_para_cliente_ignora_el_detalle_interno(self):
        pd = self.app.pd
        fila = pd.Series(self._item_con_datos_internos())
        texto = self.app.descripcion_para_cliente(fila)
        self.assertEqual(texto, "Epoxy-Mica Gris [120.00 m² x 3 mm]")
        for interno in ("Costo compra", "Margen", "markup", "6,120", "2,330"):
            self.assertNotIn(interno, texto)

    def test_29_el_pdf_no_revela_costo_ni_margen(self):
        pd = self.app.pd
        items = pd.DataFrame([self._item_con_datos_internos()])
        pdf = self.app.generar_pdf_cotizacion(
            "COT-2026-014", "Constructora del Norte S.A.", items,
            "Cotización por Área y Sistemas Epóxicos")
        texto = texto_de_pdf(pdf)
        for interno in (b"Costo compra", b"Margen", b"markup", b"6,120", b"2,330", b"27.6", b"38.1"):
            self.assertNotIn(interno, texto, f"el PDF filtra el dato interno {interno!r}")
        # lo que sí debe verse: el producto, el área cotizada y el precio al cliente
        for visible in (b"Epoxy-Mica Gris", b"8,450.00"):
            self.assertIn(visible, texto, f"el PDF perdió el dato del cliente {visible!r}")


# ============================================================ config repo
class TestConfig(unittest.TestCase):

    def test_23_requirements_de_la_marca(self):
        with open(os.path.join(BASE, "requirements.txt"), encoding="utf-8") as fh:
            reqs = fh.read().lower()
        for p in ("pillow", "scipy", "numpy", "streamlit", "fpdf2"):
            self.assertIn(p, reqs)

    def test_24_config_toml_coherente_con_paleta(self):
        ruta = os.path.join(BASE, ".streamlit", "config.toml")
        self.assertTrue(os.path.exists(ruta))
        with open(ruta, encoding="utf-8") as fh:
            toml = fh.read()
        self.assertIn('#B4552D', toml)  # barro = primaryColor
        self.assertIn('#F5EFE6', toml)  # crema = backgroundColor
        with open(os.path.join(BASE, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("herramientas/procesar_logo.py", readme)


if __name__ == "__main__":
    resultado = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    n = resultado.testsRun
    if resultado.wasSuccessful() and n == 29:
        print(f"\n{ n }/{n} OK — sistema de marca NEMET verificado")
        sys.exit(0)
    print(f"\n{ n - len(resultado.failures) - len(resultado.errors) }/{n} (esperadas 29/29)")
    sys.exit(1)
