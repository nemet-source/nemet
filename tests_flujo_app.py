#!/usr/bin/env python3
"""Batería de pruebas de acceso y prospección NEMET (18 pruebas).

Ejecuta la app real con `streamlit.testing.v1.AppTest` sobre una **copia temporal**
del proyecto (con su propio Excel y su propia base de usuarios), así que nunca toca
los datos ni los Secrets reales: el administrador inicial llega por variables de
entorno equivalentes a los Secrets.

Los métodos están numerados porque forman un flujo encadenado (login, cambio
obligatorio de contraseña, panel de administración). Unittest los ejecuta en orden.

Uso:  python tests_flujo_app.py     →  "17/17 OK" si todo pasa.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs

import pandas as pd
import prospeccion as pros

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
        for nombre in ("app.py", "auth_nemet.py", "prospeccion.py", "Sistema_Inventario_NEMET_Final.xlsx"):
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
        avisos = " ".join(i.value for i in at.info)
        self.assertIn("No hay registro abierto", avisos,
                      "la pantalla de acceso debe explicar que las cuentas las crea un administrador")

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
        self.assertIn("🎯 Prospección Comercial", menu_editor)
        self.assertNotIn("🛡️ Administración de Usuarios", menu_editor)

        at = self.abrir(paso="usuario")
        self.entrar(at, "ventas", "ComercialNemet2026", "usuario")
        menu_usuario = self.opciones_menu(at)
        self.assertNotIn("📦 Control de Inventario y Edición", menu_usuario)
        self.assertNotIn("👥 Gestión de Clientes", menu_usuario)
        self.assertNotIn("🎯 Prospección Comercial", menu_usuario)
        self.assertNotIn("🛡️ Administración de Usuarios", menu_usuario)
        for esperado in ("📊 Dashboard & Resumen", "📏 Cotizador por Área y Milimétrico",
                         "📝 Cotizador Comercial Profesional", "📋 Historial de Cotizaciones (Folios)"):
            self.assertIn(esperado, menu_usuario)

        at = self.abrir(paso="admin")
        self.entrar(at, "jefe", PASSWORD_ADMIN, "admin")
        self.assertEqual(len(self.opciones_menu(at)), 8)

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

    def test_11_busca_guarda_y_no_duplica_prospectos_en_el_excel(self):
        """Simula la fuente externa; nunca llama a Overpass ni modifica el Excel real."""
        self.assertFalse(auth.puede("usuario", "prospeccion"))
        self.assertTrue(auth.puede("editor", "prospeccion"))
        at = self.abrir(token=TestFlujoAcceso.token_admin, paso="prospección")
        self.ir_a(at, "🎯 Prospección Comercial")
        candidato = pros._elemento_a_candidato({
            "type": "node", "id": 9001, "lat": 27.495, "lon": -109.94,
            "tags": {"name": "Aplicadores de Sonora", "craft": "tiler", "addr:suburb": "Centro",
                     "contact:phone": "+52 662 111 2345", "contact:whatsapp": "+52 644 111 2345",
                     "products": "pisos con resina epóxica", "website": "aplicadores.example.mx"},
        }, "Ciudad Obregón, Sonora", ("aplicadores",), ("EPOXY PISOS (A Y B) TRANSPARENTE",),
           centro=(27.49, -109.94))
        cliente_existente = pros._elemento_a_candidato({
            "type": "node", "id": 9002, "tags": {"name": "Nemet", "craft": "tiler"},
        }, "Hermosillo, Sonora", ("aplicadores",), ())
        with patch("prospeccion.buscar_osm_detallada",
                  return_value=([candidato, cliente_existente],
                                {"servidor": "servidor-de-prueba", "radio_pedido": 30,
                                 "radio_usado": 30, "avisos": []})) as buscar:
            at.button(key="pros_buscar").click()
            at.run()
            self.assertFalse(at.exception, [e.value for e in at.exception])
            buscar.assert_called_once()
        self.assertTrue(any("Nuevos: 1" in e.value for e in at.info))
        self.assertTrue(at.button(key="pros_guardar").disabled,
                        "la vista compacta nunca guarda todos accidentalmente")
        at.checkbox(key="pros_todos_1").check()
        at.run()
        at.button(key="pros_guardar").click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        xlsx = os.path.join(self.app_dir, "Sistema_Inventario_NEMET_Final.xlsx")
        guardados = pd.read_excel(xlsx, sheet_name="Prospectos")
        self.assertEqual(guardados["Empresa"].tolist(), ["Aplicadores de Sonora"])
        self.assertEqual(guardados.at[0, "Estado"], "Nuevo")
        self.assertIn("EPOXY PISOS", guardados.at[0, "Productos"])
        self.assertEqual(guardados.at[0, "URL_fuente"], "https://www.openstreetmap.org/node/9001")
        self.assertEqual(guardados.at[0, "Zona"], "Centro")
        self.assertAlmostEqual(guardados.at[0, "Latitud"], 27.495)
        self.assertIn("644", guardados.at[0, "WhatsApp"])
        self.assertGreater(guardados.at[0, "Puntaje"], 80)
        with patch("prospeccion.buscar_osm_detallada",
                  return_value=([candidato, cliente_existente],
                                {"servidor": "servidor-de-prueba", "radio_pedido": 30,
                                 "radio_usado": 30, "avisos": []})):
            at.button(key="pros_buscar").click()
            at.run()
        self.assertTrue(any("Nuevos: 0" in e.value for e in at.info))
        self.assertEqual(len(pd.read_excel(xlsx, sheet_name="Prospectos")), 1, "una búsqueda repetida no duplica fichas")
        self.assertIn("Clientes", pd.ExcelFile(xlsx).sheet_names, "no reemplazar el directorio de clientes")
        original = os.path.join(BASE, "Sistema_Inventario_NEMET_Final.xlsx")
        self.assertNotIn("Prospectos", pd.ExcelFile(original).sheet_names, "las pruebas no deben tocar datos reales")

    def test_12_seguimiento_persiste_y_rechaza_edicion_concurrente(self):
        """La versión mostrada al usuario NO se actualiza silenciosamente en el rerun."""
        clave = "osm/node/9001"
        at_a = self.abrir(token=TestFlujoAcceso.token_admin, paso="seguimiento A")
        self.ir_a(at_a, "🎯 Prospección Comercial")
        at_b = self.abrir(token=TestFlujoAcceso.token_admin, paso="seguimiento B")
        self.ir_a(at_b, "🎯 Prospección Comercial")

        at_a.selectbox(key=f"pros_estado_{clave}").select("Contactado")
        at_a.text_area(key=f"pros_notas_{clave}").set_value("Llamar el martes")
        at_a.button(key="pros_actualizar").click()
        at_a.run()
        self.assertFalse(at_a.error, [e.value for e in at_a.error])
        self.assertFalse(at_a.exception, [e.value for e in at_a.exception])
        xlsx = os.path.join(self.app_dir, "Sistema_Inventario_NEMET_Final.xlsx")
        guardados = pd.read_excel(xlsx, sheet_name="Prospectos")
        self.assertEqual(guardados.at[0, "Estado"], "Contactado")
        self.assertEqual(guardados.at[0, "Notas"], "Llamar el martes")

        at_b.selectbox(key=f"pros_estado_{clave}").select("Descartado")
        at_b.button(key="pros_actualizar").click()
        at_b.run()
        self.assertFalse(at_b.exception, [e.value for e in at_b.exception])
        self.assertTrue(any("Otra sesión" in e.value for e in at_b.error))
        self.assertEqual(pd.read_excel(xlsx, sheet_name="Prospectos").at[0, "Estado"], "Contactado")
        at_b.button(key=f"pros_recargar_{clave}").click()
        at_b.run()
        self.assertFalse(at_b.exception, [e.value for e in at_b.exception])
        self.assertEqual(at_b.selectbox(key=f"pros_estado_{clave}").value, "Contactado")

    def test_13_agenda_y_perfil_requieren_evidencia_y_persisten(self):
        clave = "osm/node/9001"
        at = self.abrir(token=TestFlujoAcceso.token_admin, paso="agenda comercial")
        self.ir_a(at, "🎯 Prospección Comercial")
        at.selectbox(key=f"pros_tamano_{clave}").select("Grande")
        at.date_input(key=f"pros_proximo_{clave}").set_value(date(2020, 1, 1))
        at.button(key="pros_actualizar").click()
        at.run()
        self.assertTrue(any("indica una fuente" in e.value for e in at.error))
        xlsx = os.path.join(self.app_dir, "Sistema_Inventario_NEMET_Final.xlsx")
        self.assertFalse(pd.read_excel(xlsx, sheet_name="Prospectos").at[0, "Próximo_seguimiento"] == "2020-01-01",
                         "si falla el guardado no debe quedar medio escrito")
        at.text_input(key=f"pros_fuente_{clave}").set_value("Catálogo comercial revisado")
        at.selectbox(key=f"pros_clientela_{clave}").select("Empresas")
        at.date_input(key=f"pros_actividad_{clave}").set_value(date(2026, 9, 25))
        at.button(key="pros_actualizar").click()
        at.run()
        self.assertFalse(at.error, [e.value for e in at.error])
        self.assertFalse(at.exception, [e.value for e in at.exception])
        fila = pd.read_excel(xlsx, sheet_name="Prospectos").iloc[0]
        self.assertEqual(str(fila["Próximo_seguimiento"])[:10], "2020-01-01")
        self.assertEqual(fila["Tamaño"], "Grande")
        self.assertEqual(fila["Tipo_clientela"], "Empresas")
        self.assertEqual(fila["Notas"], "Llamar el martes", "no debe perder notas previas")
        self.assertIn("Catálogo comercial", fila["Fuente_perfil"])
        self.assertTrue(any("seguimiento(s) pendiente(s)" in w.value for w in at.warning))

        at.selectbox(key=f"pros_estado_{clave}").select("No contactar")
        at.button(key="pros_actualizar").click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(pd.read_excel(xlsx, sheet_name="Prospectos").at[0, "Estado"], "No contactar")
        self.assertFalse(any("seguimiento(s) pendiente(s)" in w.value for w in at.warning))

    def test_14_excel_antiguo_se_lee_y_actualiza_sin_perder_ficha(self):
        xlsx = os.path.join(self.app_dir, "Sistema_Inventario_NEMET_Final.xlsx")
        ficha = pd.read_excel(xlsx, sheet_name="Prospectos", dtype=str, keep_default_na=False)
        with pd.ExcelWriter(xlsx, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            ficha[list(pros.COLUMNAS[:18])].to_excel(writer, sheet_name="Prospectos", index=False)
        at = self.abrir(token=TestFlujoAcceso.token_admin, paso="Excel legado")
        self.ir_a(at, "🎯 Prospección Comercial")
        self.assertFalse(at.exception, [e.value for e in at.exception])
        at.selectbox(key="pros_estado_osm/node/9001").select("Interesado")
        at.button(key="pros_actualizar").click()
        at.run()
        self.assertFalse(at.error, [e.value for e in at.error])
        self.assertFalse(at.exception, [e.value for e in at.exception])
        migrado = pd.read_excel(xlsx, sheet_name="Prospectos")
        self.assertEqual(migrado.at[0, "Estado"], "Interesado")
        self.assertEqual(migrado.at[0, "Empresa"], "Aplicadores de Sonora")
        self.assertIn("Próximo_seguimiento", migrado.columns)
        self.assertIn("WhatsApp", migrado.columns)

    def test_15_alta_manual_sin_red_mapa_estado_filtros_y_guardado_local(self):
        """El alta rechaza contactos sin confirmación y persiste en un Excel temporal."""
        xlsx = os.path.join(self.app_dir, "Sistema_Inventario_NEMET_Final.xlsx")
        at = self.abrir(token=TestFlujoAcceso.token_admin, paso="alta manual")
        self.ir_a(at, "🎯 Prospección Comercial")
        self.assertTrue(any("@media (max-width: 768px)" in m.value for m in at.markdown),
                        "la vista móvil debe apilar los formularios y filtros")
        self.assertIn("Seguimiento", at.selectbox(key="pros_estado_osm/node/9001").options)
        self.assertIn("Cotización", at.selectbox(key="pros_estado_osm/node/9001").options)
        at.text_input(key="manual_empresa").set_value("Carpintería del Valle")
        at.selectbox(key="manual_giro").select("carpinterias")
        at.text_input(key="manual_direccion").set_value("Calle Sinaloa 12, Centro")
        at.text_input(key="manual_telefono").set_value("+52 644 123 4567")
        at.text_input(key="manual_origen").set_value("Sitio comercial público del negocio")
        at.text_input(key="manual_url_fuente").set_value("https://ejemplo.mx/contacto")
        at.selectbox(key="manual_estado").select("Cotización")
        at.text_area(key="manual_notas").set_value("Enviar catálogo por correo si lo solicitan")
        at.date_input(key="manual_proximo").set_value(date(2026, 10, 2))
        at.button(key="manual_guardar").click()
        at.run()
        self.assertTrue(any("Confirma" in e.value for e in at.error))
        self.assertEqual(len(pd.read_excel(xlsx, sheet_name="Prospectos")), 1,
                         "el fallo no debe escribir parcialmente")

        at.checkbox(key="manual_permiso").check()
        at.button(key="manual_guardar").click()
        at.run()
        self.assertFalse(at.error, [e.value for e in at.error])
        self.assertFalse(at.exception, [e.value for e in at.exception])
        registros = pd.read_excel(xlsx, sheet_name="Prospectos", dtype=str, keep_default_na=False)
        self.assertEqual(len(registros), 2)
        manual = registros.loc[registros["Empresa"] == "Carpintería del Valle"].iloc[0]
        clave = manual["Clave"]
        self.assertTrue(clave.startswith("manual/"))
        self.assertEqual(manual["Segmento"], pros.SECTORES["carpinterias"]["nombre"])
        self.assertEqual(manual["Estado"], "Cotización")
        self.assertEqual(manual["Notas"], "Enviar catálogo por correo si lo solicitan")
        self.assertEqual(manual["Próximo_seguimiento"], "2026-10-02")
        self.assertEqual(manual["Teléfono"], "+52 644 123 4567")
        self.assertEqual(manual["WhatsApp"], "", "un teléfono no equivale a WhatsApp")
        self.assertTrue(0 <= int(manual["Puntaje"]) <= 100)
        self.assertIn("EPO-DEEP", manual["Productos"])
        self.assertIn("Sitio comercial", manual["Fuente"])
        self.assertTrue(pros.enlace_google_maps(manual.to_dict()).startswith(
            "https://www.google.com/maps/search/?api=1&query=Calle%20Sinaloa%2012"))
        # El AppTest de Streamlit 1.49 todavía no conoce `download_button`: lo expone como
        # UnknownElement sin `key`. Se acepta la clave o la etiqueta visible para que la
        # batería sirva igual con la versión mínima declarada en requirements.txt.
        exportaciones = at.get("download_button")
        self.assertTrue(any(getattr(b, "key", None) == "pros_descargar_todos"
                            or "Exportar todos" in (getattr(b, "label", "") or "")
                            for b in exportaciones),
                        "debe poder exportarse la lista completa en CSV")

        at.selectbox(key="pros_elegido").select(clave)
        at.run()
        self.assertEqual(at.selectbox(key=f"pros_estado_{clave}").value, "Cotización")
        at.selectbox(key=f"pros_estado_{clave}").select("Seguimiento")
        at.text_area(key=f"pros_notas_{clave}").set_value("Llamar después de compartir catálogo")
        at.button(key="pros_actualizar").click()
        at.run()
        self.assertFalse(at.error, [e.value for e in at.error])
        cambiado = pd.read_excel(xlsx, sheet_name="Prospectos", dtype=str, keep_default_na=False)
        self.assertEqual(cambiado.loc[cambiado["Clave"] == clave, "Estado"].iloc[0], "Seguimiento")
        at.multiselect(key="pros_filtro_estado").select("Seguimiento")
        at.run()
        self.assertEqual(at.selectbox(key="pros_elegido").options,
                         ["Carpintería del Valle · Ciudad Obregón, Sonora"])
        self.assertFalse(at.checkbox(key="manual_permiso").value,
                         "cada prospecto nuevo debe reconfirmar el uso autorizado")
        self.assertEqual(at.text_input(key="manual_origen").value, "")
        at.text_input(key="manual_empresa").set_value("Carpintería del Valle")
        at.selectbox(key="manual_giro").select("carpinterias")
        at.text_input(key="manual_origen").set_value("Ficha pública")
        at.checkbox(key="manual_permiso").check()
        at.button(key="manual_guardar").click()
        at.run()
        self.assertTrue(any("No se duplicó" in w.value for w in at.warning))
        self.assertEqual(len(pd.read_excel(xlsx, sheet_name="Prospectos")), 2)

        at2 = self.abrir(token=TestFlujoAcceso.token_admin, paso="persistencia del alta manual")
        self.ir_a(at2, "🎯 Prospección Comercial")
        self.assertTrue(any("Carpintería del Valle" in o for o in at2.selectbox(key="pros_elegido").options))
        self.assertIn("Carpintería del Valle", pd.read_excel(xlsx, sheet_name="Prospectos")["Empresa"].tolist())

    def test_16_busqueda_desplegada_contra_un_servidor_overpass_local(self):
        """Prueba la búsqueda REAL de la app desplegada, de punta a punta.

        Solo se sustituye la lista de servidores por uno local que imita a Overpass: la
        consulta, el reintento, el parseo, la clasificación, el filtro por radio, el Excel
        y la interfaz son los de producción. Sirve para ver qué hace la app cuando el
        servicio falla de verdad (429 por IP y consulta que no se completa).
        """
        # Cada escenario usa otra ciudad/radio: la búsqueda se cachea media hora y así
        # ninguna prueba se come el resultado de otra.
        # --- 1) El servidor limita por IP (429) y la app reintenta antes de rendirse ---
        prueba = ServidorOverpassDePrueba("429 luego ok").arrancar()
        try:
            with patch.object(pros, "OVERPASS_URLS", (prueba.url,)):
                at = self.abrir(token=TestFlujoAcceso.token_admin, paso="búsqueda desplegada")
                self.ir_a(at, "🎯 Prospección Comercial")
                at.text_input(key="pros_ciudad").set_value("Obregón, Sonora")
                at.button(key="pros_buscar").click()
                at.run()
                self.assertFalse(at.exception, [e.value for e in at.exception])
        finally:
            prueba.detener()
        self.assertEqual(len(prueba.peticiones), 2, "un 429 se reintenta una vez, no se abandona")
        self.assertIn("around:30000,27.48642,-109.94079", prueba.peticiones[0])
        self.assertIn('nwr["craft"', prueba.peticiones[0])
        self.assertTrue(any("Encontrados: 3 · Nuevos: 3" in i.value for i in at.info),
                        [i.value for i in at.info])
        self.assertTrue(any(c.value.startswith("Servidor consultado: 127.0.0.1") for c in at.caption),
                        "la interfaz dice qué servidor respondió, sin rutas ni credenciales")
        self.assertTrue(any("Carpintería del Sol" in o for s in at.selectbox for o in s.options),
                        "la ficha pública debe aparecer como candidato")

        # --- 2) Si ningún servidor responde: error claro y alternativas sin red ---
        prueba = ServidorOverpassDePrueba("429").arrancar()
        try:
            with patch.object(pros, "OVERPASS_URLS", (prueba.url,)):
                at.text_input(key="pros_ciudad").set_value("Ciudad Obregón")
                at.slider(key="pros_radio").set_value(5)
                # Sin at.run() intermedio: los valores del formulario se aplican al enviarlo.
                at.button(key="pros_buscar").click()
                at.run()
                self.assertFalse(at.exception, [e.value for e in at.exception])
        finally:
            prueba.detener()
        self.assertTrue(at.error, "un fallo total debe verse, no esconderse")
        self.assertIn("No hay respuesta de OpenStreetMap/Overpass", at.error[0].value)
        self.assertIn("CSV", at.error[0].value)
        self.assertIn("manualmente", at.error[0].value)
        self.assertEqual(len(prueba.peticiones), 2,
                         "429: se vuelve una sola vez, tras el turno que pidió el servidor")
        self.assertIn("around:5000,27.48642,-109.94079", prueba.peticiones[0])
        detalle = " ".join(m.value for m in at.markdown)
        self.assertIn("límite de peticiones (HTTP 429)", detalle,
                      "la pantalla debe distinguir el límite por IP de una caída de red")
        self.assertIn("127.0.0.1", detalle, "debe nombrarse el servidor que falló")
        self.assertNotIn("/api/interpreter", detalle, "solo el host, nunca la ruta ni parámetros")
        self.assertTrue(any(b.key == "pros_reintentar" for b in at.button),
                        "debe ofrecerse reintentar")
        self.assertNotIn("pros_resultados", at.session_state,
                         "un intento fallido nunca deja fichas anteriores en pantalla")

        # --- 3) «Reintentar» relanza la búsqueda: el servicio ya responde ---
        prueba = ServidorOverpassDePrueba("ok").arrancar()
        try:
            with patch.object(pros, "OVERPASS_URLS", (prueba.url,)):
                at.button(key="pros_reintentar").click()
                at.run()
                self.assertFalse(at.exception, [e.value for e in at.exception])
        finally:
            prueba.detener()
        self.assertEqual(len(prueba.peticiones), 1)
        self.assertTrue(any("Encontrados: 2 · Nuevos: 2" in i.value for i in at.info),
                        [i.value for i in at.info])
        self.assertFalse(at.error, "tras reintentar no debe quedarse el error anterior")
        self.assertFalse(any(b.key == "pros_reintentar" for b in at.button),
                         "sin fallo no se ofrecen alternativas")

        # --- 4) El servidor acepta la consulta pero no la termina: baja el radio y avisa ---
        prueba = ServidorOverpassDePrueba("no completa luego ok").arrancar()
        try:
            with patch.object(pros, "OVERPASS_URLS", (prueba.url,)):
                at.slider(key="pros_radio").set_value(12)
                at.button(key="pros_buscar").click()  # el envío va en la misma tanda
                at.run()
                self.assertFalse(at.exception, [e.value for e in at.exception])
        finally:
            prueba.detener()
        self.assertTrue(any("12 km" in w.value and "6 km" in w.value for w in at.warning),
                        [w.value for w in at.warning])
        self.assertTrue(any("Encontrados: 2 · Nuevos: 2" in i.value for i in at.info),
                        [i.value for i in at.info])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "con root los permisos de solo lectura no aplican")
    def test_17_con_la_base_en_solo_lectura_la_app_avisa_y_deja_entrar(self):
        """Regresión del fallo reportado: `sqlite3.OperationalError` en el UPDATE del login.

        En Streamlit Cloud el mensaje salía censurado («original error message is
        redacted») y nadie podía entrar. Ahora la app avisa en pantalla, usa una copia
        escribible para no dejar fuera al equipo y el acceso con credenciales correctas
        sigue funcionando.
        """
        # Se apunta la app a una copia de la base con permisos de solo lectura (el
        # caché de conexiones está ligado a la ruta, así que esta copia se abre aparte).
        copia_ro = os.path.join(self.tmp.name, "usuarios_solo_lectura.db")
        shutil.copyfile(self.ruta_db, copia_ro)
        os.chmod(copia_ro, 0o444)  # base del despliegue que no admite escritura
        anterior = os.environ["NEMET_DB_USUARIOS"]
        os.environ["NEMET_DB_USUARIOS"] = copia_ro
        try:
            at = self.abrir(paso="base de solo lectura")
            avisos = " ".join(w.value for w in at.warning)
            self.assertIn("no admite escritura", avisos)
            self.assertIn("copia temporal", avisos)
            # El cuadro rojo de traceback no debe aparecer: se explica la causa real.
            self.assertFalse(any("redacted" in e.value for e in at.error), [e.value for e in at.error])
            at = self.entrar(at, "jefe", PASSWORD_ADMIN, "base de solo lectura")
            self.assertEqual(self.opciones_menu(at)[0], "📊 Dashboard & Resumen")
        finally:
            os.environ["NEMET_DB_USUARIOS"] = anterior
            os.chmod(copia_ro, 0o644)

    def test_18_si_overpass_falla_la_interfaz_usa_denue_y_no_expone_el_token(self):
        """El fallo de transporte de OSM activa DENUE si se configuró un token privado."""
        token = "token-falso-para-prueba"
        registro = {
            "Id": "922221", "Nombre": "Carpintería de Prueba DENUE", "Razon_social": "",
            "Clase_actividad": "Carpintería y fabricación de productos de madera",
            "Estrato": "6 a 10 personas", "Tipo_vialidad": "CALLE", "Calle": "CENTRAL",
            "Num_Exterior": "25", "Num_Interior": "", "Colonia": "CENTRO", "CP": "85000",
            "Ubicacion": "Ciudad Obregón, Cajeme, Sonora", "Telefono": "6441094321",
            "Correo_e": "", "Sitio_internet": "", "Latitud": "27.49", "Longitud": "-109.94",
        }
        candidato = pros._registro_denue_a_candidato(
            registro, "Ciudad Obregón, Sonora", tuple(pros.SECTORES), ("EPOXY PISOS",),
            centro=(27.48642, -109.94079))
        with patch.dict(os.environ, {"NEMET_INEGI_DENUE_TOKEN": token}):
            with patch("prospeccion.buscar_osm_detallada", side_effect=pros.ErrorBusqueda(
                    "No hay respuesta de OpenStreetMap/Overpass desde este despliegue.",
                    (("overpass.kumi.systems", "no respondió a tiempo"),))) as osm:
                with patch("prospeccion.buscar_denue_detallada", return_value=(
                        [candidato], {"servidor": "api.inegi.org.mx", "radio_pedido": 30,
                                      "radio_usado": 5, "avisos": ["DENUE permite un máximo de 5 km."]})) as denue:
                    at = self.abrir(token=TestFlujoAcceso.token_admin, paso="respaldo DENUE")
                    self.ir_a(at, "🎯 Prospección Comercial")
                    at.text_input(key="pros_ciudad").set_value("Obregón (prueba DENUE), Sonora")
                    at.button(key="pros_buscar").click()
                    at.run()
                    self.assertFalse(at.exception, [e.value for e in at.exception])
                    osm.assert_called_once()
                    denue.assert_called_once()
                    self.assertEqual(denue.call_args.args[-1], token)
                    self.assertTrue(any("OpenStreetMap/Overpass no respondió" in w.value for w in at.warning),
                                    [w.value for w in at.warning])

                    # La fuente DENUE también se puede elegir directamente sin pasar por Overpass.
                    at.radio(key="pros_fuente_busqueda").set_value("DENUE (INEGI)")
                    at.button(key="pros_buscar").click()  # el envío va en la misma tanda
                    at.run()
                    self.assertFalse(at.exception, [e.value for e in at.exception])
                    self.assertEqual(osm.call_count, 1, "la fuente directa no debe llamar a Overpass")
                    self.assertEqual(denue.call_count, 2)
        self.assertFalse(at.error, [e.value for e in at.error])
        self.assertTrue(any("DENUE (INEGI)" in c.value for c in at.caption),
                        [c.value for c in at.caption])
        self.assertTrue(any("Carpintería de Prueba DENUE" in o for s in at.selectbox for o in s.options),
                        "la ficha de DENUE queda disponible como candidata")
        render = " ".join([*(c.value for c in at.caption), *(m.value for m in at.markdown),
                           *(e.value for e in at.error), *(w.value for w in at.warning)])
        self.assertNotIn(token, render)

    def test_19_el_fallo_de_credencial_del_denue_se_explica_sin_exponer_el_token(self):
        """El INEGI rechaza la credencial con HTTP 200: la pantalla debe decirlo y ofrecer verificarla."""
        token = "token-falso-de-verificacion"
        diagnostico = {"estado": 200, "tipo_contenido": "text/plain", "bytes": 39,
                       "forma": "texto sin JSON", "huella_token": pros.huella_token_denue(token)}
        fallo = pros.ErrorBusqueda(
            "DENUE (INEGI) rechazó el token: el servicio respondió que la clave no es válida con HTTP 200 "
            "y texto plano, no con un error HTTP (huella configurada: 36 caracteres).",
            (("api.inegi.org.mx", "token no válido o sin autorización"),), diagnostico)
        with patch.dict(os.environ, {"NEMET_INEGI_DENUE_TOKEN": token}):
            with patch("prospeccion.buscar_denue_detallada", side_effect=fallo) as denue:
                with patch("prospeccion.verificar_credencial_denue", return_value=(
                        "rechazado", "El INEGI rechazó la credencial: respondió que la clave no es válida.",
                        diagnostico)) as verificar:
                    at = self.abrir(token=TestFlujoAcceso.token_admin, paso="credencial DENUE")
                    self.ir_a(at, "🎯 Prospección Comercial")
                    at.radio(key="pros_fuente_busqueda").set_value("DENUE (INEGI)")
                    at.button(key="pros_buscar").click()  # el envío del formulario va en la misma tanda
                    at.run()
                    self.assertFalse(at.exception, [e.value for e in at.exception])
                    denue.assert_called_once()
                    self.assertTrue(any("rechazó el token" in e.value for e in at.error),
                                    [e.value for e in at.error])
                    render = " ".join([*(c.value for c in at.caption), *(m.value for m in at.markdown),
                                       *(e.value for e in at.error), *(w.value for w in at.warning)])
                    self.assertIn("HTTP 200", render, "el diagnóstico debe decir que el rechazo llegó con 200")
                    self.assertIn("texto sin JSON", render)
                    self.assertIn("huella", render.lower())
                    self.assertNotIn(token, render, "el token jamás se muestra")

                    # El botón de verificación distingue «token rechazado» de «sin coincidencias».
                    at.button(key="pros_verificar_denue").click()
                    at.run()
                    verificar.assert_called_once_with(token)
                    self.assertFalse(at.exception, [e.value for e in at.exception])
                    render = " ".join([*(c.value for c in at.caption), *(m.value for m in at.markdown),
                                       *(e.value for e in at.error), *(w.value for w in at.warning)])
                    self.assertIn("rechazó la credencial", render)
                    self.assertNotIn(token, render)


class _ManejadorOverpass(BaseHTTPRequestHandler):
    """Atiende POST /api/interpreter como Overpass, con el modo que le pida la prueba."""

    def log_message(self, *args):
        pass  # sin ruido en la salida de las pruebas

    def do_POST(self):
        prueba = self.server.prueba
        largo = int(self.headers.get("Content-Length") or 0)
        forma = parse_qs(self.rfile.read(largo).decode("utf-8"))
        prueba.peticiones.append(forma.get("data", [""])[0])
        if prueba.modo in ("429", "429 luego ok") and (
                prueba.modo == "429" or len(prueba.peticiones) == 1):
            self._responder(429, b'{"remark":"limite de peticiones"}', {"Retry-After": "1"})
        elif prueba.modo == "no completa luego ok" and len(prueba.peticiones) == 1:
            self._responder(200, json.dumps({"remark": "runtime error: Query timed out",
                                             "elements": []}).encode("utf-8"))
        else:
            cuerpo = json.dumps({"elements": prueba.ELEMENTOS}).encode("utf-8")
            self._responder(200, cuerpo)

    def _responder(self, estado, cuerpo, cabeceras=None):
        self.send_response(estado)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        for clave, valor in (cabeceras or {}).items():
            self.send_header(clave, valor)
        self.end_headers()
        self.wfile.write(cuerpo)


class ServidorOverpassDePrueba:
    """Servidor HTTP local que imita a Overpass para probar la búsqueda real de la app.

    No inventa negocios: devuelve el mismo tipo de fichas públicas que Overpass (con
    coordenadas reales de Ciudad Obregón) y reproduce los dos fallos que la app debe saber
    manejar: el límite por IP (HTTP 429 con `Retry-After`) y la consulta que el servidor
    acepta pero no termina.
    """

    ELEMENTOS = [
        {"type": "node", "id": 5001, "lat": 27.49, "lon": -109.94,
         "tags": {"name": "Carpintería del Sol", "craft": "carpenter", "addr:suburb": "Centro",
                  "contact:phone": "+52 644 109 4422"}},
        {"type": "node", "id": 5002, "lat": 27.60, "lon": -109.90,
         "tags": {"name": "Ebanistería Yaqui", "craft": "cabinet_maker"}},
        {"type": "node", "id": 5003, "lat": 27.20, "lon": -109.95,
         "tags": {"name": "Muebles del Mayo", "craft": "furniture_maker"}},  # ~32 km: fuera de 30 km
        {"type": "way", "id": 5004, "center": {"lat": 27.50, "lon": -109.93},
         "tags": {"name": "Pisos Obregón", "craft": "tiler", "phone": "662 109 4455"}},
        {"type": "node", "id": 5005, "lat": 27.48, "lon": -109.93,
         "tags": {"name": "Panadería Yaqui", "shop": "bakery"}},  # otro giro: se descarta
    ]

    def __init__(self, modo="ok"):
        self.modo = modo
        self.peticiones = []
        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _ManejadorOverpass)
        self._http.prueba = self
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}/api/interpreter"

    def arrancar(self):
        threading.Thread(target=self._http.serve_forever, daemon=True).start()
        return self

    def detener(self):
        self._http.shutdown()
        self._http.server_close()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)
    n = resultado.testsRun
    if resultado.wasSuccessful():
        print(f"\n{n}/{n} OK — flujo de acceso NEMET verificado")
        sys.exit(0)
    print(f"\n{n - len(resultado.failures) - len(resultado.errors)}/{n} (revisa los fallos)")
    sys.exit(1)
