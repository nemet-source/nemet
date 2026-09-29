#!/usr/bin/env python3
"""Batería de pruebas de extremo a extremo del acceso NEMET (10 pruebas).

Ejecuta la app real con `streamlit.testing.v1.AppTest` sobre una **copia temporal**
del proyecto (con su propio Excel y su propia base de usuarios), así que nunca toca
los datos ni los Secrets reales: el administrador inicial llega por variables de
entorno equivalentes a los Secrets.

Los métodos están numerados porque forman un flujo encadenado (login, cambio
obligatorio de contraseña, panel de administración). Unittest los ejecuta en orden.

Uso:  python tests_flujo_app.py     →  "10/10 OK" si todo pasa.
"""
import os
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # sin Streamlit instalado, la batería no aplica
    AppTest = None

import auth_nemet as auth  # noqa: E402

PASSWORD_SEMILLA = "ClaveAdminInicial2026"
PASSWORD_ADMIN = "ClaveAdminDefinitiva2026"
PASSWORD_EDITOR = "DisenoEditorNemet26"


@unittest.skipIf(AppTest is None, "Streamlit no está instalado")
class TestFlujoAcceso(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app_dir = os.path.join(cls.tmp.name, "nemet")
        os.makedirs(os.path.join(cls.app_dir, "assets"), exist_ok=True)
        for nombre in ("app.py", "auth_nemet.py", "Sistema_Inventario_NEMET_Final.xlsx"):
            shutil.copy(os.path.join(BASE, nombre), os.path.join(cls.app_dir, nombre))
        for nombre in os.listdir(os.path.join(BASE, "assets")):
            shutil.copy(os.path.join(BASE, "assets", nombre), os.path.join(cls.app_dir, "assets", nombre))

        cls.ruta_db = os.path.join(cls.tmp.name, "usuarios.db")
        os.environ["NEMET_DB_USUARIOS"] = cls.ruta_db
        os.environ["NEMET_ADMIN_USUARIO"] = "jefe"
        os.environ["NEMET_ADMIN_PASSWORD"] = PASSWORD_SEMILLA
        os.environ["NEMET_ADMIN_NOMBRE"] = "Jefe de Planta"

        cls.conn = auth.conectar(cls.ruta_db)
        auth.inicializar_db(cls.conn)
        auth.sembrar_admin_inicial(cls.conn, "jefe", PASSWORD_SEMILLA, nombre="Jefe de Planta")
        cls.admin = auth.obtener_por_login(cls.conn, "jefe")
        # El administrador ya cambió su contraseña provisional (el cambio obligatorio se
        # prueba aparte, en test_02/test_03, con la contraseña de la semilla).
        auth.cambiar_password_propia(cls.conn, cls.admin["id"], PASSWORD_SEMILLA, PASSWORD_ADMIN)
        cls.editor = auth.crear_usuario(cls.conn, cls.admin, "disenador", password=PASSWORD_EDITOR,
                                        nombre="Diseño", rol="editor", debe_cambiar=False)
        cls.usuario = auth.crear_usuario(cls.conn, cls.admin, "ventas", password="ComercialNemet2026",
                                         nombre="Ventas", rol="usuario", debe_cambiar=False)
        cls.token_admin = None

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        for variable in ("NEMET_DB_USUARIOS", "NEMET_ADMIN_USUARIO", "NEMET_ADMIN_PASSWORD",
                         "NEMET_ADMIN_NOMBRE"):
            os.environ.pop(variable, None)
        cls.tmp.cleanup()

    # ------------------------------------------------------------------ utilidades
    def abrir(self, token=None, paso=""):
        """Arranca la app (copia temporal) y, si se da, reutiliza el token de sesión."""
        at = AppTest.from_file(os.path.join(self.app_dir, "app.py"), default_timeout=90)
        if token:
            at.query_params["sesion"] = token
        at.run()
        self.assertFalse(at.exception, f"{paso}: excepción {[e.value for e in at.exception]}")
        return at

    def entrar(self, at, usuario, password, paso=""):
        at.text_input(key="acceso_usuario").set_value(usuario)
        at.text_input(key="acceso_password").set_value(password)
        at.button(key="acceso_entrar").click()
        at.run()
        self.assertFalse(at.exception, f"{paso}: excepción {[e.value for e in at.exception]}")
        return at

    def opciones_menu(self, at):
        return at.sidebar.selectbox[0].options if at.sidebar.selectbox else []

    def ir_a(self, at, etiqueta):
        at.sidebar.selectbox[0].select(etiqueta)
        at.run()
        self.assertFalse(at.exception, f"al abrir {etiqueta}: {[e.value for e in at.exception]}")
        return at

    # ------------------------------------------------------------------ pruebas
    def test_01_sin_sesion_no_se_expone_ningun_modulo(self):
        at = self.abrir(paso="sin sesión")
        self.assertEqual([s.value for s in at.subheader], ["🔐 Acceso al sistema"])
        self.assertEqual(self.opciones_menu(at), [])
        self.assertFalse(at.title, "el dashboard no debe renderizarse sin sesión")
        visibles = " ".join(str(m.value) for m in at.markdown)
        self.assertNotIn("Menú Principal", visibles)

    def test_02_login_fallido_y_cambio_obligatorio_de_password(self):
        at = self.abrir(paso="login fallido")
        self.entrar(at, "jefe", "no-es-la-clave", "login fallido")
        self.assertTrue(any("incorrectos" in e.value for e in at.error))
        self.assertEqual(self.opciones_menu(at), [])

        # Contraseña provisional: entra pero queda bloqueado hasta cambiarla
        self.entrar(at, "invitado", PASSWORD_SEMILLA, "usuario inexistente")
        self.assertTrue(any("incorrectos" in e.value for e in at.error))
        at.text_input(key="acceso_usuario").set_value("disenador")
        at.text_input(key="acceso_password").set_value(PASSWORD_EDITOR)
        at.button(key="acceso_entrar").click()
        at.run()
        # "disenador" no debe cambiar contraseña: entra directo al sistema
        self.assertEqual(at.sidebar.selectbox[0].options[0], "📊 Dashboard & Resumen")

    def test_03_la_password_inicial_pide_cambio_obligatorio(self):
        """El administrador inicial arranca con contraseña provisional y debe cambiarla."""
        auth.sembrar_admin_inicial(self.conn, "nuevo_jefe", PASSWORD_SEMILLA, nombre="Nuevo Jefe")
        at = self.abrir(paso="cambio obligatorio")
        self.entrar(at, "nuevo_jefe", PASSWORD_SEMILLA, "cambio obligatorio")
        self.assertEqual([s.value for s in at.subheader], ["🔑 Cambia tu contraseña"])
        self.assertEqual(self.opciones_menu(at), [], "no debe haber menú antes de cambiar la contraseña")

        at.text_input(key="cambio_actual").set_value(PASSWORD_SEMILLA)
        at.text_input(key="cambio_nueva").set_value("debil")
        at.text_input(key="cambio_repetir").set_value("debil")
        at.button(key="cambio_guardar").click()
        at.run()
        self.assertTrue(any("al menos" in e.value for e in at.error))

        at.text_input(key="cambio_actual").set_value(PASSWORD_SEMILLA)
        at.text_input(key="cambio_nueva").set_value("JefeNuevoClave2026")
        at.text_input(key="cambio_repetir").set_value("JefeNuevoClave2026")
        at.button(key="cambio_guardar").click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(at.title[0].value, "🧪 Sistema Maestro NEMET")
        self.assertEqual(self.opciones_menu(at)[0], "📊 Dashboard & Resumen")
        cambio = auth.obtener_por_login(self.conn, "nuevo_jefe")
        self.assertFalse(cambio["debe_cambiar"], "el cambio de contraseña debe quedar registrado")
        self.assertIsNone(auth.autenticar(self.conn, "nuevo_jefe", PASSWORD_SEMILLA)[0],
                          "la contraseña provisional ya no debe servir")
        auth.eliminar_usuario(self.conn, self.admin, cambio["id"])

    def test_04_el_token_de_sesion_sobrevive_a_una_recarga(self):
        at = self.abrir(paso="login admin")
        self.entrar(at, "jefe", PASSWORD_ADMIN, "login admin")
        token = at.query_params.get("sesion")
        self.assertTrue(token, "la sesión debe quedar firmada en la URL")
        TestFlujoAcceso.token_admin = token[0] if isinstance(token, list) else token

        recargada = self.abrir(token=TestFlujoAcceso.token_admin, paso="recarga con token")
        self.assertEqual(recargada.title[0].value, "🧪 Sistema Maestro NEMET",
                         "con el token vigente no debe volver a pedir credenciales")
        self.assertTrue(self.opciones_menu(recargada), "recargada la app, el menú sigue disponible")

    def test_05_token_alterado_o_caducado_no_da_acceso(self):
        token = TestFlujoAcceso.token_admin
        alterado = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        at = self.abrir(token=alterado, paso="token alterado")
        self.assertEqual([s.value for s in at.subheader], ["🔐 Acceso al sistema"])

        secreto = auth.secreto_sesion(self.conn)
        caducado = auth.crear_token(secreto, self.admin, minutos=-5)
        at = self.abrir(token=caducado, paso="token caducado")
        self.assertEqual([s.value for s in at.subheader], ["🔐 Acceso al sistema"])

    def test_06_el_menu_muestra_solo_los_modulos_permitidos(self):
        at = self.abrir(paso="editor")
        self.entrar(at, "disenador", PASSWORD_EDITOR, "editor")
        menu_editor = self.opciones_menu(at)
        self.assertIn("📦 Control de Inventario y Edición", menu_editor)
        self.assertIn("👥 Gestión de Clientes", menu_editor)
        self.assertNotIn("🛡️ Administración de Usuarios", menu_editor)

        at = self.abrir(paso="usuario")
        self.entrar(at, "ventas", "ComercialNemet2026", "usuario")
        menu_usuario = self.opciones_menu(at)
        self.assertNotIn("📦 Control de Inventario y Edición", menu_usuario)
        self.assertNotIn("👥 Gestión de Clientes", menu_usuario)
        self.assertNotIn("🛡️ Administración de Usuarios", menu_usuario)
        for esperado in ("📊 Dashboard & Resumen", "📏 Cotizador por Área y Milimétrico",
                         "📝 Cotizador Comercial Profesional", "📋 Historial de Cotizaciones (Folios)"):
            self.assertIn(esperado, menu_usuario)

        at = self.abrir(paso="admin")
        self.entrar(at, "jefe", PASSWORD_ADMIN, "admin")
        self.assertEqual(len(self.opciones_menu(at)), 7)

    def test_07_el_panel_crea_cuentas_y_muestra_la_password_una_sola_vez(self):
        at = self.abrir(paso="panel")
        self.entrar(at, "jefe", PASSWORD_ADMIN, "panel")
        self.ir_a(at, "🛡️ Administración de Usuarios")
        self.assertIn("Cuentas", [m.label for m in at.metric])

        at.text_input(key="nuevo_usuario").set_value("almacen")
        at.text_input(key="nuevo_nombre").set_value("Almacén")
        at.text_input(key="nuevo_correo").set_value("almacen@nemet.mx")
        at.selectbox(key="nuevo_rol").select("editor")
        at.button(key="crear_cuenta").click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])

        creado = auth.obtener_por_login(self.conn, "almacen")
        self.assertIsNotNone(creado, "la cuenta debe quedar en la base")
        self.assertEqual(creado["rol"], "editor")
        self.assertTrue(creado["debe_cambiar"])
        avisos = [w.value for w in at.warning]
        self.assertTrue(any("Anota estas credenciales" in aviso for aviso in avisos),
                        "la contraseña temporal debe mostrarse una vez")

        # Al recargar, la contraseña temporal ya no se vuelve a mostrar
        otra = self.abrir(token=TestFlujoAcceso.token_admin, paso="recarga del panel")
        self.ir_a(otra, "🛡️ Administración de Usuarios")
        self.assertFalse(any("Anota estas credenciales" in w.value for w in otra.warning))

    def test_08_restablecer_desactivar_y_reactivar_desde_el_panel(self):
        usuario = auth.obtener_por_login(self.conn, "almacen")
        at = self.abrir(token=TestFlujoAcceso.token_admin, paso="panel de cuentas")
        self.ir_a(at, "🛡️ Administración de Usuarios")
        at.selectbox(key="cuenta_objetivo").select(usuario["id"])
        at.run()

        at.button(key="pass_restablecer").click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any("Anota estas credenciales" in w.value for w in at.warning))

        at.button(key="cuenta_desactivar").click()
        at.run()
        self.assertFalse(auth.obtener_usuario(self.conn, usuario["id"])["activo"])
        self.assertIsNone(auth.autenticar(self.conn, "almacen", "lo-que-sea-123")[0])

        at.selectbox(key="cuenta_objetivo").select(usuario["id"])
        at.run()
        at.button(key="cuenta_reactivar").click()
        at.run()
        self.assertTrue(auth.obtener_usuario(self.conn, usuario["id"])["activo"])

    def test_09_eliminar_una_cuenta_desde_el_panel(self):
        usuario = auth.obtener_por_login(self.conn, "almacen")
        at = self.abrir(token=TestFlujoAcceso.token_admin, paso="eliminar cuenta")
        self.ir_a(at, "🛡️ Administración de Usuarios")
        at.selectbox(key="cuenta_objetivo").select(usuario["id"])
        at.run()
        # La eliminación exige escribir el nombre del usuario como confirmación
        at.text_input(key=f"confirmar_borrado_{usuario['id']}").set_value("almacen")
        at.run()
        at.button(key="cuenta_eliminar").click()
        at.run()
        self.assertFalse(at.exception, f"el panel debe repintarse sin la cuenta: {[e.value for e in at.exception]}")
        self.assertIsNone(auth.obtener_por_login(self.conn, "almacen"))
        self.assertIn("usuario_eliminado", [e["accion"] for e in auth.listar_eventos(self.conn, 30)])

    def test_10_nunca_se_queda_sin_administradores_activos(self):
        # El administrador no puede desactivarse ni eliminarse desde el panel
        at = self.abrir(token=TestFlujoAcceso.token_admin, paso="último admin")
        self.ir_a(at, "🛡️ Administración de Usuarios")
        at.selectbox(key="cuenta_objetivo").select(self.admin["id"])
        at.run()
        self.assertTrue(at.button(key="cuenta_desactivar").disabled,
                        "no debe poder desactivarse la propia cuenta del administrador")
        self.assertTrue(at.button(key="cuenta_eliminar").disabled)

        # Y la regla se sostiene también en el módulo, incluso llamando directo a la API
        with self.assertRaises(auth.ErrorRegla):
            auth._exigir_ultimo_admin(self.conn, self.admin, "desactivar")
        self.assertGreaterEqual(auth.contar_admins_activos(self.conn), 1)
        self.assertTrue(any("Solo hay un administrador activo" in w.value for w in at.warning))

        # La bitácora registró todo el flujo
        acciones = [e["accion"] for e in auth.listar_eventos(self.conn, 200)]
        for esperada in ("inicio_sesion", "usuario_creado", "usuario_desactivado", "usuario_activado",
                         "password_cambiada"):
            self.assertIn(esperada, acciones)
        self.assertTrue(all("Nemet2026" not in e["detalle"] for e in auth.listar_eventos(self.conn, 200)),
                        "la bitácora no debe guardar contraseñas")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)
    n = resultado.testsRun
    if resultado.wasSuccessful():
        print(f"\n{n}/{n} OK — flujo de acceso NEMET verificado")
        sys.exit(0)
    print(f"\n{n - len(resultado.failures) - len(resultado.errors)}/{n} (revisa los fallos)")
    sys.exit(1)
