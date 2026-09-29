#!/usr/bin/env python3
"""Crea el administrador inicial de NEMET desde la terminal.

Para qué sirve
--------------
En la app **no hay registro abierto**: las cuentas las crea un administrador desde el
panel 🛡️ Administración de Usuarios. Este asistente resuelve el único caso que no se
puede hacer desde la web: la **primera** cuenta de un servidor local o un VPS donde no
se usan los Secrets de Streamlit.

Por seguridad, se niega a ejecutarse si ya existe algún administrador activo
(usa `--forzar` solo si de verdad lo necesitas, p. ej. si perdiste el acceso).

La contraseña se captura oculta (no queda en el historial de la terminal ni en `ps`).

Ejemplos
--------
    python herramientas/crear_admin.py --usuario jefe
    python herramientas/crear_admin.py --usuario jefe --nombre "Jorge" --correo jefe@nemet.mx
    python herramientas/crear_admin.py --usuario jefe --generar        # contraseña temporal
    python herramientas/crear_admin.py --usuario jefe --db /ruta/nemet_usuarios.db
"""
import argparse
import getpass
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import auth_nemet as auth  # noqa: E402

DB_POR_OMISION = os.environ.get("NEMET_DB_USUARIOS") or os.path.join(RAIZ, "nemet_usuarios.db")


def main():
    parser = argparse.ArgumentParser(
        description="Crea el administrador inicial del Sistema Maestro NEMET.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--usuario", required=True,
                        help="Nombre de usuario para iniciar sesión (3-32 caracteres).")
    parser.add_argument("--nombre", default="", help="Nombre completo (opcional).")
    parser.add_argument("--correo", default="", help="Correo de contacto (opcional).")
    parser.add_argument("--db", default=DB_POR_OMISION,
                        help=f"Ruta de la base de usuarios (por omisión {DB_POR_OMISION}).")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--password", help="Contraseña (si se omite, se pide de forma oculta).")
    grupo.add_argument("--generar", action="store_true",
                       help="Genera una contraseña temporal aleatoria y la muestra una sola vez.")
    parser.add_argument("--forzar", action="store_true",
                        help="Continúa aunque ya exista un administrador activo (emergencias).")
    args = parser.parse_args()

    conn = auth.conectar(args.db)
    auth.inicializar_db(conn)

    admins = auth.contar_admins_activos(conn)
    if admins and not args.forzar:
        existentes = ", ".join(u["usuario"] for u in auth.listar_usuarios(conn)
                               if u["activo"] and u["rol"] == "admin")
        print(f"✋ Ya existe {admins} administrador(es) activo(s): {existentes}.")
        print("   No hay nada que hacer: entra con tu cuenta y crea más usuarios desde el")
        print("   panel 🛡️ Administración de Usuarios (o usa --forzar si perdiste el acceso).")
        return 1

    password = args.password
    if args.generar:
        password = auth.generar_password_temporal()
    elif not password:
        try:
            password = getpass.getpass("Contraseña del administrador (no se muestra): ")
            repetida = getpass.getpass("Repite la contraseña: ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado: no se creó ninguna cuenta.")
            return 1
        if password != repetida:
            print("✋ Las contraseñas no coinciden; inténtalo de nuevo.")
            return 1

    estado, mensaje = auth.sembrar_admin_inicial(
        conn, args.usuario, password, nombre=args.nombre or args.usuario, correo=args.correo,
        forzar=args.forzar, origen="la línea de comandos")

    if estado not in ("creado", "actualizado"):
        print(f"✋ No se pudo crear el administrador: {mensaje}")
        return 1

    print(f"✅ {mensaje}")
    print(f"   Base de usuarios: {os.path.abspath(args.db)}")
    if args.generar:
        print(f"   Contraseña temporal: {password}")
    print("   Al iniciar sesión, la app pedirá cambiar la contraseña (es provisional).")
    if not args.generar and not args.password:
        print("   Consejo: si usas Streamlit Community Cloud, define también [auth] en los")
        print("   Secrets para que el administrador se recree si el disco se reinicia.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
