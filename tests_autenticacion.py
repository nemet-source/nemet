#!/usr/bin/env python3
"""Batería de pruebas de la autenticación NEMET (48 pruebas).

Cubre `auth_nemet.py` (hashes, roles, sesiones, reglas duras) y la integración en
`app.py` (gate de acceso, módulos protegidos, secretos fuera de Git).

Uso:  python tests_autenticacion.py     →  "48/48 OK" si todo pasa.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import auth_nemet as auth  # noqa: E402

PASSWORD_ADMIN = "NemetAdmin2026"
PASSWORD_USER = "UsuarioNemet2026"


class BaseAuth(unittest.TestCase):
    """Cada prueba corre sobre una base temporal y con el administrador inicial ya sembrado."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta_db = os.path.join(self.dir.name, "nemet_usuarios.db")
        self.conn = auth.conectar(self.ruta_db)
        auth.sembrar_admin_inicial(self.conn, "jorge", PASSWORD_ADMIN, nombre="Jorge", correo="jorge@nemet.mx")
        self.admin = auth.obtener_por_login(self.conn, "jorge")

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def crear(self, usuario="vendedor", rol="usuario", password=PASSWORD_USER):
        return auth.crear_usuario(self.conn, self.admin, usuario, password=password,
                                  nombre=usuario.title(), correo=f"{usuario}@nemet.mx", rol=rol)


# ============================================================ contraseñas
class TestPasswords(BaseAuth):

    def test_01_el_hash_no_contiene_la_password(self):
        h = auth.hash_password(PASSWORD_ADMIN)
        self.assertNotIn(PASSWORD_ADMIN, h)
        self.assertTrue(h.startswith("scrypt$"))

    def test_02_sal_aleatoria_hace_hashes_distintos(self):
        self.assertNotEqual(auth.hash_password(PASSWORD_ADMIN), auth.hash_password(PASSWORD_ADMIN))

    def test_03_verificacion_correcta_incorrecta_y_basura(self):
        h = auth.hash_password(PASSWORD_ADMIN)
        self.assertTrue(auth.verificar_password(PASSWORD_ADMIN, h)[0])
        self.assertFalse(auth.verificar_password("otra-cosa-1", h)[0])
        self.assertFalse(auth.verificar_password(PASSWORD_ADMIN, "formato-invalido")[0])

    def test_04_la_password_no_queda_en_texto_plano_en_el_archivo(self):
        self.crear("vendedor", password="ClaveSecreta9876")
        self.conn.commit()
        with open(self.ruta_db, "rb") as fh:
            contenido = fh.read()
        self.assertNotIn(b"ClaveSecreta9876", contenido)
        self.assertNotIn(b"NemetAdmin2026", contenido)

    def test_05_politica_de_passwords_debiles(self):
        for debil in ("corta1", "solamenteletras", "1234567890123", "password"):
            with self.assertRaises(auth.ErrorAuth):
                auth.validar_password(debil, "vendedor")
        with self.assertRaises(auth.ErrorAuth):
            auth.validar_password("VendedorNemet2026", "vendedor")  # contiene el usuario

    def test_06_password_temporal_cumple_la_politica(self):
        for _ in range(5):
            auth.validar_password(auth.generar_password_temporal())


# ============================================================ base y semilla
class TestBaseYSemilla(BaseAuth):

    def test_07_esquema_idempotente_y_con_conteo(self):
        self.assertEqual(auth.inicializar_db(self.conn), 1)
        auth.inicializar_db(self.conn)
        self.assertEqual(len(auth.listar_usuarios(self.conn)), 1)

    def test_08_la_semilla_crea_el_admin_y_es_idempotente(self):
        estado, _ = auth.sembrar_admin_inicial(self.conn, "jorge", PASSWORD_ADMIN)
        self.assertEqual(estado, "existente")
        self.assertEqual(len([u for u in auth.listar_usuarios(self.conn) if u["rol"] == "admin"]), 1)

    def test_09_la_semilla_no_es_puerta_trasera_permanente(self):
        auth.cambiar_password_propia(self.conn, self.admin["id"], PASSWORD_ADMIN, "OtraClaveFuerte2026")
        auth.autenticar(self.conn, "jorge", PASSWORD_ADMIN)
        self.assertIsNone(auth.autenticar(self.conn, "jorge", PASSWORD_ADMIN)[0])
        self.assertIsNotNone(auth.autenticar(self.conn, "jorge", "OtraClaveFuerte2026")[0])

    def test_10_semilla_con_forzar_restablece_y_desbloquea(self):
        auth.cambiar_password_propia(self.conn, self.admin["id"], PASSWORD_ADMIN, "OtraClaveFuerte2026")
        estado, mensaje = auth.sembrar_admin_inicial(self.conn, "jorge", PASSWORD_ADMIN, forzar=True)
        self.assertEqual(estado, "actualizado")
        self.assertIn("restablecida", mensaje)
        usuario = auth.autenticar(self.conn, "jorge", PASSWORD_ADMIN)[0]
        self.assertIsNotNone(usuario)
        self.assertTrue(usuario["debe_cambiar"])  # se pedirá cambiarla al entrar

    def test_11_semilla_sin_datos_y_sin_password(self):
        self.assertEqual(auth.sembrar_admin_inicial(self.conn, "", "")[0], "sin_datos")
        self.assertEqual(auth.sembrar_admin_inicial(self.conn, "jorge", "")[0], "sin_password")

    def test_12_semilla_rechaza_password_debil(self):
        estado, _ = auth.sembrar_admin_inicial(self.conn, "nuevo", "corta")
        self.assertEqual(estado, "invalido")
        self.assertIsNone(auth.obtener_por_login(self.conn, "nuevo"))

    def test_13_las_cuentas_sobreviven_al_cierre_y_reapertura_de_la_base(self):
        patron = auth.hash_password(PASSWORD_USER)
        auth.crear_usuario(self.conn, self.admin, "vendedor", password=PASSWORD_USER)
        self.conn.close()
        self.conn = auth.conectar(self.ruta_db)
        self.assertIsNotNone(auth.obtener_por_login(self.conn, "vendedor"))
        self.assertIsNotNone(auth.autenticar(self.conn, "vendedor", PASSWORD_USER)[0])
        with open(self.ruta_db, "rb") as fh:
            self.assertNotIn(patron.encode(), fh.read())

    def test_14_sin_admins_activos_la_semilla_lo_reporta(self):
        # Situación extrema: la base quedó sin administradores activos (edición manual del archivo)
        with auth._LOCK:
            self.conn.execute("UPDATE usuarios SET activo = 0")
            self.conn.commit()
        admins, hay = auth.asegurar_administradores(self.conn)
        self.assertEqual(admins, 0)
        self.assertFalse(hay)
        estado, mensaje = auth.sembrar_admin_inicial(self.conn, "", "")
        self.assertEqual(estado, "sin_admins")
        self.assertIn("Secrets", mensaje)


# ============================================================ inicio de sesión
class TestLogin(BaseAuth):

    def test_15_login_correcto_registra_ultimo_acceso(self):
        usuario, mensaje = auth.autenticar(self.conn, "jorge", PASSWORD_ADMIN)
        self.assertIsNotNone(usuario)
        self.assertTrue(usuario["ultimo_acceso"])
        self.assertEqual(mensaje, "Sesión iniciada.")

    def test_16_login_acepta_mayusculas_y_acentos(self):
        auth.crear_usuario(self.conn, self.admin, "jose", password=PASSWORD_USER, nombre="José")
        self.assertIsNotNone(auth.autenticar(self.conn, "JOSÉ", PASSWORD_USER)[0])

    def test_17_login_fallido_no_revela_si_el_usuario_existe(self):
        _, m1 = auth.autenticar(self.conn, "jorge", "clave-mala-123")
        _, m2 = auth.autenticar(self.conn, "no-existe", "clave-mala-123")
        self.assertEqual(m1, m2)

    def test_18_bloqueo_temporal_tras_cinco_intentos(self):
        for intento in range(1, auth.MAX_INTENTOS_FALLIDOS):
            _, mensaje = auth.autenticar(self.conn, "jorge", "clave-mala-123")
            self.assertIn("incorrectos", mensaje)
        _, mensaje = auth.autenticar(self.conn, "jorge", "clave-mala-123")
        self.assertIn("se bloqueó", mensaje)
        # Con la contraseña correcta tampoco entra mientras dure el bloqueo
        self.assertIsNone(auth.autenticar(self.conn, "jorge", PASSWORD_ADMIN)[0])
        # Al expirar el bloqueo, vuelve a entrar
        with auth._LOCK:
            self.conn.execute("UPDATE usuarios SET bloqueado_hasta = ? WHERE usuario_norm = 'jorge'",
                              ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
            self.conn.commit()
        self.assertIsNotNone(auth.autenticar(self.conn, "jorge", PASSWORD_ADMIN)[0])

    def test_19_usuario_desactivado_no_puede_entrar(self):
        usuario = auth.crear_usuario(self.conn, self.admin, "temp", password=PASSWORD_USER)
        auth.establecer_activo(self.conn, self.admin, usuario["id"], False)
        self.assertIsNone(auth.autenticar(self.conn, "temp", PASSWORD_USER)[0])
        auth.establecer_activo(self.conn, self.admin, usuario["id"], True)
        self.assertIsNotNone(auth.autenticar(self.conn, "temp", PASSWORD_USER)[0])


# ============================================================ reglas duras
class TestReglasDuras(BaseAuth):

    def test_20_no_se_puede_desactivar_al_ultimo_admin(self):
        with self.assertRaises(auth.ErrorRegla):
            auth.establecer_activo(self.conn, self.admin, self.admin["id"], False)

    def test_21_no_se_puede_eliminar_al_ultimo_admin(self):
        with self.assertRaises(auth.ErrorRegla):
            auth.eliminar_usuario(self.conn, self.admin, self.admin["id"])

    def test_22_no_se_puede_degradar_al_ultimo_admin(self):
        with self.assertRaises(auth.ErrorRegla):
            auth.cambiar_rol(self.conn, self.admin, self.admin["id"], "editor")

    def test_23_con_dos_admins_si_se_puede_desactivar_a_uno(self):
        otro = auth.crear_usuario(self.conn, self.admin, "jefa", password=PASSWORD_USER, rol="admin")
        auth.establecer_activo(self.conn, otro, self.admin["id"], False)
        self.assertFalse(auth.obtener_usuario(self.conn, self.admin["id"])["activo"])
        self.assertEqual(auth.contar_admins_activos(self.conn), 1)
        # Y el que queda tampoco puede desactivarse a sí mismo
        with self.assertRaises(auth.ErrorRegla):
            auth.establecer_activo(self.conn, otro, otro["id"], False)

    def test_24_nadie_elimina_ni_desactiva_su_propia_cuenta(self):
        otro_admin = auth.crear_usuario(self.conn, self.admin, "jefa", password=PASSWORD_USER, rol="admin")
        with self.assertRaises(auth.ErrorRegla):
            auth.establecer_activo(self.conn, otro_admin, otro_admin["id"], False)
        with self.assertRaises(auth.ErrorRegla):
            auth.eliminar_usuario(self.conn, otro_admin, otro_admin["id"])

    def test_25_un_admin_tampoco_puede_cambiarse_el_rol_a_si_mismo(self):
        auth.crear_usuario(self.conn, self.admin, "jefa", password=PASSWORD_USER, rol="admin")
        with self.assertRaises(auth.ErrorRegla):
            auth.cambiar_rol(self.conn, self.admin, self.admin["id"], "editor")

    def test_26_el_invariante_de_administradores_sobrevive_a_una_secuencia(self):
        jefa = auth.crear_usuario(self.conn, self.admin, "jefa", password=PASSWORD_ADMIN, rol="admin")
        # Desactivar a un admin cuando queda otro activo sí se permite
        auth.establecer_activo(self.conn, jefa, self.admin["id"], False)
        self.assertEqual(auth.contar_admins_activos(self.conn), 1)
        # A partir de ahí toda operación que dejaría 0 administradores se rechaza
        for operacion in (lambda: auth.cambiar_rol(self.conn, jefa, jefa["id"], "usuario"),
                          lambda: auth.establecer_activo(self.conn, jefa, jefa["id"], False),
                          lambda: auth.eliminar_usuario(self.conn, jefa, jefa["id"])):
            with self.assertRaises(auth.ErrorRegla):
                operacion()
        self.assertEqual(auth.contar_admins_activos(self.conn), 1)
        self.assertIsNotNone(auth.autenticar(self.conn, "jefa", PASSWORD_ADMIN)[0])

    def test_27_solo_un_admin_activo_gestiona_usuarios(self):
        editor = self.crear("editor1", rol="editor")
        with self.assertRaises(auth.ErrorRegla):
            auth.crear_usuario(self.conn, editor, "colado", password=PASSWORD_USER)
        auth.establecer_activo(self.conn, self.admin, editor["id"], False)
        with self.assertRaises(auth.ErrorAuth):
            auth.crear_usuario(self.conn, editor, "colado2", password=PASSWORD_USER)


# ============================================================ administración
class TestAdministracion(BaseAuth):

    def test_28_crear_usuario_con_password_temporal(self):
        nuevo = auth.crear_usuario(self.conn, self.admin, "vendedor2", nombre="Vendedor", rol="editor")
        self.assertTrue(nuevo["debe_cambiar"])
        self.assertTrue(nuevo["password_temporal"])
        self.assertTrue(auth.autenticar(self.conn, "vendedor2", nuevo["password_temporal"])[0])

    def test_29_no_se_repiten_usuarios_ni_se_aceptan_roles_invalidos(self):
        self.crear("vendedor")
        with self.assertRaises(auth.ErrorAuth):
            self.crear("VENDEDOR")
        with self.assertRaises(auth.ErrorAuth):
            self.crear("otro", rol="superusuario")

    def test_30_restablecer_password_marca_cambio_obligatorio(self):
        usuario = self.crear("vendedor")
        auth.establecer_activo(self.conn, self.admin, usuario["id"], False)
        _, temporal = auth.restablecer_password(self.conn, self.admin, usuario["id"])
        datos = auth.obtener_usuario(self.conn, usuario["id"])
        self.assertTrue(datos["debe_cambiar"])
        self.assertTrue(datos["activo"])  # restablecer reactiva la cuenta
        self.assertIsNotNone(auth.autenticar(self.conn, "vendedor", temporal)[0])

    def test_31_cambio_de_password_propia(self):
        usuario = self.crear("vendedor")
        with self.assertRaises(auth.ErrorAuth):
            auth.cambiar_password_propia(self.conn, usuario["id"], "no-es-la-actual", "NuevaClaveFuerte2026")
        with self.assertRaises(auth.ErrorAuth):
            auth.cambiar_password_propia(self.conn, usuario["id"], PASSWORD_USER, "debil")
        auth.cambiar_password_propia(self.conn, usuario["id"], PASSWORD_USER, "NuevaClaveFuerte2026")
        self.assertIsNotNone(auth.autenticar(self.conn, "vendedor", "NuevaClaveFuerte2026")[0])

    def test_32_listar_usuarios_nunca_expone_el_hash(self):
        self.crear("vendedor")
        for usuario in auth.listar_usuarios(self.conn):
            self.assertNotIn("password_hash", usuario)
        self.assertNotIn("password_hash", auth.resumen(self.conn))

    def test_33_la_bitacora_se_poda_para_no_crecer_sin_control(self):
        for i in range(auth.LIMITE_EVENTOS + 25):
            auth.registrar_evento(self.conn, "prueba", detalle=f"evento {i}")
        total = self.conn.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
        self.assertLessEqual(total, auth.LIMITE_EVENTOS)
        self.assertEqual(auth.listar_eventos(self.conn, 1)[0]["detalle"], f"evento {auth.LIMITE_EVENTOS + 24}")

    def test_34_bitacora_de_eventos(self):
        usuario = self.crear("vendedor")
        auth.establecer_activo(self.conn, self.admin, usuario["id"], False)
        auth.autenticar(self.conn, "vendedor", "clave-mala-123")
        acciones = [e["accion"] for e in auth.listar_eventos(self.conn, 50)]
        for esperada in ("admin_sembrado", "usuario_creado", "usuario_desactivado", "inicio_sesion_fallido"):
            self.assertIn(esperada, acciones)
        texto = " ".join(e["detalle"] for e in auth.listar_eventos(self.conn, 50))
        self.assertNotIn(PASSWORD_USER, texto)


# ============================================================ sesiones y roles
class TestSesionesYPermisos(BaseAuth):

    def test_35_tokens_firmados_validos_expirados_y_alterados(self):
        secreto = auth.secreto_sesion(self.conn)
        token = auth.crear_token(secreto, self.admin)
        self.assertEqual(auth.leer_token(secreto, token)["u"], "jorge")
        self.assertIsNotNone(auth.usuario_de_token(self.conn, secreto, token))
        self.assertIsNone(auth.leer_token(secreto, token[:-2] + ("aa" if not token.endswith("aa") else "bb")))
        self.assertIsNone(auth.leer_token("otro-secreto", token))
        self.assertIsNone(auth.leer_token(secreto, auth.crear_token(secreto, self.admin, minutos=-1)))
        self.assertIsNone(auth.leer_token(secreto, ""))

    def test_36_el_token_deja_de_valer_si_desactivan_o_eliminan_al_usuario(self):
        secreto = auth.secreto_sesion(self.conn)
        usuario = self.crear("vendedor")
        token = auth.crear_token(secreto, usuario)
        self.assertIsNotNone(auth.usuario_de_token(self.conn, secreto, token))
        auth.establecer_activo(self.conn, self.admin, usuario["id"], False)
        self.assertIsNone(auth.usuario_de_token(self.conn, secreto, token))
        auth.eliminar_usuario(self.conn, self.admin, usuario["id"])
        self.assertIsNone(auth.usuario_de_token(self.conn, secreto, token))

    def test_37_el_rol_vigente_se_relée_de_la_base(self):
        secreto = auth.secreto_sesion(self.conn)
        usuario = self.crear("vendedor", rol="usuario")
        token = auth.crear_token(secreto, usuario)
        auth.cambiar_rol(self.conn, self.admin, usuario["id"], "editor")
        self.assertEqual(auth.usuario_de_token(self.conn, secreto, token)["rol"], "editor")

    def test_38_rotar_la_clave_invalida_las_sesiones(self):
        secreto = auth.secreto_sesion(self.conn)
        token = auth.crear_token(secreto, self.admin)
        auth.rotar_secreto_sesion(self.conn, self.admin)
        self.assertIsNone(auth.leer_token(auth.secreto_sesion(self.conn), token))

    def test_39_permisos_por_rol(self):
        self.assertTrue(auth.puede("admin", "inventario"))
        self.assertTrue(auth.puede("editor", "inventario"))
        self.assertFalse(auth.puede("usuario", "inventario"))
        self.assertFalse(auth.puede("editor", "admin_usuarios"))
        self.assertFalse(auth.puede("editor", "respaldo"))
        for rol in auth.ROLES:
            self.assertTrue(auth.puede(rol, "dashboard"))
            self.assertTrue(auth.puede(rol, "cotizador_area"))
            self.assertTrue(auth.puede(rol, "cotizador_comercial"))
            self.assertTrue(auth.puede(rol, "historial"))
        self.assertFalse(auth.puede("desconocido", "dashboard"))
        self.assertTrue(auth.puede("admin", "usuarios_gestionar"))


# ============================================================ integración app.py
class TestIntegracionApp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BASE, "app.py"), encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_40_app_usa_el_modulo_de_autenticacion(self):
        for token in ("import auth_nemet", "auth.inicializar_db", "auth.sembrar_admin_inicial",
                      "auth.autenticar", "auth.crear_token", "auth.usuario_de_token", "def requiere_modulo"):
            self.assertIn(token, self.src, f"falta {token} en app.py")

    def test_41_configuracion_de_pagina_sigue_siendo_lo_primero(self):
        m = re.search(r"^st\.(\w+)\s*\(", self.src, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "set_page_config")

    def test_42_el_gate_corre_antes_de_cargar_datos(self):
        posicion_gate = self.src.index("SESION = ejecutar_gate_acceso()")
        self.assertLess(posicion_gate, self.src.index("# ESTADO INICIAL"))
        self.assertLess(posicion_gate, self.src.index("st.sidebar.title"))

    def test_43_todos_los_modulos_del_menu_estan_protegidos(self):
        lineas = self.src.splitlines()
        encontrados = 0
        for i, linea in enumerate(lineas):
            coincidencia = re.match(r'^(?:el)?if menu == "([^"]+)":', linea)
            if not coincidencia:
                continue
            encontrados += 1
            primeras = "\n".join(lineas[i + 1:i + 4])
            self.assertIn("requiere_modulo(", primeras,
                          f"el módulo {coincidencia.group(1)} no está protegido")
        self.assertGreaterEqual(encontrados, 6)

    def test_44_modulos_del_menu_declaran_rol(self):
        menu = re.search(r"MODULOS_MENU = \[(.*?)\n\]", self.src, re.DOTALL)
        self.assertIsNotNone(menu, "app.py debe declarar MODULOS_MENU")
        claves = re.findall(r'"(\w+)"', menu.group(1))
        for clave in claves:
            self.assertIn(clave, auth.MODULOS, f"el módulo {clave} no existe en MODULOS")
        for clave in ("inventario", "clientes", "historial", "admin_usuarios"):
            self.assertIn(clave, claves)

    def test_45_permisos_efectivos_por_rol_en_el_menu(self):
        self.assertIn("def modulos_visibles", self.src)
        etiqueta_historias = auth.MODULOS["historial"][0]
        self.assertIn(etiqueta_historias, self.src)

    def test_46_no_hay_credenciales_en_el_codigo_ni_en_los_secrets_versionados(self):
        # Ninguna asignación de contraseña literal en el código de la app
        self.assertIsNone(re.search(r'(admin_password|password)\s*=\s*"[^"\s]{6,}"', self.src),
                          "app.py no debe traer contraseñas escritas en el código")
        # El archivo de secretos locales no debe estar versionado
        ruta_secrets = os.path.join(BASE, ".streamlit", "secrets.toml")
        with open(os.path.join(BASE, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn(".streamlit/secrets.toml", fh.read())
        salida = subprocess.run(["git", "ls-files"], cwd=BASE, capture_output=True, text=True).stdout
        self.assertNotIn("secrets.toml", salida)
        self.assertFalse(os.path.exists(ruta_secrets) and ruta_secrets in salida)

    def test_47_los_roles_se_aplican_en_las_acciones_sensibles(self):
        for token in ("usuarios_gestionar", "historial_borrar", '"respaldo"'):
            self.assertIn(token, self.src, f"falta el permiso {token} en app.py")

    def test_48_respaldo_en_github_incluye_la_base_de_usuarios(self):
        self.assertIn("rutas_extra", self.src)
        self.assertIn("RUTA_DB_USUARIOS", self.src)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)
    n = resultado.testsRun
    if resultado.wasSuccessful():
        print(f"\n{n}/{n} OK — autenticación NEMET verificada")
        sys.exit(0)
    print(f"\n{n - len(resultado.failures) - len(resultado.errors)}/{n} (revisa los fallos)")
    sys.exit(1)
