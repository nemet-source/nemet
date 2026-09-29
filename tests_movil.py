#!/usr/bin/env python3
"""Batería de pruebas del modo móvil NEMET (15 pruebas).

Cubre `movil_nemet.py` —detección (ancho declarado, Client Hints, user-agent y forzado por
URL), funciones puras, hoja de estilos y tema— y su integración en `app.py` y el `README.md`
(el modo se resuelve antes del gate de acceso y la configuración de página sigue siendo lo
primero). Dos pruebas usan `streamlit.testing.v1.AppTest`: una **entra de verdad a la app**
sobre una copia temporal del proyecto (login incluido) y comprueba el dashboard en modo móvil,
y la otra resuelve el forzado por URL y su recuerdo en la sesión sobre una sonda mínima.

Uso:  python tests_movil.py     →  "15/15 OK" si todo pasa.
"""
import inspect
import os
import re
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import auth_nemet as auth  # noqa: E402
import movil_nemet as mn  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # sin Streamlit instalado no corren las dos pruebas de la app real
    AppTest = None

# Agentes de usuario reales de referencia (los que declaran los navegadores en la calle).
UA_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
UA_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Mobile Safari/537.36")
UA_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/126.0.0.0 Safari/537.36")
UA_WINDOWS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
UA_IPAD = ("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
           "Version/17.5 Mobile/15E148 Safari/604.1")
UA_TABLETA_ANDROID = ("Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0.0.0 Safari/537.36")

# Credenciales de la copia temporal de la app (nunca tocan la base real del repositorio).
CLAVE_SEMILLA = "ClaveAdminInicial2026"
CLAVE_ADMIN = "ClaveAdminDefinitiva2026"

# Sonda mínima: usa el módulo tal como lo usa app.py (resolver el modo, guardar el tema y pintar
# la hoja de estilos) y publica el resultado en el propio panel para poder leerlo desde AppTest.
SONDA = f'''
import sys
sys.path.insert(0, r"{BASE}")
import streamlit as st
import movil_nemet as mn

modo = mn.aplicar()
st.markdown(f"modo={{modo}}")
st.markdown(f"tema={{mn.tema_actual()}}")
st.markdown(mn.estilos(oscuro=mn.tema_actual() == "oscuro"))
'''


# ============================================================ detección
class TestDeteccion(unittest.TestCase):

    def test_01_el_ancho_declarado_decide_y_tiene_prioridad(self):
        self.assertEqual(mn.ancho_declarado({"x-nemet-viewport": "390"}), 390)
        self.assertTrue(mn.modo_movil(encabezados={"x-nemet-viewport": "390"}))
        self.assertFalse(mn.modo_movil(encabezados={"viewport-width": "1440"}))
        # El encabezado propio del despliegue manda sobre los estándar
        self.assertEqual(mn.ancho_declarado({"x-nemet-viewport": "1024", "viewport-width": "375"}), 1024)

    def test_02_el_user_agent_respalda_cuando_no_hay_ancho(self):
        self.assertTrue(mn.modo_movil(encabezados={"user-agent": UA_IPHONE}))
        self.assertTrue(mn.modo_movil(user_agent=UA_ANDROID))
        self.assertFalse(mn.modo_movil(encabezados={"user-agent": UA_MAC}))
        self.assertFalse(mn.modo_movil(user_agent=UA_WINDOWS))
        # Sin ninguna pista se asume el diseño completo (escritorio)
        self.assertFalse(mn.modo_movil())
        self.assertFalse(mn.modo_movil(consulta={}, encabezados=None, user_agent=None))

    def test_03_umbral_del_breakpoint_y_valor_a_medida(self):
        for ancho in (320, 375, 390, 640, 768):
            self.assertTrue(mn.es_movil_por_ancho(ancho), f"{ancho} px debe ser móvil")
        for ancho in (769, 1024, 1440, 2560):
            self.assertFalse(mn.es_movil_por_ancho(ancho), f"{ancho} px no debe ser móvil")
        self.assertTrue(mn.es_movil_por_ancho("768px"), "el valor puede traer la unidad")
        # El umbral es configurable: una tablet de 900 px con umbral 1024 sí es móvil
        self.assertTrue(mn.modo_movil(ancho=900, breakpoint=1024))
        self.assertFalse(mn.modo_movil(ancho=900, breakpoint=600))

    def test_04_client_hints_del_navegador(self):
        self.assertTrue(mn.es_movil_por_encabezados({"sec-ch-ua-mobile": "?1"}))
        self.assertFalse(mn.es_movil_por_encabezados({"sec-ch-ua-mobile": "?0"}))
        self.assertIsNone(mn.es_movil_por_encabezados({"accept-language": "es-MX,es"}),
                          "sin pistas no se debe inventar una decisión")
        # El ancho declarado manda sobre el Client Hint (algunos navegadores lo reportan mal)
        self.assertTrue(mn.es_movil_por_encabezados({"sec-ch-ua-mobile": "?0", "sec-ch-viewport-width": "360"}))
        self.assertFalse(mn.es_movil_por_encabezados({"sec-ch-ua-mobile": "?1", "sec-ch-viewport-width": "1280"}))
        self.assertTrue(mn.modo_movil(encabezados={"sec-ch-ua-mobile": "?1"}))

    def test_05_el_forzado_por_url_gana_sobre_todo(self):
        self.assertEqual(mn.interpretar_consulta({"movil": "1"}), (True, True))
        self.assertEqual(mn.interpretar_consulta({"movil": "0"}), (True, False))
        self.assertEqual(mn.interpretar_consulta({"escritorio": "1"}), (True, False))
        self.assertEqual(mn.interpretar_consulta({"escritorio": "0"}), (True, True))
        self.assertEqual(mn.interpretar_consulta({}), (False, None))
        # Un parámetro sin valor cuenta como bandera encendida
        self.assertEqual(mn.interpretar_consulta({"movil": ""}), (True, True))
        self.assertEqual(mn.interpretar_consulta({"escritorio": ""}), (True, False))
        # ?movil=1 se impone a un escritorio con pantalla ancha…
        self.assertTrue(mn.modo_movil(consulta={"movil": "1"},
                                      encabezados={"user-agent": UA_MAC, "x-nemet-viewport": "1440"}))
        # …y ?escritorio=1 a un teléfono (útil para ver una tabla completa a propósito)
        self.assertFalse(mn.modo_movil(consulta={"escritorio": "1"},
                                       encabezados={"user-agent": UA_IPHONE, "x-nemet-viewport": "390"}))
        # Clave repetida en la URL: manda el último valor
        self.assertTrue(mn.modo_movil(consulta={"movil": ["0", "1"]}))

    def test_06_valores_raros_o_ausentes_no_revientan(self):
        self.assertEqual(mn.interpretar_consulta({"movil": "quizá"}), (False, None))
        self.assertFalse(mn.modo_movil(consulta={"movil": "quizá"}))
        # Un ancho imposible se ignora y deciden las demás pistas
        self.assertFalse(mn.modo_movil(encabezados={"x-nemet-viewport": "no-es-un-número"}))
        self.assertTrue(mn.modo_movil(ancho="no-es-un-número", encabezados={"user-agent": UA_IPHONE}))
        self.assertFalse(mn.es_movil_por_ancho(None))
        self.assertFalse(mn.es_movil_por_ancho("   "))
        self.assertIsNone(mn.ancho_declarado({"x-nemet-viewport": ""}))
        self.assertIsNone(mn._booleano(None))
        self.assertFalse(mn.es_movil(""), "sin user-agent no hay pista de móvil")

    def test_07_el_ancho_declarado_manda_sobre_el_user_agent(self):
        # Un iPhone conectado a un proyector lógico / ventana de escritorio ancha: manda el ancho
        self.assertFalse(mn.modo_movil(encabezados={"user-agent": UA_IPHONE, "x-nemet-viewport": "1280"}))
        # Una ventana angosta en una computadora sí recibe la vista táctil
        self.assertTrue(mn.modo_movil(encabezados={"user-agent": UA_MAC, "x-nemet-viewport": "360"}))

    def test_08_tabletas_tambien_son_pantalla_chica(self):
        self.assertTrue(mn.es_movil(UA_IPAD))
        self.assertTrue(mn.es_movil(UA_TABLETA_ANDROID), "Android sin 'Mobile' es tableta")
        self.assertTrue(mn.modo_movil(encabezados={"user-agent": UA_TABLETA_ANDROID}))
        # Pero si declara 1024 px (iPad en horizontal) se muestra el diseño completo
        self.assertFalse(mn.modo_movil(encabezados={"user-agent": UA_IPAD, "x-nemet-viewport": "1024px"}))

    def test_09_lectura_de_encabezados_y_unidades(self):
        self.assertEqual(mn.ancho_declarado({"viewport-width": " 375 "}), 375)
        self.assertEqual(mn.ancho_declarado({"sec-ch-viewport-width": "412px"}), 412)
        self.assertEqual(mn.ancho_declarado({"x-device-width": ["390", "412"]}), 412,
                         "con encabezado repetido manda el último valor")
        self.assertIsNone(mn.ancho_declarado({"viewport-width": "muy-chico"}))
        self.assertIsNone(mn.ancho_declarado({}))
        self.assertIsNone(mn.ancho_declarado(None))


# ============================================================ presentación
class TestPresentacion(unittest.TestCase):

    def test_10_la_hoja_de_estilos_es_tactil_y_responsiva(self):
        css = mn.estilos()
        self.assertIn(f"max-width: {mn.BREAKPOINT_PX}px", css, "el breakpoint debe regir la hoja")
        self.assertIn("44px", css, "el área táctil mínima debe estar declarada")
        self.assertIn("font-size: 16px", css, "los campos a 16 px evitan el zoom de iOS")
        self.assertIn("flex-wrap: wrap", css, "las columnas se envuelven en pantalla chica")
        self.assertIn("#F5EFE6", css, "el tema claro conserva el crema de marca")
        self.assertIn("#B4552D", css, "el acento barro sigue presente")
        self.assertNotIn("logo_oscuro", css, "el letterpress oscuro ya no existe en la app")
        self.assertIn("<style>", css)
        # El umbral es parametrizable (pruebas y despliegues a medida)
        self.assertIn("max-width: 600px", mn.estilos(breakpoint=600))

    def test_11_tema_claro_por_omision_y_nocturno_a_peticion(self):
        self.assertNotIn("#141210", mn.estilos())
        nocturno = mn.estilos(oscuro=True)
        self.assertIn("#141210", nocturno, "el tema nocturno oscurece el fondo")
        self.assertIn("#F5EFE6", nocturno, "y usa el crema de marca para el texto")
        self.assertIn("--gdg-bg-cell", nocturno, "las tablas de datos se tiñen con sus variables")

        self.assertEqual(mn.preferencia_tema({}), "claro")
        self.assertEqual(mn.preferencia_tema({"tema": "oscuro"}), "oscuro")
        self.assertEqual(mn.preferencia_tema({"tema": "NOCHE"}), "oscuro")
        self.assertEqual(mn.preferencia_tema({"tema": "dark"}), "oscuro")
        self.assertEqual(mn.preferencia_tema({"tema": "claro"}), "claro")
        self.assertEqual(mn.preferencia_tema({"tema": "morado"}), "claro", "un valor raro no rompe el tema")
        self.assertEqual(mn.preferencia_tema({"tema": ["oscuro", "claro"]}), "claro",
                         "con el parámetro repetido manda el último")


# ============================================================ la app real
@unittest.skipIf(AppTest is None, "Streamlit no está instalado")
class TestAplicacionEnLaApp(unittest.TestCase):
    """Corre la app de verdad sobre una **copia temporal** (su propio Excel y su base de
    usuarios), así que nunca toca los datos, los Secrets ni `nemet_usuarios.db` del repo."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app_dir = os.path.join(cls.tmp.name, "nemet")
        os.makedirs(os.path.join(cls.app_dir, "assets"), exist_ok=True)
        for nombre in ("app.py", "auth_nemet.py", "movil_nemet.py", "Sistema_Inventario_NEMET_Final.xlsx"):
            shutil.copy(os.path.join(BASE, nombre), os.path.join(cls.app_dir, nombre))
        for nombre in os.listdir(os.path.join(BASE, "assets")):
            shutil.copy(os.path.join(BASE, "assets", nombre), os.path.join(cls.app_dir, "assets", nombre))

        cls.ruta_db = os.path.join(cls.tmp.name, "usuarios.db")
        os.environ["NEMET_DB_USUARIOS"] = cls.ruta_db
        os.environ["NEMET_ADMIN_USUARIO"] = "jefe"
        os.environ["NEMET_ADMIN_PASSWORD"] = CLAVE_SEMILLA
        os.environ["NEMET_ADMIN_NOMBRE"] = "Jefe de Planta"
        conexion = auth.conectar(cls.ruta_db)
        auth.inicializar_db(conexion)
        auth.sembrar_admin_inicial(conexion, "jefe", CLAVE_SEMILLA, nombre="Jefe de Planta")
        admin = auth.obtener_por_login(conexion, "jefe")
        auth.cambiar_password_propia(conexion, admin["id"], CLAVE_SEMILLA, CLAVE_ADMIN)
        conexion.close()

    @classmethod
    def tearDownClass(cls):
        for variable in ("NEMET_DB_USUARIOS", "NEMET_ADMIN_USUARIO", "NEMET_ADMIN_PASSWORD",
                         "NEMET_ADMIN_NOMBRE"):
            os.environ.pop(variable, None)
        cls.tmp.cleanup()

    def entrar(self, movil=False, tema=None):
        """Abre la app real, inicia sesión como administrador y devuelve el AppTest."""
        at = AppTest.from_file(os.path.join(self.app_dir, "app.py"), default_timeout=90)
        if movil:
            at.query_params[mn.PARAM_MOVIL] = "1"
        if tema:
            at.query_params[mn.PARAM_TEMA] = tema
        at.run()
        self.assertFalse(at.exception, f"al abrir la app: {[e.value for e in at.exception]}")
        at.text_input(key="acceso_usuario").set_value("jefe")
        at.text_input(key="acceso_password").set_value(CLAVE_ADMIN)
        at.button(key="acceso_entrar").click()
        at.run()
        self.assertFalse(at.exception, f"al entrar: {[e.value for e in at.exception]}")
        return at

    def abrir(self, **parametros):
        at = AppTest.from_string(SONDA, default_timeout=90)
        for clave, valor in parametros.items():
            at.query_params[clave] = valor
        at.run()
        self.assertFalse(at.exception, f"la sonda falló: {[e.value for e in at.exception]}")
        return at

    def leer(self, at):
        textos = [str(m.value) for m in at.markdown]
        modo = next(t for t in textos if t.startswith("modo=")).split("=", 1)[1]
        tema = next(t for t in textos if t.startswith("tema=")).split("=", 1)[1]
        css = next(t for t in textos if "<style>" in t)
        return modo == "True", tema, css

    def test_12_la_app_real_entra_y_se_ve_completa_en_modo_movil(self):
        at = self.entrar(movil=True)
        self.assertEqual(at.title[0].value, "🧪 Sistema Maestro NEMET", "el dashboard debe abrir completo")
        etiquetas = [m.label for m in at.metric]
        for esperada in ("Total de SKUs Registrados", "Clientes Registrados",
                         "Valor Total Inventario ($)", "SKUs por Reabastecer"):
            self.assertIn(esperada, etiquetas, "el dashboard móvil conserva las cuatro métricas")
        css = " ".join(str(m.value) for m in at.markdown if "<style>" in str(m.value))
        self.assertIn(f"max-width: {mn.BREAKPOINT_PX}px", css, "la hoja móvil debe llegar a la app")
        self.assertTrue(any("Vista móvil" in c.value for c in at.sidebar.caption),
                        "el modo móvil debe quedar activo en la sesión")

        # La vista de escritorio no se contamina: la decisión es por sesión, no global
        escritorio = self.entrar(movil=False, tema="oscuro")
        self.assertTrue(any("Vista de escritorio" in c.value for c in escritorio.sidebar.caption))
        css_escritorio = " ".join(str(m.value) for m in escritorio.markdown if "<style>" in str(m.value))
        self.assertIn("#141210", css_escritorio, "?tema=oscuro debe teñir la sesión de escritorio")

    def test_13_el_forzado_manual_se_recuerda_en_la_sesion(self):
        at = AppTest.from_string(SONDA, default_timeout=90)
        at.query_params["movil"] = "1"
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(self.leer(at)[0])

        # Las recargas de Streamlit pierden la URL: el forzado se recuerda en la sesión
        del at.query_params["movil"]
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(self.leer(at)[0], "el modo móvil debe sobrevivir a las recargas")

        # Una orden explícita posterior sí cambia de vista
        at.query_params["escritorio"] = "1"
        at.run()
        self.assertFalse(self.leer(at)[0], "?escritorio=1 debe devolver el diseño completo")


# ============================================================ integración
class TestIntegracion(unittest.TestCase):

    def test_14_el_modulo_es_puro_y_no_toca_streamlit_al_importarse(self):
        with open(os.path.join(BASE, "movil_nemet.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(re.findall(r"^st\.\w+\(", src, re.MULTILINE), [],
                         "movil_nemet.py no debe llamar a Streamlit al importarse")
        # Las funciones de detección se pueden probar sin Streamlit corriendo
        for nombre in ("modo_movil", "es_movil", "es_movil_por_ancho", "es_movil_por_encabezados",
                       "ancho_declarado", "interpretar_consulta", "preferencia_tema", "_booleano", "_entero"):
            self.assertNotIn("st.", inspect.getsource(getattr(mn, nombre)),
                             f"{nombre} debe ser pura (sin Streamlit)")

    def test_15_integracion_en_la_app_y_documentacion(self):
        with open(os.path.join(BASE, "app.py"), encoding="utf-8") as fh:
            app = fh.read()
        for token in ("import movil_nemet", "MODO_MOVIL = movil_nemet.aplicar()", "movil_nemet.estilos(",
                      "movil_nemet.columnas_apiladas(", "movil_nemet.nota_sidebar(",
                      "not MODO_MOVIL and portada_marca()"):
            self.assertIn(token, app, f"falta {token} en app.py")

        # La configuración de página debe seguir siendo la primera llamada de Streamlit
        primera = re.search(r"^st\.(\w+)\s*\(", app, re.MULTILINE)
        self.assertEqual(primera.group(1), "set_page_config")

        # El modo se resuelve antes del gate de acceso: la pantalla de login ya se ve en el teléfono
        self.assertLess(app.index("MODO_MOVIL = movil_nemet.aplicar()"),
                        app.index("SESION = ejecutar_gate_acceso()"))
        self.assertNotIn("logo_oscuro.png", app, "la app solo usa los assets claros")

        with open(os.path.join(BASE, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for token in ("Modo móvil", "movil_nemet.py", "?movil=1", "tests_movil.py"):
            self.assertIn(token, readme, f"el README debe documentar {token}")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)
    n = resultado.testsRun
    if resultado.wasSuccessful() and n == 15:
        print(f"\n{n}/{n} OK — modo móvil NEMET verificado")
        sys.exit(0)
    print(f"\n{n - len(resultado.failures) - len(resultado.errors)}/{n} (esperadas 15/15)")
    sys.exit(1)
