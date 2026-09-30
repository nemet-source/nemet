"""Descubrimiento y clasificación de negocios para la prospección NEMET.

Usa fichas de negocios publicadas en OpenStreetMap (Overpass + Nominatim)
o giros declarados en archivos CSV propios.
El puntaje combina afinidad de giro, señales públicas y perfil con fuente; no predice compras.
Sin Streamlit ni acceso al Excel: las búsquedas, reglas y deduplicación se pueden probar
sin red ni modificar los datos de la empresa.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
import unicodedata
from datetime import date
from functools import lru_cache
from urllib.parse import parse_qs, quote, urlparse

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Identificar la aplicación ante los servidores comunitarios; no hacer búsquedas en segundo plano.
CABECERAS = {
    "User-Agent": "NEMET-Prospeccion/1.0 (https://github.com/nemet-source/nemet)",
    "Accept": "application/json",
}
MAX_RESULTADOS = 300
FUENTE_OSM = "© OpenStreetMap contributors (ODbL)"
# Nominatim público: como máximo 1 petición por segundo en este proceso.
_GEO_LOCK = threading.Lock()
_ULTIMA_GEO = 0.0


def respaldo_github_privado(repo: str, token: str) -> tuple[bool, str]:
    """Verifica con GitHub que el destino del Excel/CRM es PRIVADO antes de subirlo.

    Fallar cerrado: un fallo de red, token sin acceso o respuesta incompleta no puede
    causar que notas, contactos comerciales ni la base de usuarios se publiquen.
    Nunca se incluye el token en URLs ni en mensajes de error.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(repo or "")):
        return False, "Configura `[git] repo` con el nombre de un repositorio privado (organización/repositorio)."
    try:
        respuesta = requests.get(
            f"https://api.github.com/repos/{repo}",
            headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
            timeout=10,
        )
        respuesta.raise_for_status()
        info = respuesta.json()
        if (isinstance(info, dict) and info.get("private") is True
                and str(info.get("full_name", "")).casefold() == repo.casefold()):
            return True, ""
        if isinstance(info, dict) and info.get("private") is False:
            return False, "Respaldo bloqueado: el repositorio de GitHub es PÚBLICO. Usa un repositorio PRIVADO para el Excel y el CRM."
    except (requests.RequestException, ValueError, TypeError):
        pass
    return False, ("Respaldo bloqueado: no se pudo comprobar que el repositorio de GitHub es privado. "
                   "Verifica el acceso del token y reintenta o descarga un respaldo local.")


# Flujo de ventas visible primero; conservar estados anteriores y de exclusión para
# que una actualización no reescriba fichas antiguas ni reactive "No contactar".
ESTADOS_COMERCIALES = ("Nuevo", "Contactado", "Interesado", "Cotización", "Seguimiento", "Cliente")
ESTADOS_ADICIONALES = ("Por investigar", "En seguimiento", "Descartado", "No contactar")
ESTADOS = ESTADOS_COMERCIALES + ESTADOS_ADICIONALES
ESTADOS_CERRADOS = ("Cliente", "Descartado", "No contactar")
TAMANOS = ("Sin dato", "Micro", "Pequeño", "Mediano", "Grande")
CLIENTELAS = ("Sin dato", "Empresas", "Consumidor final", "Mixto")
# Se agregan columnas al final: los Excel guardados por la primera versión se leen sin migración manual.
COLUMNAS = (
    "Clave", "Empresa", "Segmento", "Prioridad", "Puntaje", "Motivo", "Productos",
    "Ciudad", "Dirección", "Teléfono", "Correo", "Sitio_web", "Fuente", "URL_fuente",
    "Fecha_alta", "Estado", "Notas", "Último_contacto",
    "Zona", "Latitud", "Longitud", "Distancia_km", "WhatsApp", "Redes",
    "Productos_negocio", "Tamaño", "Tipo_clientela", "Última_actividad", "Fuente_perfil",
    "Próximo_seguimiento",
)
# Solo estos campos se editan desde la ficha; se comparan antes de escribir para evitar
# sobrescribir cambios de otra sesión. El puntaje/razón se recalculan al editar el perfil.
CAMPOS_SEGUIMIENTO = ("Estado", "Notas", "Último_contacto", "Próximo_seguimiento",
                      "Productos_negocio", "Tamaño", "Tipo_clientela", "Última_actividad", "Fuente_perfil")

# Solo giros vinculados con productos que NEMET realmente vende. Los valores OSM son
# cerrados (nunca se interpolan cadenas suministradas por el visitante en Overpass).
SECTORES = {
    "aplicadores": {
        "nombre": "Aplicadores y talleres de pisos",
        "osm": {"craft": ("floorer", "tiler", "painter")},
        "puntaje": 66,
        "motivo": "Trabajo en pisos o acabados; posible uso de sistemas epóxicos.",
        "productos": ("EPOXY PISOS", "EPOXY PRIMER", "EPO-PAINT"),
    },
    "carpinterias": {
        "nombre": "Carpinterías y ebanisterías",
        "osm": {"craft": ("carpenter", "cabinet_maker", "woodworker")},
        "puntaje": 65,
        "motivo": "Trabajo de madera; posible uso de resinas de colada y pigmentos.",
        "productos": ("EPO-DEEP", "EPO-FAST", "EPOXY TINTA"),
    },
    "mobiliario": {
        "nombre": "Fabricantes de muebles y mesas",
        "osm": {"craft": ("furniture_maker", "table_maker")},
        "puntaje": 65,
        "motivo": "Fabrica mobiliario; posible uso de resina para mesas y acabados.",
        "productos": ("EPO-DEEP", "EPO-FAST", "POLIURETANO"),
    },
    "artesanos": {
        "nombre": "Artesanos de madera y resina",
        "osm": {"craft": ("wood_carver", "sculptor", "artist")},
        "puntaje": 55,
        "motivo": "Trabajo artesanal; confirmar si usa madera o resina antes de contactar.",
        "productos": ("EPO-DEEP", "EPO-FAST", "EPOXY TINTA"),
    },
    "manualidades": {
        "nombre": "Tiendas de manualidades",
        "osm": {"shop": ("craft", "art_supplies")},
        "puntaje": 52,
        "motivo": "Comercio de materiales creativos; posible canal de venta para resinas y pigmentos.",
        "productos": ("EPO-FAST", "EPO-DEEP", "EPOXY TINTA"),
    },
    "decoracion": {
        "nombre": "Decoración y diseño de interiores",
        "osm": {"office": ("interior_design",), "shop": ("interior_decoration", "decoration")},
        "puntaje": 52,
        "motivo": "Puede especificar acabados decorativos, aunque no necesariamente compra directamente.",
        "productos": ("EPOXY PISOS", "EPOXY-HOJUELA", "POLIURETANO"),
    },
    "restauracion": {
        "nombre": "Restauradores y tapicerías",
        "osm": {"craft": ("restorer", "restoration", "upholsterer")},
        "puntaje": 55,
        "motivo": "Restaura superficies o muebles; confirmar su trabajo antes de ofrecer resinas.",
        "productos": ("EPO-DEEP", "EPO-FAST", "POLIURETANO"),
    },
    "constructoras": {
        "nombre": "Constructoras y acabados",
        "osm": {"office": ("construction_company",), "craft": ("builder", "concrete", "plasterer")},
        "puntaje": 60,
        "motivo": "Actividad de construcción; posible especificación de pisos y selladores.",
        "productos": ("EPOXY PISOS", "EPOXY PRIMER", "POLIURETANO"),
    },
    "distribuidores": {
        "nombre": "Distribuidores de materiales",
        "osm": {"shop": ("hardware", "doityourself", "trade", "building_materials", "flooring", "paint")},
        "puntaje": 56,
        "motivo": "Vende materiales para obra; posible canal de distribución de epóxicos.",
        "productos": ("EPOXY PISOS", "EPO-DEEP", "EPO-PAINT"),
    },
    "industria": {
        "nombre": "Talleres y manufactura",
        "osm": {"industrial": ("factory", "manufacturing")},
        "puntaje": 55,
        "motivo": "Actividad industrial; posible necesidad de pisos de trabajo.",
        "productos": ("EPOXY PISOS", "EPOXY PRIMER", "EPO-PAINT"),
    },
    "arquitectura": {
        "nombre": "Despachos de arquitectura",
        "osm": {"office": ("architect",)},
        "puntaje": 50,
        "motivo": "Puede especificar materiales en proyectos, aunque no necesariamente compra directamente.",
        "productos": ("EPOXY PISOS", "EPOXY-HOJUELA", "POLIURETANO"),
    },
}

# Al importar CSV se usa únicamente el giro declarado; nunca se adivina por el nombre
# del negocio. Reglas específicas antes que genéricas (p. ej. fabricante antes que tienda).
PALABRAS_GIRO = (
    (r"restaurad|restauracion|tapicer", "restauracion"),
    (r"fabricante de (muebles|mesas)|fabricacion de (muebles|mesas)|fabrica de (muebles|mesas)|taller de (muebles|mesas)", "mobiliario"),
    (r"carpint|ebanist|mobiliario a medida", "carpinterias"),
    (r"artesan|escult|tallado de madera|arte en resina|mesas de resina|river table", "artesanos"),
    (r"manualidad|tienda de arte|insumos para artesano", "manualidades"),
    (r"decorad|interiorismo|diseno de interiores", "decoracion"),
    (r"arquitect", "arquitectura"),
    (r"ferreter|materiales de construccion|distribuidor|tienda de pinturas", "distribuidores"),
    (r"constructor|construccion|albanil|concreto|obra civil", "constructoras"),
    (r"manufactur|nave industrial|taller industrial|fabrica", "industria"),
    (r"epoxi|epoxy|pisos?|recubrimiento|pintura|pintor|acabados?", "aplicadores"),
)


class ErrorBusqueda(Exception):
    """Error de red, ubicación o fuente de datos apto para mostrar en la interfaz."""


def normalizar(valor):
    """Comparación de empresas/ciudades insensible a tildes, signos y mayúsculas."""
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def texto(valor, limite=240):
    """Elimina controles de campos externos para mostrarlos y persistirlos como texto."""
    if valor is None:
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(valor)).strip()[:limite]


def url_publica(valor):
    sitio = texto(valor, 500).split(";")[0].strip()
    if sitio and "://" not in sitio and "." in sitio and not sitio.lower().startswith(("javascript:", "data:")):
        sitio = "https://" + sitio
    try:
        url = urlparse(sitio)
        if (url.scheme.lower() in ("http", "https") and url.hostname and "." in url.hostname
                and not url.username and not url.password and not re.search(r"\s", sitio)):
            return sitio
    except ValueError:
        pass
    return ""


def telefono_publico(valor):
    numero = texto(valor, 70).split(";")[0].strip()
    return numero if 7 <= len(re.sub(r"\D", "", numero)) <= 16 and re.fullmatch(r"[+\d\s() ./-]+", numero) else ""


def correo_publico(valor):
    correo = texto(valor, 160).split(";")[0].strip()
    return correo if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", correo) else ""


def whatsapp_publico(valor):
    """Acepta solo un número expresamente publicado como WhatsApp (también URL wa.me)."""
    valor = texto(valor, 180).split(";")[0].strip()
    if valor.startswith(("https://", "http://")):
        url = urlparse(valor)
        if url.scheme != "https":
            return ""
        host = (url.hostname or "").lower()
        if host == "wa.me":
            valor = url.path.strip("/")
        elif host == "api.whatsapp.com" and url.path == "/send":
            valor = parse_qs(url.query).get("phone", [""])[0]
        else:
            return ""
    numero = telefono_publico(valor)
    digitos = re.sub(r"\D", "", numero)
    return numero if (len(digitos) == 10 or len(digitos) == 12 and digitos.startswith("52")
                      or len(digitos) == 13 and digitos.startswith("521")) else ""


def enlace_whatsapp(ficha, mensaje):
    """Abre un borrador en wa.me; SIN WhatsApp publicado no se reutiliza el teléfono normal."""
    numero = whatsapp_publico(ficha.get("WhatsApp"))
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) == 10:  # solo se prospecta en México
        digitos = "52" + digitos
    elif len(digitos) == 13 and digitos.startswith("521"):
        digitos = "52" + digitos[3:]  # formato móvil antiguo
    if len(digitos) != 12 or not digitos.startswith("52"):
        return ""
    return f"https://wa.me/{digitos}?text={quote(mensaje, safe='')}"


def coordenadas(latitud, longitud):
    """Par público válido dentro de México; nunca geocodificar domicilios particulares."""
    try:
        lat, lon = float(latitud), float(longitud)
        if math.isfinite(lat) and math.isfinite(lon) and 14 <= lat <= 33.5 and -119 <= lon <= -86:
            return lat, lon
    except (TypeError, ValueError):
        pass
    return None


def enlace_google_maps(ficha):
    """Enlace externo de búsqueda: coordenadas comerciales o dirección + ciudad.

    No geocodifica ni asegura que Google haya verificado el negocio. Si faltan ambos,
    no ofrece un mapa engañoso. Escapa los datos antes de construir el enlace.
    """
    direccion, ciudad = texto(ficha.get("Dirección"), 240), texto(ficha.get("Ciudad"), 120)
    punto = coordenadas(ficha.get("Latitud"), ficha.get("Longitud"))
    if direccion and ciudad:
        consulta = f"{direccion}, {ciudad}, México"
    elif punto:
        consulta = f"{punto[0]:.6f},{punto[1]:.6f}"
    else:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={quote(consulta, safe=',')}"


def distancia_km(origen, destino):
    """Distancia geográfica aproximada (no tiempo de recorrido)."""
    lat1, lon1 = map(math.radians, origen)
    lat2, lon2 = map(math.radians, destino)
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return round(6371.0 * 2 * math.asin(min(1, math.sqrt(a))), 1)


def fecha_valida(valor):
    """Fecha ISO o vacío. No interpretar fecha de edición de OSM como actividad comercial."""
    valor = texto(valor, 10)
    try:
        return date.fromisoformat(valor).isoformat() if valor else ""
    except ValueError:
        return ""


def seleccionar_productos(sector, catalogo):
    """Sugiere hasta 3 descripciones que *existen* en el inventario; no inventa SKUs."""
    sugeridos = []
    productos = [texto(nombre, 160) for nombre in catalogo]
    for patron in SECTORES[sector]["productos"]:
        for nombre in productos:
            if normalizar(patron) in normalizar(nombre) and nombre not in sugeridos:
                sugeridos.append(nombre)
                break
    return "; ".join(sugeridos[:3])


def _sector_de_ficha(ficha):
    etiqueta = ficha.get("Segmento")
    sector = next((s for s, datos in SECTORES.items() if datos["nombre"] == etiqueta), None)
    # Compatibilidad con fichas guardadas antes de ampliar los giros.
    antiguos = {"Aplicadores de pisos y recubrimientos": "aplicadores",
                "Carpintería y mobiliario": "carpinterias",
                "Arquitectura e interiorismo": "arquitectura"}
    return sector or antiguos.get(etiqueta)


def clasificar(sector, ficha, hoy=None):
    """Puntaje explicable. Campos desconocidos no suman ni restan; jamás deduce ventas reales."""
    hoy = hoy or date.today()
    puntos = SECTORES[sector]["puntaje"]
    senales = [f"giro (+{puntos})"]

    def sumar(condicion, cantidad, motivo):
        nonlocal puntos
        if condicion:
            puntos += cantidad
            senales.append(f"{motivo} (+{cantidad})")

    # Lo que vende/utiliza solo se valora si la ficha OSM lo declara públicamente,
    # o el equipo aportó fuente para el dato importado/editado. No leer webs ni redes.
    perfil = ficha.get("Fuente_perfil") or ficha.get("Fuente") == FUENTE_OSM
    producto = normalizar(ficha.get("Productos_negocio")) if perfil else ""
    resina = bool(re.search(r"\b(resina|resinas|epoxi|epoxy|epoxico|epoxicos)\b", producto))
    relacionado = bool(re.search(r"madera|mueble|mesa|piso|recubrimiento|arte|manualidad", producto))
    sumar(resina, 12, "menciona resina/epóxico")
    sumar(not resina and relacionado, 4, "productos relacionados")
    try:
        distancia = float(ficha.get("Distancia_km"))
    except (TypeError, ValueError):
        distancia = math.inf
    sumar(0 <= distancia <= 10, 5, "hasta 10 km")
    sumar(10 < distancia <= 30, 2, "hasta 30 km")
    sumar(bool(ficha.get("Teléfono") or ficha.get("Correo")), 9, "teléfono/correo público")
    sumar(bool(whatsapp_publico(ficha.get("WhatsApp"))), 5, "WhatsApp publicado")
    sumar(bool(ficha.get("Sitio_web") or ficha.get("Redes")), 5, "presencia web/redes")
    tamano = ficha.get("Tamaño")
    sumar(bool(ficha.get("Fuente_perfil")) and tamano in ("Mediano", "Grande"), 3, "tamaño documentado")
    fecha_actividad = fecha_valida(ficha.get("Última_actividad"))
    if ficha.get("Fuente_perfil") and fecha_actividad:
        dias = (hoy - date.fromisoformat(fecha_actividad)).days
        sumar(0 <= dias <= 90, 4, "actividad comercial reciente documentada")
    sumar(bool(ficha.get("Fuente_perfil")) and ficha.get("Tipo_clientela") in ("Empresas", "Mixto"),
          2, "clientela documentada")
    puntos = max(0, min(100, puntos))  # escala cerrada 0–100
    prioridad = "Alta" if puntos >= 80 else "Media" if puntos >= 60 else "Exploratoria"
    return puntos, prioridad, senales


def recalcular_ficha(ficha, hoy=None):
    ficha = dict(ficha)
    sector = _sector_de_ficha(ficha)
    if sector:
        puntos, prioridad, senales = clasificar(sector, ficha, hoy=hoy)
        ficha["Puntaje"], ficha["Prioridad"] = puntos, prioridad
        ficha["Motivo"] = SECTORES[sector]["motivo"] + " Señales: " + ", ".join(senales) + "."
    return ficha


def preparar_mensaje(nombre_vendedor, ficha, producto_elegido=None):
    """Borrador, sin envío automático ni afirmaciones sobre uso actual de resina."""
    vendedor = texto(nombre_vendedor, 70) or "el equipo comercial"
    empresa = texto(ficha.get("Empresa"), 120) or "su negocio"
    opciones = [texto(p, 120) for p in str(ficha.get("Productos") or "").split(";") if texto(p)]
    producto = (opciones[0] if producto_elegido is None and opciones else
                producto_elegido if producto_elegido in opciones else "")
    linea = (f" Contamos con {producto}, que podría interesarles para sus proyectos."
             if producto else " Contamos con resinas epóxicas para distintas aplicaciones.")
    return (f"Hola, buen día. Soy {vendedor} de NEMET. Nos dedicamos a las resinas epóxicas y "
            f"acabados para proyectos. Vi el negocio {empresa} y me gustaría presentarnos."
            f"{linea} ¿Te puedo compartir información? Gracias.")


def _candidato(clave, empresa, sector, ciudad, direccion, telefono, correo, sitio, fuente, enlace, catalogo,
               *, whatsapp="", redes="", zona="", latitud=None, longitud=None, centro=None,
               productos_negocio="", tamano="", clientela="", actividad="", fuente_perfil=""):
    nombre = texto(empresa, 160)
    if not nombre:
        return None
    telefono, correo, sitio = telefono_publico(telefono), correo_publico(correo), url_publica(sitio)
    whatsapp, redes = whatsapp_publico(whatsapp), url_publica(redes)
    punto = coordenadas(latitud, longitud)
    ubicacion = coordenadas(*(centro or (None, None)))
    distancia = distancia_km(ubicacion, punto) if ubicacion and punto else ""
    ficha = {col: "" for col in COLUMNAS}
    ficha.update({
        "Clave": clave, "Empresa": nombre, "Segmento": SECTORES[sector]["nombre"],
        "Productos": seleccionar_productos(sector, catalogo), "Ciudad": texto(ciudad, 120),
        "Zona": texto(zona, 120), "Dirección": texto(direccion, 240), "Teléfono": telefono,
        "WhatsApp": whatsapp, "Correo": correo, "Sitio_web": sitio, "Redes": redes,
        "Latitud": round(punto[0], 6) if punto else "", "Longitud": round(punto[1], 6) if punto else "",
        "Distancia_km": distancia, "Productos_negocio": texto(productos_negocio, 250),
        "Tamaño": tamano if tamano in TAMANOS[1:] else "",
        "Tipo_clientela": clientela if clientela in CLIENTELAS[1:] else "",
        "Última_actividad": fecha_valida(actividad), "Fuente_perfil": texto(fuente_perfil, 250),
        "Fuente": fuente, "URL_fuente": url_publica(enlace),
        "Estado": "Nuevo" if telefono or correo or whatsapp or sitio or redes else "Por investigar",
    })
    return recalcular_ficha(ficha)


def puntos_mapa(fichas):
    """Solo ubica negocios con coordenadas comerciales válidas; no geocodifica direcciones."""
    colores = {"Alta": "#B4552D", "Media": "#2F5D3A", "Exploratoria": "#8F8B84"}
    puntos = []
    for ficha in fichas:
        punto = coordenadas(ficha.get("Latitud"), ficha.get("Longitud"))
        if punto:
            puntos.append({"lat": punto[0], "lon": punto[1], "color": colores.get(ficha.get("Prioridad"), "#8F8B84")})
    return puntos


def seguimientos_pendientes(fichas, hoy=None):
    """Recordatorios visibles al abrir la app; nunca mensajes automáticos en segundo plano."""
    hoy = hoy or date.today()
    return sorted((f for f in fichas
                   if f.get("Estado") not in ESTADOS_CERRADOS and fecha_valida(f.get("Próximo_seguimiento"))
                   and date.fromisoformat(fecha_valida(f["Próximo_seguimiento"])) <= hoy),
                  key=lambda f: f["Próximo_seguimiento"])


def crear_consulta_osm(lat, lon, radio_km, sectores):
    """Consulta acotada a POIs etiquetados, con radio y límite de salida."""
    if not sectores or any(s not in SECTORES for s in sectores):
        raise ValueError("Selecciona al menos un giro válido.")
    if not isinstance(radio_km, (int, float)) or not 1 <= radio_km <= 30:
        raise ValueError("El radio debe estar entre 1 y 30 km.")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Coordenadas fuera de rango.")
    etiquetas = {}
    for sector in sectores:
        for clave, valores in SECTORES[sector]["osm"].items():
            etiquetas.setdefault(clave, set()).update(valores)
    lineas = []
    for clave, valores in sorted(etiquetas.items()):
        patron = "|".join(sorted(valores))
        lineas.append(f'  nwr["{clave}"~"^({patron})$"](around:{int(radio_km * 1000)},{lat:.5f},{lon:.5f});')
    return "[out:json][timeout:25];\n(\n" + "\n".join(lineas) + f"\n);\nout center {MAX_RESULTADOS};"


@lru_cache(maxsize=50)
def ubicar_ciudad(ciudad):
    ciudad = texto(ciudad, 100)
    if not ciudad or len(ciudad) < 3:
        raise ErrorBusqueda("Indica una ciudad y estado de México (por ejemplo, Hermosillo, Sonora).")
    # Centros urbanos conocidos: la búsqueda predeterminada puede continuar aunque
    # Nominatim esté temporalmente inaccesible (Overpass sí necesita conexión).
    # Ciudad Obregón: https://geodatos.net/en/coordinates/mexico/sonora/ciudad-obregon
    nombre = normalizar(ciudad)
    if nombre in ("hermosillo", "hermosillo sonora", "hermosillo sonora mexico"):
        return 29.0892, -110.9613, "Hermosillo, Sonora"
    if nombre in ("ciudad obregon", "ciudad obregon sonora", "ciudad obregon sonora mexico",
                  "obregon sonora", "obregon sonora mexico"):
        return 27.48642, -109.94079, "Ciudad Obregón, Sonora"
    global _ULTIMA_GEO
    try:
        with _GEO_LOCK:
            espera = max(0, 1.05 - (time.monotonic() - _ULTIMA_GEO))
            if espera:
                time.sleep(espera)
            _ULTIMA_GEO = time.monotonic()
            respuesta = requests.get(NOMINATIM_URL, params={
                "q": ciudad if "mexico" in normalizar(ciudad) else ciudad + ", México",
                "countrycodes": "mx", "format": "jsonv2", "limit": 1,
            }, headers=CABECERAS, timeout=12)
        respuesta.raise_for_status()
        lugares = respuesta.json()
        if not isinstance(lugares, list) or not lugares:
            raise ErrorBusqueda("No encontré esa ciudad en México. Escribe ciudad y estado para distinguirla.")
        lat, lon = float(lugares[0]["lat"]), float(lugares[0]["lon"])
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Coordenadas inválidas")
        return lat, lon, ciudad
    except requests.RequestException as exc:
        raise ErrorBusqueda("No se pudo consultar la ubicación en OpenStreetMap. Reintenta más tarde.") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ErrorBusqueda("La respuesta del servicio de ubicaciones no es válida.") from exc


def _elemento_a_candidato(elemento, ciudad, sectores, catalogo, centro=None):
    if not isinstance(elemento, dict) or elemento.get("type") not in ("node", "way", "relation"):
        return None
    try:
        osm_id = int(elemento["id"])
        if osm_id < 1:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    etiquetas = elemento.get("tags") or {}
    if not isinstance(etiquetas, dict):
        return None
    compatibles = [sector for sector in sectores if any(
        etiquetas.get(clave) in valores for clave, valores in SECTORES[sector]["osm"].items()
    )]
    if not compatibles:
        return None
    sector = max(compatibles, key=lambda s: SECTORES[s]["puntaje"])
    nombre = etiquetas.get("name")
    if not nombre:
        return None  # sin ficha identificable no hay prospecto verificable
    calle = texto(etiquetas.get("addr:street"))
    numero = texto(etiquetas.get("addr:housenumber"))
    colonia = texto(etiquetas.get("addr:suburb"))
    direccion = " ".join(p for p in (calle, numero) if p)
    if direccion and colonia:
        direccion += ", " + colonia
    direccion = direccion or texto(etiquetas.get("addr:full"))
    tipo = elemento["type"]
    enlace = f"https://www.openstreetmap.org/{tipo}/{osm_id}"
    posicion = elemento if tipo == "node" else elemento.get("center") or {}
    if not isinstance(posicion, dict):
        posicion = {}
    return _candidato(
        f"osm/{tipo}/{osm_id}", nombre, sector, etiquetas.get("addr:city") or ciudad,
        direccion, (etiquetas.get("contact:phone") or etiquetas.get("phone")
                    or etiquetas.get("contact:mobile") or etiquetas.get("mobile")),
        etiquetas.get("contact:email") or etiquetas.get("email"),
        etiquetas.get("contact:website") or etiquetas.get("website"),
        FUENTE_OSM, enlace, catalogo,
        whatsapp=etiquetas.get("contact:whatsapp") or etiquetas.get("whatsapp"),
        redes=(etiquetas.get("contact:instagram") or etiquetas.get("instagram")
               or etiquetas.get("contact:facebook") or etiquetas.get("facebook")),
        zona=(etiquetas.get("addr:neighbourhood") or etiquetas.get("addr:suburb")
              or etiquetas.get("addr:quarter")),
        latitud=posicion.get("lat"), longitud=posicion.get("lon"), centro=centro,
        productos_negocio=(etiquetas.get("products") or etiquetas.get("product")
                           or etiquetas.get("description")),
    )


def buscar_osm(ciudad, radio_km, sectores, catalogo):
    """Busca negocios reales; no persiste datos ni devuelve fichas sin giro/nombre."""
    # Valida *antes* de llamar a geocodificación.
    crear_consulta_osm(0, 0, radio_km, sectores)
    lat, lon, nombre_ciudad = ubicar_ciudad(ciudad)
    consulta = crear_consulta_osm(lat, lon, radio_km, sectores)
    try:
        respuesta = requests.post(OVERPASS_URL, data={"data": consulta}, headers=CABECERAS, timeout=35)
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
    except requests.RequestException as exc:
        raise ErrorBusqueda("OpenStreetMap/Overpass no respondió. Reintenta luego o importa un CSV de negocios.") from exc
    except ValueError as exc:
        raise ErrorBusqueda("Overpass devolvió una respuesta no válida. Reintenta más tarde.") from exc
    if not isinstance(cuerpo, dict) or cuerpo.get("remark") or not isinstance(cuerpo.get("elements"), list):
        raise ErrorBusqueda("Overpass no pudo completar la búsqueda. Reduce el radio o reintenta después.")
    resultados = []
    for elemento in cuerpo["elements"][:MAX_RESULTADOS]:
        prospecto = _elemento_a_candidato(elemento, nombre_ciudad, sectores, catalogo, centro=(lat, lon))
        # Overpass puede incluir un polígono que apenas roza el círculo, cuyo centro
        # cae fuera del radio solicitado. No mostrarlo como negocio dentro del radio.
        if prospecto and (not prospecto["Distancia_km"] or prospecto["Distancia_km"] <= radio_km):
            resultados.append(prospecto)
    return sorted(resultados, key=lambda p: (-p["Puntaje"], p["Empresa"].casefold()))


def candidato_de_archivo(fila, catalogo, ciudad_default=""):
    """Construye una ficha desde CSV con Empresa + Giro/Segmento; sin giro no se clasifica."""
    empresa = texto(fila.get("Empresa"), 160)
    ciudad = texto(fila.get("Ciudad") or ciudad_default, 120)
    giro = normalizar(fila.get("Segmento") or fila.get("Giro"))
    if not empresa or not giro:
        return None
    sector = next((s for s, d in SECTORES.items() if giro in (s, normalizar(d["nombre"]))), None)
    if not sector:
        sector = next((s for patron, s in PALABRAS_GIRO if re.search(patron, giro)), None)
    if not sector:
        return None  # no inventar una afinidad para un giro desconocido
    clave = "csv/" + hashlib.sha256((normalizar(empresa) + "|" + normalizar(ciudad)).encode()).hexdigest()[:20]
    tamano = next((t for t in TAMANOS[1:] if normalizar(t) == normalizar(fila.get("Tamaño"))), "")
    clientela = next((c for c in CLIENTELAS[1:] if normalizar(c) == normalizar(fila.get("Tipo_clientela"))), "")
    return _candidato(
        clave, empresa, sector, ciudad, fila.get("Dirección", ""), fila.get("Teléfono", ""),
        fila.get("Correo", ""), fila.get("Sitio_web", ""), "Archivo importado",
        fila.get("URL_fuente", ""), catalogo,
        whatsapp=fila.get("WhatsApp", ""), redes=fila.get("Redes", ""), zona=fila.get("Zona", ""),
        latitud=fila.get("Latitud"), longitud=fila.get("Longitud"),
        productos_negocio=fila.get("Productos_negocio", ""), tamano=tamano, clientela=clientela,
        actividad=fila.get("Última_actividad", ""), fuente_perfil=fila.get("Fuente_perfil", ""),
    )


def candidato_manual(datos, catalogo, hoy=None):
    """Alta guiada de un negocio, sin búsqueda en red ni contactos privados.

    Exige giro del catálogo, origen descrito y confirmación de uso autorizado.
    Los datos desconocidos permanecen vacíos; las fechas y contactos introducidos
    erróneamente producen un error en vez de desaparecer silenciosamente.
    """
    hoy = hoy or date.today()
    empresa, ciudad = texto(datos.get("Empresa"), 160), texto(datos.get("Ciudad"), 120)
    sector = datos.get("Giro")
    origen = texto(datos.get("Origen_datos"), 240)
    if not empresa or not ciudad or sector not in SECTORES:
        raise ValueError("Indica empresa, ciudad y un tipo de negocio válido.")
    if not origen:
        raise ValueError("Indica el origen de la información comercial o quién autorizó el contacto.")
    if datos.get("Datos_comerciales_autorizados") is not True:
        raise ValueError("Confirma que los datos son comerciales públicos o aportados con autorización.")

    for campo, validador in (("Teléfono", telefono_publico), ("WhatsApp", whatsapp_publico),
                             ("Correo", correo_publico), ("Sitio_web", url_publica),
                             ("Redes", url_publica), ("URL_fuente", url_publica)):
        if texto(datos.get(campo)) and not validador(datos[campo]):
            raise ValueError(f"{campo} no tiene un formato válido. Corrige o deja el campo vacío.")
    latitud, longitud = datos.get("Latitud"), datos.get("Longitud")
    if (texto(latitud) or texto(longitud)) and not coordenadas(latitud, longitud):
        raise ValueError("Indica ambas coordenadas comerciales válidas dentro de México o déjalas vacías.")
    tamano = datos.get("Tamaño") or ""
    clientela = datos.get("Tipo_clientela") or ""
    if tamano and tamano not in TAMANOS[1:]:
        raise ValueError("Tamaño de negocio no válido.")
    if clientela and clientela not in CLIENTELAS[1:]:
        raise ValueError("Tipo de clientela no válido.")
    if any(texto(datos.get(c)) for c in ("Productos_negocio", "Tamaño", "Tipo_clientela", "Última_actividad")):
        if not texto(datos.get("Fuente_perfil")):
            raise ValueError("Para calificar el perfil comercial, indica una fuente de verificación.")
    notas = str(datos.get("Notas") or "")
    if len(notas) > 500:
        raise ValueError("Las notas no pueden superar 500 caracteres.")
    fechas = {}
    for campo in ("Último_contacto", "Próximo_seguimiento", "Última_actividad"):
        valor = datos.get(campo) or ""
        if isinstance(valor, date):
            valor = valor.isoformat()
        if valor:
            try:
                fecha = date.fromisoformat(str(valor))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{campo} debe tener formato de fecha válido.") from exc
            if campo != "Próximo_seguimiento" and fecha > hoy:
                raise ValueError(f"{campo} no puede ser una fecha futura.")
            fechas[campo] = fecha.isoformat()
        else:
            fechas[campo] = ""
    estado = datos.get("Estado") or "Nuevo"
    if estado not in ESTADOS_COMERCIALES:
        raise ValueError("Elige un estado comercial válido para el alta.")

    fila = {campo: valor for campo, valor in datos.items() if campo != "Segmento"}
    fila["Última_actividad"] = fechas["Última_actividad"]
    ficha = candidato_de_archivo(fila, catalogo)
    # Ya se validó el sector; no debería ocurrir, pero no guardar una ficha sin giro.
    if ficha is None:
        raise ValueError("No se pudo clasificar el negocio seleccionado.")
    clave = hashlib.sha256((normalizar(empresa) + "|" + normalizar(ciudad)).encode()).hexdigest()[:20]
    ficha.update({"Clave": f"manual/{clave}", "Fuente": "Alta manual: " + origen,
                  "Estado": estado, "Notas": texto(notas, 500),
                  "Último_contacto": fechas["Último_contacto"],
                  "Próximo_seguimiento": fechas["Próximo_seguimiento"]})
    return recalcular_ficha(ficha, hoy=hoy)


def _contactos(fila):
    correo = normalizar(fila.get("Correo"))
    vias = {correo} if correo else set()
    for campo in ("Teléfono", "WhatsApp"):
        telefono = re.sub(r"\D", "", str(fila.get(campo) or ""))
        # Un mismo número mexicano puede publicarse como +52 662... o 662...
        if len(telefono) == 13 and telefono.startswith("521"):
            telefono = telefono[3:]
        elif len(telefono) == 12 and telefono.startswith("52"):
            telefono = telefono[2:]
        if len(telefono) >= 7:
            vias.add(telefono)
    return vias


def filtrar_nuevos(candidatos, guardados, clientes):
    """Excluye clientes existentes y prospectos ya guardados (por ID, contacto o empresa/ciudad).

    Devuelve (nuevos, ya_guardados, ya_clientes). También deduplica dentro del lote.
    Los registros descartados NO se vuelven a importar en la siguiente búsqueda.
    """
    claves = {texto(p.get("Clave")) for p in guardados}
    empresas = {(normalizar(p.get("Empresa")), normalizar(p.get("Ciudad"))) for p in guardados}
    contactos = set().union(*(_contactos(p) for p in guardados)) if guardados else set()
    nombres_clientes = {normalizar(c.get(campo)) for c in clientes for campo in ("Empresa", "Nombre")}
    contactos_clientes = set().union(*(_contactos(c) for c in clientes)) if clientes else set()
    nuevos, repetidos, conocidos = [], 0, 0
    for p in candidatos:
        clave, empresa = texto(p.get("Clave")), normalizar(p.get("Empresa"))
        if not clave or not empresa:
            continue
        ubicacion = (empresa, normalizar(p.get("Ciudad")))
        vias = _contactos(p)
        if empresa in nombres_clientes or vias & contactos_clientes:
            conocidos += 1
        elif clave in claves or ubicacion in empresas or vias & contactos:
            repetidos += 1
        else:
            nuevos.append(p)
            claves.add(clave)
            empresas.add(ubicacion)
            contactos.update(vias)
    return nuevos, repetidos, conocidos


def proteger_excel(valor):
    """Evita que Excel ejecute fórmulas suministradas por fuentes externas o notas."""
    valor = texto(valor, 2000)
    return "'" + valor if valor.startswith("=") else valor


def proteger_csv(valor):
    """Los lectores CSV pueden interpretar también +, -, @ y tabuladores como fórmulas."""
    valor = texto(valor, 2000)
    return "'" + valor if valor.lstrip().startswith(("=", "+", "-", "@")) else valor
