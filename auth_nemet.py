#!/usr/bin/env python3
"""Autenticación, sesiones y gestión de usuarios del Sistema Maestro NEMET.

Decisiones de diseño
--------------------
* **Persistencia:** SQLite en un solo archivo (`nemet_usuarios.db`). No requiere
  dependencias externas y el archivo se respalda en GitHub junto con el Excel
  (solo contiene *hashes*, nunca contraseñas).
* **Contraseñas:** `hashlib.scrypt` con sal aleatoria de 16 bytes y comparación en
  tiempo constante (`hmac.compare_digest`). Nunca se guarda ni se registra la
  contraseña en texto plano.
* **Sesiones:** token firmado con HMAC-SHA256 (expiración por inactividad) que no
  contiene datos sensibles; el estado se revalida contra la base en cada carga.
* **Regla dura:** el sistema nunca queda sin al menos un administrador activo
  (no se puede desactivar, eliminar ni degradar al último), y nadie puede
  desactivar ni eliminar su propia cuenta.

Este módulo es independiente de Streamlit a propósito: así se puede probar y
reutilizar sin arrancar la interfaz (`tests_autenticacion.py`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone

VERSION = "1.0"

# ==========================================
# ROLES Y PERMISOS
# ==========================================
ROLES = ("admin", "editor", "usuario")

ETIQUETA_ROL = {
    "admin": "Administrador",
    "editor": "Editor",
    "usuario": "Usuario",
}

# Módulos de la app y roles con acceso. El módulo "admin_usuarios" no es un
# módulo de negocio: es el panel de administración de cuentas.
MODULOS = {
    "dashboard": ("📊 Dashboard & Resumen", ("admin", "editor", "usuario")),
    "inventario": ("📦 Control de Inventario y Edición", ("admin", "editor")),
    "clientes": ("👥 Gestión de Clientes", ("admin", "editor")),
    "cotizador_area": ("📏 Cotizador por Área y Milimétrico", ("admin", "editor", "usuario")),
    "cotizador_comercial": ("📝 Cotizador Comercial Profesional", ("admin", "editor", "usuario")),
    "historial": ("📋 Historial de Cotizaciones (Folios)", ("admin", "editor", "usuario")),
    "admin_usuarios": ("🛡️ Administración de Usuarios", ("admin",)),
}

# Acciones sensibles que no dependen de un módulo completo
ACCIONES_ADMIN = ("respaldo", "historial_borrar", "usuarios_gestionar")


def puede(rol, modulo):
    """True si el rol tiene acceso al módulo (o a la acción sensible) indicada."""
    if modulo in ACCIONES_ADMIN:
        return rol == "admin"
    roles = MODULOS.get(modulo, (None, ()))[1]
    return rol in roles


def etiqueta_rol(rol):
    return ETIQUETA_ROL.get(rol, str(rol))


# ==========================================
# PARÁMETROS DE SEGURIDAD
# ==========================================
SCRYPT_N = 2 ** 14          # 16 384 (≈16 MB de memoria por hash)
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
SCRYPT_PREFIJO = "scrypt"

LARGO_MINIMO_PASSWORD = 10
MAX_INTENTOS_FALLIDOS = 5
BLOQUEO_MINUTOS = 15
TTL_SESION_MINUTOS = 60     # expiración por inactividad (configurable)

_RE_USUARIO = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Contraseñas descartadas de plano (bootstrap local y variantes obvias)
_PASSWORDS_PROHIBIDAS = {
    "contrasena", "contraseña", "password", "1234567890", "nemet12345",
    "administrador", "admin12345", "qwertyuiop", "nemetnemet",
}

# Cerrojo del proceso: Streamlit atiende varias sesiones en hilos del mismo
# proceso y SQLite se comparte entre ellas.
_LOCK = threading.RLock()


class ErrorAuth(Exception):
    """Error de validación o de permisos con un mensaje apto para mostrar al usuario."""


class ErrorRegla(ErrorAuth):
    """Se intentó una operación que rompe una regla dura (p. ej. dejar el sistema sin admins)."""


def ahora(zona=None):
    """Momento actual: UTC por defecto, o en la zona indicada (p. ej. America/Hermosillo)."""
    return datetime.now(zona) if zona is not None else datetime.now(timezone.utc)


# ==========================================
# CONTRASEÑAS
# ==========================================
def hash_password(password):
    """Devuelve `scrypt$n$r$p$sal$hash` (sal aleatoria por contraseña)."""
    if not isinstance(password, str) or not password:
        raise ErrorAuth("La contraseña no puede estar vacía.")
    sal = secrets.token_bytes(SCRYPT_SALT_BYTES)
    derivada = hashlib.scrypt(password.encode("utf-8"), salt=sal, n=SCRYPT_N, r=SCRYPT_R,
                              p=SCRYPT_P, dklen=SCRYPT_DKLEN, maxmem=64 * 1024 * 1024)
    return "{}${}${}${}${}${}".format(
        SCRYPT_PREFIJO, SCRYPT_N, SCRYPT_R, SCRYPT_P,
        base64.b64encode(sal).decode("ascii"),
        base64.b64encode(derivada).decode("ascii"),
    )


def verificar_password(password, password_hash):
    """Compara en tiempo constante. Devuelve (valida, requiere_rehash)."""
    if not password or not password_hash:
        return False, False
    try:
        algoritmo, n, r, p, sal_b64, hash_b64 = str(password_hash).split("$")
        if algoritmo != SCRYPT_PREFIJO:
            return False, False
        sal = base64.b64decode(sal_b64)
        esperado = base64.b64decode(hash_b64)
        derivada = hashlib.scrypt(password.encode("utf-8"), salt=sal, n=int(n), r=int(r),
                                  p=int(p), dklen=len(esperado), maxmem=64 * 1024 * 1024)
    except Exception:
        return False, False
    valida = hmac.compare_digest(derivada, esperado)
    desactualizado = (int(n), int(r), int(p)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return valida, bool(valida and desactualizado)


def _password_debil(password, usuario=""):
    """Motivo por el que la contraseña no cumple la política, o cadena vacía si cumple."""
    if not isinstance(password, str) or len(password) < LARGO_MINIMO_PASSWORD:
        return f"La contraseña debe tener al menos {LARGO_MINIMO_PASSWORD} caracteres."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "La contraseña debe combinar letras y números."
    if password.strip().lower() in _PASSWORDS_PROHIBIDAS:
        return "Esa contraseña es demasiado común; elige otra."
    normal = normalizar(password)
    if usuario and normalizar(usuario) and normalizar(usuario) in normal:
        return "La contraseña no puede contener el nombre de usuario."
    return ""


def validar_password(password, usuario=""):
    motivo = _password_debil(password, usuario)
    if motivo:
        raise ErrorAuth(motivo)
    return True


_ALFABETO_TEMPORAL = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generar_password_temporal(largo=12):
    """Contraseña provisional legible (sin caracteres ambiguos) que cumple la política."""
    largo = max(LARGO_MINIMO_PASSWORD, int(largo))
    while True:
        candidata = "".join(secrets.choice(_ALFABETO_TEMPORAL) for _ in range(largo))
        if not _password_debil(candidata):
            return candidata


# ==========================================
# UTILIDADES DE TEXTO
# ==========================================
def normalizar(texto):
    """Minúsculas sin acentos ni espacios sobrantes (para comparar usuarios)."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.strip().lower()


# ==========================================
# BASE DE DATOS
# ==========================================
ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario             TEXT    NOT NULL,
    usuario_norm        TEXT    NOT NULL UNIQUE,
    nombre              TEXT    NOT NULL DEFAULT '',
    correo              TEXT    NOT NULL DEFAULT '',
    rol                 TEXT    NOT NULL,
    password_hash       TEXT    NOT NULL,
    debe_cambiar        INTEGER NOT NULL DEFAULT 0,
    activo              INTEGER NOT NULL DEFAULT 1,
    creado_en           TEXT    NOT NULL,
    creado_por          TEXT    NOT NULL DEFAULT '',
    actualizado_en      TEXT    NOT NULL DEFAULT '',
    ultimo_acceso       TEXT    NOT NULL DEFAULT '',
    password_cambiada_en TEXT   NOT NULL DEFAULT '',
    intentos_fallidos   INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta     TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS eventos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha     TEXT NOT NULL,
    actor     TEXT NOT NULL DEFAULT '',
    actor_id  INTEGER,
    accion    TEXT NOT NULL,
    objetivo  TEXT NOT NULL DEFAULT '',
    detalle   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_eventos_fecha ON eventos (fecha DESC, id DESC);

CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL DEFAULT ''
);
"""

COLUMNAS_PUBLICAS = ("id", "usuario", "nombre", "correo", "rol", "activo", "debe_cambiar",
                     "creado_en", "creado_por", "actualizado_en", "ultimo_acceso",
                     "bloqueado_hasta", "intentos_fallidos")


def conectar(ruta):
    """Abre (creando si hace falta) la base de usuarios. Devuelve la conexión."""
    carpeta = os.path.dirname(os.path.abspath(ruta))
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    conn = sqlite3.connect(ruta, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with _LOCK:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.executescript(ESQUEMA)
        conn.commit()
    return conn


def inicializar_db(conn):
    """Garantiza el esquema (idempotente) y devuelve el número de usuarios."""
    with _LOCK:
        conn.executescript(ESQUEMA)
        conn.commit()
        return int(conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0])


def _fila_a_dict(fila):
    if fila is None:
        return None
    datos = {clave: fila[clave] for clave in fila.keys() if clave != "password_hash"}
    datos["activo"] = bool(datos.get("activo"))
    datos["debe_cambiar"] = bool(datos.get("debe_cambiar"))
    return datos


LIMITE_EVENTOS = 5000  # la bitácora se poda para que el archivo no crezca sin control


def registrar_evento(conn, accion, actor="", actor_id=None, objetivo="", detalle="", zona=None):
    """Bitácora de auditoría. Nunca recibe contraseñas."""
    with _LOCK:
        conn.execute(
            "INSERT INTO eventos (fecha, actor, actor_id, accion, objetivo, detalle) VALUES (?,?,?,?,?,?)",
            (ahora(zona).isoformat(timespec="seconds"), str(actor or ""),
             int(actor_id) if actor_id else None, str(accion), str(objetivo or ""), str(detalle or "")))
        total = int(conn.execute("SELECT COUNT(*) FROM eventos").fetchone()[0])
        if total > LIMITE_EVENTOS:
            conn.execute("DELETE FROM eventos WHERE id <= (SELECT MAX(id) - ? FROM eventos)", (LIMITE_EVENTOS,))
        conn.commit()


def listar_eventos(conn, limite=200):
    with _LOCK:
        filas = conn.execute(
            "SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (int(limite),)).fetchall()
    return [dict(f) for f in filas]


# ==========================================
# CONSULTAS DE USUARIOS
# ==========================================
def obtener_usuario(conn, id_usuario):
    with _LOCK:
        fila = conn.execute("SELECT * FROM usuarios WHERE id = ?", (int(id_usuario),)).fetchone()
    return _fila_a_dict(fila)


def obtener_por_login(conn, usuario):
    with _LOCK:
        fila = conn.execute("SELECT * FROM usuarios WHERE usuario_norm = ?",
                            (normalizar(usuario),)).fetchone()
    return _fila_a_dict(fila)


def listar_usuarios(conn, incluir_inactivos=True):
    consulta = "SELECT * FROM usuarios"
    if not incluir_inactivos:
        consulta += " WHERE activo = 1"
    consulta += " ORDER BY (rol != 'admin'), usuario COLLATE NOCASE"
    with _LOCK:
        filas = conn.execute(consulta).fetchall()
    return [_fila_a_dict(f) for f in filas]


def contar_admins_activos(conn, excluir_id=None):
    consulta = "SELECT COUNT(*) FROM usuarios WHERE rol = 'admin' AND activo = 1"
    parametros = []
    if excluir_id is not None:
        consulta += " AND id != ?"
        parametros.append(int(excluir_id))
    with _LOCK:
        return int(conn.execute(consulta, parametros).fetchone()[0])


def resumen(conn):
    """Métricas para el tablero del panel de administración."""
    usuarios = listar_usuarios(conn)
    bloqueados = [u for u in usuarios if _esta_bloqueado(u)]
    return {
        "total": len(usuarios),
        "activos": sum(1 for u in usuarios if u["activo"]),
        "inactivos": sum(1 for u in usuarios if not u["activo"]),
        "admins_activos": sum(1 for u in usuarios if u["activo"] and u["rol"] == "admin"),
        "por_cambiar": sum(1 for u in usuarios if u["activo"] and u["debe_cambiar"]),
        "bloqueados": len(bloqueados),
    }


def _esta_bloqueado(usuario, momento=None):
    if not usuario or not usuario.get("bloqueado_hasta"):
        return False
    limite = _parsear_fecha(usuario["bloqueado_hasta"])
    return bool(limite and limite > (momento or datetime.now(timezone.utc)))


def _parsear_fecha(texto):
    try:
        valor = datetime.fromisoformat(str(texto))
    except (TypeError, ValueError):
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


# ==========================================
# ADMINISTRADOR INICIAL (SEMILLA DESDE SECRETS)
# ==========================================
ACCIONES_SEMILLA = {
    "creado": "Se creó la cuenta de administrador inicial.",
    "actualizado": "Se restableció la contraseña del administrador inicial.",
    "existente": "El administrador inicial ya existe; no se tocó su contraseña.",
    "sin_datos": "No hay credenciales de administrador inicial configuradas.",
    "sin_password": "Falta la contraseña del administrador inicial.",
    "invalido": "El usuario o la contraseña del administrador inicial no son válidos.",
    "sin_admins": "No existe ningún administrador activo y no hay credenciales configuradas "
                  "para crear uno (en Streamlit Community Cloud se definen en los Secrets).",
}


def sembrar_admin_inicial(conn, usuario, password, nombre="", correo="", forzar=False, zona=None,
                          origen="los Secrets"):
    """Crea el administrador inicial (idempotente) desde una fuente externa de confianza.

    La usan los Secrets de la app y `herramientas/crear_admin.py`; `origen` solo sirve
    para redactar el mensaje.

    - Si el usuario no existe: se crea con esa contraseña y se marca `debe_cambiar = True`
      para que la cambie al primer inicio de sesión.
    - Si ya existe: **no** se toca su contraseña (no es una puerta trasera permanente),
      salvo que `forzar=True`, que la restablece y desbloquea la cuenta.

    Devuelve (estado, mensaje) con estado en {"creado", "actualizado", "existente",
    "sin_datos", "sin_password", "invalido", "sin_admins"}.
    """
    if not usuario:
        # Sin credenciales no se inventa nada: solo se reporta el estado.
        estado = "sin_admins" if contar_admins_activos(conn) == 0 else "sin_datos"
        return estado, ACCIONES_SEMILLA[estado]
    if not password:
        return "sin_password", ACCIONES_SEMILLA["sin_password"]

    usuario = str(usuario).strip()
    if not _RE_USUARIO.match(normalizar(usuario)):
        return "invalido", ACCIONES_SEMILLA["invalido"]
    try:
        validar_password(password, usuario)
    except ErrorAuth as error:
        return "invalido", f"{ACCIONES_SEMILLA['invalido']} {error}"

    existente = obtener_por_login(conn, usuario)
    momento = ahora(zona).isoformat(timespec="seconds")
    if existente is None:
        try:
            creado = crear_usuario(conn, actor="semilla", usuario=usuario, password=password,
                                   nombre=nombre or usuario, correo=correo, rol="admin",
                                   debe_cambiar=True, zona=zona, interno=True)
        except ErrorAuth as error:
            return "invalido", f"{ACCIONES_SEMILLA['invalido']} {error}"
        registrar_evento(conn, "admin_sembrado", actor="semilla", actor_id=creado["id"],
                         objetivo=creado["usuario"], detalle=ACCIONES_SEMILLA["creado"], zona=zona)
        return "creado", f"Administrador inicial `{creado['usuario']}` creado desde {origen}."

    if not forzar:
        return "existente", ACCIONES_SEMILLA["existente"]
    if existente["rol"] != "admin":
        return "invalido", "El usuario del administrador inicial existe con otro rol; revísalo en el panel."
    with _LOCK:
        conn.execute("UPDATE usuarios SET password_hash = ?, debe_cambiar = 1, activo = 1, "
                     "intentos_fallidos = 0, bloqueado_hasta = '', password_cambiada_en = ?, "
                     "actualizado_en = ? WHERE id = ?",
                     (hash_password(password), momento, momento, int(existente["id"])))
        conn.commit()
    registrar_evento(conn, "admin_password_restablecida", actor="semilla", actor_id=existente["id"],
                     objetivo=existente["usuario"], detalle=ACCIONES_SEMILLA["actualizado"], zona=zona)
    return "actualizado", (f"Contraseña de `{existente['usuario']}` restablecida desde {origen}; "
                           "se pedirá cambiarla al iniciar sesión.")


# ==========================================
# AUTENTICACIÓN
# ==========================================
def autenticar(conn, usuario, password, zona=None):
    """Valida credenciales. Devuelve (usuario_dict | None, mensaje).

    Aplica bloqueo temporal tras varios intentos fallidos y registra la auditoría.
    """
    login = str(usuario or "").strip()
    if not login or not password:
        return None, "Captura tu usuario y tu contraseña."
    with _LOCK:
        fila = conn.execute("SELECT * FROM usuarios WHERE usuario_norm = ?",
                            (normalizar(login),)).fetchone()

    if fila is None:
        # Verificación falsa (mismo costo que una real) para no delatar por tiempo de
        # respuesta si el usuario existe o no.
        hashlib.scrypt(b"verificacion-de-relleno", salt=b"0" * SCRYPT_SALT_BYTES, n=SCRYPT_N,
                       r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN, maxmem=64 * 1024 * 1024)
        registrar_evento(conn, "inicio_sesion_fallido", objetivo=login,
                         detalle="El usuario no existe.", zona=zona)
        return None, "Usuario o contraseña incorrectos."

    datos = _fila_a_dict(fila)
    if not datos["activo"]:
        registrar_evento(conn, "inicio_sesion_fallido", objetivo=datos["usuario"],
                         detalle="La cuenta está desactivada.", zona=zona)
        return None, "La cuenta está desactivada. Pide a un administrador que la reactive."

    bloqueado_hasta = _parsear_fecha(datos.get("bloqueado_hasta"))
    if bloqueado_hasta and bloqueado_hasta > ahora(zona):
        restante = max(1, int((bloqueado_hasta - ahora(zona)).total_seconds() // 60) + 1)
        registrar_evento(conn, "inicio_sesion_bloqueado", objetivo=datos["usuario"],
                         detalle="Intento durante el bloqueo temporal.", zona=zona)
        return None, (f"La cuenta está bloqueada temporalmente por intentos fallidos. "
                      f"Intenta de nuevo en {restante} minuto(s).")

    valida, rehash = verificar_password(password, fila["password_hash"])
    momento = ahora(zona).isoformat(timespec="seconds")
    if not valida:
        intentos = int(datos["intentos_fallidos"] or 0) + 1
        bloqueo = ""
        if intentos >= MAX_INTENTOS_FALLIDOS:
            bloqueo = (ahora(zona) + timedelta(minutes=BLOQUEO_MINUTOS)).isoformat(timespec="seconds")
            intentos = 0
        with _LOCK:
            conn.execute("UPDATE usuarios SET intentos_fallidos = ?, bloqueado_hasta = ? WHERE id = ?",
                         (intentos, bloqueo, int(datos["id"])))
            conn.commit()
        detalle = (f"Bloqueo temporal de {BLOQUEO_MINUTOS} min tras {MAX_INTENTOS_FALLIDOS} intentos."
                   if bloqueo else f"Intento fallido {intentos}/{MAX_INTENTOS_FALLIDOS}.")
        registrar_evento(conn, "inicio_sesion_fallido", objetivo=datos["usuario"],
                         detalle=detalle, zona=zona)
        if bloqueo:
            return None, (f"Contraseña incorrecta. La cuenta se bloqueó {BLOQUEO_MINUTOS} minutos "
                          "por intentos fallidos.")
        return None, "Usuario o contraseña incorrectos."

    if rehash:
        with _LOCK:
            conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                         (hash_password(password), int(datos["id"])))
            conn.commit()
    with _LOCK:
        conn.execute("UPDATE usuarios SET ultimo_acceso = ?, intentos_fallidos = 0, bloqueado_hasta = '' "
                     "WHERE id = ?", (momento, int(datos["id"])))
        conn.commit()
    registrar_evento(conn, "inicio_sesion", actor=datos["usuario"], actor_id=datos["id"],
                     objetivo=datos["usuario"], detalle=f"Rol: {etiqueta_rol(datos['rol'])}.", zona=zona)
    return obtener_usuario(conn, datos["id"]), "Sesión iniciada."


# ==========================================
# SESIONES FIRMADAS
# ==========================================
def secreto_sesion(conn, secreto_configurado=None):
    """Clave para firmar las sesiones.

    Se toma de `[auth] session_secret` en los Secrets (recomendado). Si no está
    configurada, se genera una y se guarda en la tabla `meta` de la base para que
    las sesiones sobrevivan a los reinicios de la app (Streamlit Cloud reinicia en
    cada respaldo). Esa clave nunca es una contraseña: si el repositorio pudiera
    ser público, configura `session_secret` en los Secrets.
    """
    if secreto_configurado:
        return str(secreto_configurado)
    with _LOCK:
        fila = conn.execute("SELECT valor FROM meta WHERE clave = 'secreto_sesiones'").fetchone()
        if fila and fila["valor"]:
            return fila["valor"]
        generado = secrets.token_urlsafe(32)
        conn.execute("INSERT OR REPLACE INTO meta (clave, valor) VALUES ('secreto_sesiones', ?)", (generado,))
        conn.commit()
    return generado


def rotar_secreto_sesion(conn, actor=None, zona=None):
    """Invalida todas las sesiones activas (cambia la clave de firma)."""
    with _LOCK:
        conn.execute("DELETE FROM meta WHERE clave = 'secreto_sesiones'")
        conn.commit()
    registrar_evento(conn, "sesiones_revocadas", actor=(actor or {}).get("usuario", ""),
                     actor_id=(actor or {}).get("id"), detalle="Se rotó la clave de firma de sesiones.", zona=zona)
    return secreto_sesion(conn)


def _b64(texto_bytes):
    return base64.urlsafe_b64encode(texto_bytes).decode("ascii").rstrip("=")


def _de_b64(texto):
    relleno = "=" * (-len(texto) % 4)
    return base64.urlsafe_b64decode(texto + relleno)


def crear_token(secreto, usuario, minutos=None):
    """Token `payload.firma` (HMAC-SHA256) con la identidad y la expiración."""
    ttl = int(minutos if minutos is not None else TTL_SESION_MINUTOS)
    emision = int(time.time())
    payload = {"v": 1, "sub": int(usuario["id"]), "u": str(usuario["usuario"]),
               "r": str(usuario["rol"]), "iat": emision, "exp": emision + ttl * 60}
    cuerpo = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    firma = _b64(hmac.new(str(secreto).encode("utf-8"), cuerpo.encode("ascii"), hashlib.sha256).digest())
    return f"{cuerpo}.{firma}"


def leer_token(secreto, token):
    """Devuelve el payload si la firma y la expiración son válidas; si no, None."""
    if not token or "." not in str(token):
        return None
    cuerpo, _, firma = str(token).partition(".")
    esperada = _b64(hmac.new(str(secreto).encode("utf-8"), cuerpo.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(firma, esperada):
        return None
    try:
        payload = json.loads(_de_b64(cuerpo).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    return payload


def usuario_de_token(conn, secreto, token):
    """Sesión vigente a partir del token: revalida contra la base (activo y rol actuales)."""
    payload = leer_token(secreto, token)
    if not payload:
        return None
    usuario = obtener_usuario(conn, payload.get("sub"))
    if not usuario or not usuario["activo"]:
        return None
    return usuario


# ==========================================
# GESTIÓN DE USUARIOS (PANEL DE ADMINISTRACIÓN)
# ==========================================
def _validar_login_usuario(usuario):
    usuario = str(usuario or "").strip()
    if not _RE_USUARIO.match(normalizar(usuario)):
        raise ErrorAuth("El usuario debe tener de 3 a 32 caracteres: letras, números, punto, guion o guion bajo.")
    return usuario


def _validar_rol(rol):
    if rol not in ROLES:
        raise ErrorAuth(f"Rol inválido: {rol!r}. Usa uno de {', '.join(ROLES)}.")
    return rol


def _validar_correo(correo):
    correo = str(correo or "").strip()
    if correo and not _RE_CORREO.match(correo):
        raise ErrorAuth("El correo no tiene un formato válido.")
    return correo


def _exigir_admin(conn, actor):
    """Quien administra debe ser un administrador activo (dict, id o nombre de usuario)."""
    if isinstance(actor, dict):
        administrador = obtener_usuario(conn, actor.get("id"))
    elif isinstance(actor, int) or (isinstance(actor, str) and str(actor).isdigit()):
        administrador = obtener_usuario(conn, int(actor))
    else:
        administrador = obtener_por_login(conn, actor)
    if not administrador:
        raise ErrorAuth("La sesión ya no es válida; vuelve a iniciar sesión.")
    if administrador["rol"] != "admin" or not administrador["activo"]:
        raise ErrorRegla("Solo un administrador activo puede gestionar usuarios.")
    return administrador


def _exigir_ultimo_admin(conn, objetivo, accion):
    """Impide dejar el sistema sin administradores activos."""
    if objetivo["rol"] == "admin" and objetivo["activo"] and contar_admins_activos(conn, excluir_id=objetivo["id"]) == 0:
        raise ErrorRegla(f"No se puede {accion} a `{objetivo['usuario']}`: es el último administrador "
                         "activo del sistema. Crea o activa otro administrador antes de continuar.")


def crear_usuario(conn, actor, usuario, password=None, nombre="", correo="", rol="usuario",
                  debe_cambiar=True, zona=None, interno=False):
    """Crea una cuenta. Si no se da contraseña, se genera una temporal.

    `interno=True` es exclusivo de la semilla desde los Secrets (no hay sesión
    todavía); el resto de llamadas exigen un administrador activo.
    Devuelve el dict del usuario (incluye `password_temporal` cuando se generó).
    """
    administrador = {"id": None, "usuario": "semilla"} if interno else _exigir_admin(conn, actor)
    usuario = _validar_login_usuario(usuario)
    rol = _validar_rol(rol)
    correo = _validar_correo(correo)
    temporal = None
    if password:
        validar_password(password, usuario)
    else:
        temporal = generar_password_temporal()
        password = temporal
    if obtener_por_login(conn, usuario):
        raise ErrorAuth(f"El usuario `{usuario}` ya existe.")

    momento = ahora(zona).isoformat(timespec="seconds")
    with _LOCK:
        cursor = conn.execute(
            "INSERT INTO usuarios (usuario, usuario_norm, nombre, correo, rol, password_hash, debe_cambiar, "
            "activo, creado_en, creado_por, actualizado_en, password_cambiada_en) "
            "VALUES (?,?,?,?,?,?,?,1,?,?,?,?)",
            (usuario, normalizar(usuario), str(nombre or usuario).strip(), correo, rol,
             hash_password(password), 1 if debe_cambiar else 0, momento,
             administrador["usuario"], momento, momento))
        conn.commit()
        nuevo_id = int(cursor.lastrowid)
    registrar_evento(conn, "usuario_creado", actor=administrador["usuario"], actor_id=administrador["id"],
                     objetivo=usuario, detalle=f"Rol: {etiqueta_rol(rol)}"
                     + ("; contraseña temporal." if temporal else "; contraseña definida por el administrador.")
                     + ("; deberá cambiarla al entrar." if debe_cambiar else ""), zona=zona)
    datos = obtener_usuario(conn, nuevo_id)
    if temporal:
        datos["password_temporal"] = temporal
    return datos


def actualizar_datos(conn, actor, id_usuario, nombre=None, correo=None, zona=None):
    """Actualiza nombre y/o correo (nunca la contraseña ni el rol)."""
    administrador = _exigir_admin(conn, actor)
    objetivo = obtener_usuario(conn, id_usuario)
    if not objetivo:
        raise ErrorAuth("El usuario indicado ya no existe.")
    campos, valores, cambios = [], [], []
    if nombre is not None:
        campos.append("nombre = ?")
        valores.append(str(nombre).strip())
        cambios.append(f"nombre → {str(nombre).strip()!r}")
    if correo is not None:
        correo = _validar_correo(correo)
        campos.append("correo = ?")
        valores.append(correo)
        cambios.append(f"correo → {correo!r}")
    if not campos:
        return objetivo
    campos.append("actualizado_en = ?")
    valores.append(ahora(zona).isoformat(timespec="seconds"))
    valores.append(int(objetivo["id"]))
    with _LOCK:
        conn.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ?", valores)
        conn.commit()
    registrar_evento(conn, "usuario_actualizado", actor=administrador["usuario"], actor_id=administrador["id"],
                     objetivo=objetivo["usuario"], detalle="; ".join(cambios), zona=zona)
    return obtener_usuario(conn, id_usuario)


def cambiar_rol(conn, actor, id_usuario, nuevo_rol, zona=None):
    """Cambia el rol. No permite degradar al último administrador activo ni a uno mismo."""
    administrador = _exigir_admin(conn, actor)
    objetivo = obtener_usuario(conn, id_usuario)
    if not objetivo:
        raise ErrorAuth("El usuario indicado ya no existe.")
    nuevo_rol = _validar_rol(nuevo_rol)
    if nuevo_rol == objetivo["rol"]:
        return objetivo
    if objetivo["id"] == administrador["id"]:
        raise ErrorRegla("No puedes cambiar tu propio rol; pídelo a otro administrador.")
    if nuevo_rol != "admin":
        _exigir_ultimo_admin(conn, objetivo, "quitarle el rol de administrador")
    momento = ahora(zona).isoformat(timespec="seconds")
    with _LOCK:
        conn.execute("UPDATE usuarios SET rol = ?, actualizado_en = ? WHERE id = ?",
                     (nuevo_rol, momento, int(objetivo["id"])))
        conn.commit()
    registrar_evento(conn, "rol_cambiado", actor=administrador["usuario"], actor_id=administrador["id"],
                     objetivo=objetivo["usuario"],
                     detalle=f"{etiqueta_rol(objetivo['rol'])} → {etiqueta_rol(nuevo_rol)}", zona=zona)
    return obtener_usuario(conn, id_usuario)


def establecer_activo(conn, actor, id_usuario, activo, zona=None):
    """Activa o desactiva una cuenta (no la elimina). Protege al último admin y a uno mismo."""
    administrador = _exigir_admin(conn, actor)
    objetivo = obtener_usuario(conn, id_usuario)
    if not objetivo:
        raise ErrorAuth("El usuario indicado ya no existe.")
    activo = bool(activo)
    if activo == objetivo["activo"]:
        return objetivo
    if not activo:
        if objetivo["id"] == administrador["id"]:
            raise ErrorRegla("No puedes desactivar tu propia cuenta.")
        _exigir_ultimo_admin(conn, objetivo, "desactivar")
    momento = ahora(zona).isoformat(timespec="seconds")
    with _LOCK:
        conn.execute("UPDATE usuarios SET activo = ?, actualizado_en = ?, bloqueado_hasta = '', "
                     "intentos_fallidos = 0 WHERE id = ?", (1 if activo else 0, momento, int(objetivo["id"])))
        conn.commit()
    registrar_evento(conn, "usuario_activado" if activo else "usuario_desactivado",
                     actor=administrador["usuario"], actor_id=administrador["id"],
                     objetivo=objetivo["usuario"],
                     detalle="Cuenta habilitada." if activo else "Cuenta deshabilitada (acceso bloqueado).", zona=zona)
    return obtener_usuario(conn, id_usuario)


def eliminar_usuario(conn, actor, id_usuario, zona=None):
    """Elimina una cuenta de forma definitiva (los eventos de auditoría se conservan)."""
    administrador = _exigir_admin(conn, actor)
    objetivo = obtener_usuario(conn, id_usuario)
    if not objetivo:
        raise ErrorAuth("El usuario indicado ya no existe.")
    if objetivo["id"] == administrador["id"]:
        raise ErrorRegla("No puedes eliminar tu propia cuenta.")
    _exigir_ultimo_admin(conn, objetivo, "eliminar")
    with _LOCK:
        conn.execute("DELETE FROM usuarios WHERE id = ?", (int(objetivo["id"]),))
        conn.commit()
    registrar_evento(conn, "usuario_eliminado", actor=administrador["usuario"], actor_id=administrador["id"],
                     objetivo=objetivo["usuario"], detalle=f"Rol: {etiqueta_rol(objetivo['rol'])}.", zona=zona)
    return True


def restablecer_password(conn, actor, id_usuario, password=None, debe_cambiar=True, zona=None):
    """Asigna una contraseña nueva (generada si no se da). Devuelve (usuario, temporal|None)."""
    administrador = _exigir_admin(conn, actor)
    objetivo = obtener_usuario(conn, id_usuario)
    if not objetivo:
        raise ErrorAuth("El usuario indicado ya no existe.")
    temporal = None
    if password:
        validar_password(password, objetivo["usuario"])
    else:
        temporal = generar_password_temporal()
        password = temporal
    momento = ahora(zona).isoformat(timespec="seconds")
    with _LOCK:
        conn.execute("UPDATE usuarios SET password_hash = ?, debe_cambiar = ?, activo = 1, "
                     "intentos_fallidos = 0, bloqueado_hasta = '', password_cambiada_en = ?, "
                     "actualizado_en = ? WHERE id = ?",
                     (hash_password(password), 1 if debe_cambiar else 0, momento, momento,
                      int(objetivo["id"])))
        conn.commit()
    registrar_evento(conn, "password_restablecida", actor=administrador["usuario"],
                     actor_id=administrador["id"], objetivo=objetivo["usuario"],
                     detalle="Contraseña temporal entregada por el administrador." if temporal
                     else "El administrador definió la contraseña.", zona=zona)
    return obtener_usuario(conn, id_usuario), temporal


def cambiar_password_propia(conn, usuario_id, password_actual, password_nueva, zona=None):
    """Cambio de contraseña por el propio usuario: exige la contraseña actual."""
    with _LOCK:
        fila = conn.execute("SELECT * FROM usuarios WHERE id = ?", (int(usuario_id),)).fetchone()
    if not fila:
        raise ErrorAuth("La sesión ya no es válida.")
    if not verificar_password(password_actual, fila["password_hash"])[0]:
        registrar_evento(conn, "password_cambio_fallido", actor=fila["usuario"], actor_id=fila["id"],
                         objetivo=fila["usuario"], detalle="La contraseña actual no coincide.", zona=zona)
        raise ErrorAuth("La contraseña actual no es correcta.")
    if password_actual == password_nueva:
        raise ErrorAuth("La contraseña nueva debe ser distinta a la actual.")
    validar_password(password_nueva, fila["usuario"])
    momento = ahora(zona).isoformat(timespec="seconds")
    with _LOCK:
        conn.execute("UPDATE usuarios SET password_hash = ?, debe_cambiar = 0, password_cambiada_en = ?, "
                     "actualizado_en = ? WHERE id = ?",
                     (hash_password(password_nueva), momento, momento, int(fila["id"])))
        conn.commit()
    registrar_evento(conn, "password_cambiada", actor=fila["usuario"], actor_id=fila["id"],
                     objetivo=fila["usuario"], detalle="Cambio de contraseña por el propio usuario.", zona=zona)
    return obtener_usuario(conn, int(fila["id"]))


def asegurar_administradores(conn):
    """Diagnóstico para la pantalla de bloqueo: ¿hay algún administrador activo?"""
    admins = contar_admins_activos(conn)
    return admins, (admins > 0)
