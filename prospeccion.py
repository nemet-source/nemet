"""Descubrimiento y clasificación de negocios para la prospección NEMET.

Usa fichas de negocios publicadas en OpenStreetMap (Overpass + Nominatim),
registros comerciales del DENUE (INEGI) o giros declarados en archivos CSV propios.
El puntaje combina afinidad de giro, señales públicas y perfil con fuente; no predice compras.
La consulta a Overpass se intenta en varios servidores públicos, porque la IP de salida de
Streamlit Cloud es compartida y esos servidores limitan por IP: un HTTP 429 pasa al
siguiente servidor sin esperar y solo se reintenta tras el turno que pidió `Retry-After`.
La app recuerda cuál respondió para empezar por él la próxima vez y aparta un rato al que
acaba de fallar; si ninguno responde, informa qué pasó y puede recurrir al DENUE del INEGI
cuando hay un token configurado. Si las fuentes fallan, nunca devuelve fichas inventadas.
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
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import parse_qs, quote, urlparse

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Instancia principal de Overpass: reparte por turnos entre z y lz4 y cada servidor cuenta
# sus peticiones por IP de salida.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Servidores públicos en orden de intento (lista de la wiki de OSM:
# https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances).
# La app se ejecuta en Streamlit Cloud, donde la IP de salida es COMPARTIDA con otras apps:
# allí la instancia principal responde 429 (límite por IP) o rechaza la petición, y el fallo no
# depende del tamaño de la consulta. Por eso se pregunta a varios servidores antes de rendirse.
# Ninguno recibe credenciales: la consulta solo lleva coordenadas y etiquetas cerradas.
OVERPASS_URLS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    OVERPASS_URL,
    "https://z.overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
# Identificar la aplicación ante los servidores comunitarios; no hacer búsquedas en segundo plano.
CABECERAS = {
    "User-Agent": "NEMET-Prospeccion/1.0 (https://github.com/nemet-source/nemet)",
    "Accept": "application/json",
}
MAX_RESULTADOS = 300
FUENTE_OSM = "© OpenStreetMap contributors (ODbL)"
FUENTE_DENUE = "DENUE (INEGI)"
URL_DENUE = "https://www.inegi.org.mx/app/mapa/denue/default.aspx"
DENUE_API_URL = "https://www.inegi.org.mx/app/api/denue/v1/consulta"
DENUE_RADIO_MAX_KM = 5  # límite oficial del método Buscar del DENUE
TIEMPO_DENUE = 18
# El borde del DENUE (Microsoft-IIS/F5) corta las URLs largas: medido en 2026-10, una
# condición de más de ~280 caracteres responde «Hubo un problema con su solicitud…» o
# «400 Bad Request - Invalid URL» en vez de atender la consulta. Por eso la búsqueda
# reparte los giros en varias consultas cortas y nunca arma una URL mayor que este
# presupuesto (con margen sobre lo medido).
DENUE_URL_MAX = 250
# Los tokens del DENUE son códigos cortos (el INEGI los envía por correo; suelen medir
# 36 caracteres). Un valor mucho más largo o con espacios y comillas delata un copiado
# defectuoso del secreto: es mejor explicarlo que mandar una URL condenada al rechazo.
DENUE_TOKEN_MAX = 128
# Tope de consultas por búsqueda: cada consulta es una petición al servicio del INEGI.
DENUE_CONSULTAS_MAX = 10
DENUE_VERIFICACION_METROS = 250
# Punto denso y conocido (Zócalo de la Ciudad de México) para comprobar la credencial.
# Solo se usa para verificar que el INEGI acepta la clave; no se muestran sus fichas.
DENUE_VERIFICACION_CENTRO = (19.43261, -99.13321)
# Avisos del DENUE que llegan con HTTP 200 y sin JSON:
#   * «No Autorizado, utilice una clave valida.»  → credencial rechazada (texto plano)
#   * «Hubo un problema con su solicitud…»        → fallo del servicio (página HTML)
#   * «Bad Request - Invalid URL»                 → consulta rechazada por su longitud
_DENUE_AUTORIZACION = re.compile(r"no\s+autorizado|clave\s+v[aá]lida|sin\s+autorizaci[oó]n", re.IGNORECASE)
_DENUE_ERROR_URL = re.compile(r"bad\s+request|invalid\s+url|request\s+url", re.IGNORECASE)
_DENUE_ERROR_SERVICIO = re.compile(r"hubo\s+un\s+problema|lamentamos\s+el\s+inconveniente|c[oó]digo\s+de\s+soporte",
                                   re.IGNORECASE)
_DENUE_SIN_JSON = object()  # centinela: el cuerpo no se pudo leer como JSON
# Términos estáticos y acotados: nunca se inserta texto libre del formulario en la URL.
# La API Buscar acepta condiciones separadas por comas y un radio de hasta 5,000 m.
DENUE_TERMINOS = {
    "aplicadores": ("pisos", "recubrimientos", "impermeabilizantes", "acabados"),
    "carpinterias": ("carpintería", "carpintero", "ebanistería", "ebanista"),
    "mobiliario": ("fabricación de muebles", "fabricante de muebles", "mesas de madera"),
    "artesanos": ("artesanías", "artesano", "escultura", "tallado de madera", "resina"),
    "manualidades": ("manualidades", "materiales para manualidades", "mercería"),
    "decoracion": ("decoración de interiores", "diseño de interiores", "interiorismo"),
    "restauracion": ("restauración de muebles", "tapicería", "tapicero"),
    "constructoras": ("constructoras", "construcción", "acabados de construcción"),
    "distribuidores": ("ferretería", "materiales de construcción", "pinturas", "pisos"),
    "industria": ("taller industrial", "manufactura", "fabricación"),
    "arquitectura": ("arquitectura", "arquitectos", "diseño arquitectónico"),
}
# Las descripciones de actividad del DENUE son la evidencia principal. Se prueban primero
# las clases específicas; los nombres se usan solo como respaldo cuando la actividad es
# demasiado genérica o no viene informada.
DENUE_PATRONES_ACTIVIDAD = (
    ("mobiliario", r"\b(?:fabricacion|elaboracion|manufactura) de (?:muebles?|mesas?)\b"),
    ("restauracion", r"\b(?:tapiceria|tapiceros?|restauracion de muebles|reparacion de muebles)\b"),
    ("carpinterias", r"\b(?:carpinteria|carpinteros?|ebanisteria|ebanistas?)\b|\bfabricacion de productos de madera\b"),
    ("manualidades", r"\b(?:manualidades|articulos de merceria|materiales para manualidades)\b"),
    ("artesanos", r"\b(?:artesanias|artesanos?|escultura|tallado de madera|arte en resina)\b"),
    ("distribuidores", r"\b(?:ferreteria|madereria|materiales de construccion)\b|\bcomercio al por (?:mayor|menor) de (?:pinturas?|pisos|recubrimientos|madera)\b"),
    ("arquitectura", r"\b(?:servicios de arquitectura|arquitectos?|despachos? de arquitectura|diseno arquitectonico)\b"),
    ("decoracion", r"\b(?:decoracion de interiores|diseno de interiores|interiorismo)\b"),
    ("aplicadores", r"\b(?:colocacion|instalacion) de pisos\b|\b(?:impermeabilizantes?|pintura y otros trabajos de acabados|trabajos de acabados en edificios|aplicacion de recubrimientos)\b"),
    ("constructoras", r"\b(?:edificacion|construccion de obras|construccion residencial|obra civil|albanileria)\b"),
    ("industria", r"\b(?:fabricacion|manufactura|industria manufacturera)\b"),
)
DENUE_PATRONES_NOMBRE = (
    ("mobiliario", r"\b(?:fabricante|fabricacion|fabrica|taller) de (?:muebles?|mesas?)\b|\bfabricantes? de mobiliario\b"),
    ("restauracion", r"\b(?:tapiceria|tapiceros?|restauracion de muebles|restauradores? de muebles)\b"),
    ("carpinterias", r"\b(?:carpinteria|carpinteros?|ebanisteria|ebanistas?)\b"),
    ("manualidades", r"\b(?:manualidades|materiales para manualidades|insumos para artesanos)\b"),
    ("artesanos", r"\b(?:artesanias|artesanos?|escultura|tallado de madera|arte en resina)\b"),
    ("distribuidores", r"\b(?:ferreteria|madereria|materiales de construccion|tienda de pinturas|distribuidora de pinturas)\b"),
    ("arquitectura", r"\b(?:despacho de arquitectura|arquitectos?|arquitectura)\b"),
    ("decoracion", r"\b(?:decoracion|diseno de interiores|interiorismo)\b"),
    ("aplicadores", r"\b(?:aplicadores? de pisos|pintores?|pisos epoxicos?|recubrimientos epoxicos?)\b"),
    ("constructoras", r"\b(?:constructoras?|construccion|obra civil|acabados de construccion)\b"),
    ("industria", r"\b(?:taller industrial|industria manufacturera|manufactura)\b"),
)
# Nominatim público: como máximo 1 petición por segundo en este proceso.
_GEO_LOCK = threading.Lock()
_ULTIMA_GEO = 0.0
# Memoria de servidores de Overpass, compartida por todas las sesiones del proceso:
# qué servidor respondió la última vez y cuáles acaban de fallar. Solo guarda URLs
# públicas y marcas de tiempo; jamás consultas, resultados ni credenciales.
_SERVIDORES_LOCK = threading.Lock()
_SERVIDORES_EN_ESPERA = {}  # url -> instante monotónico a partir del cual conviene reintentar
_SERVIDOR_PREFERIDO = None  # último servidor que sí entregó resultados
TIEMPO_OVERPASS = 35      # segundos por intento; la consulta pide [timeout:25] al servidor
PRESUPUESTO_BUSQUEDA = 75  # techo total de reintentos: la interfaz no se queda colgada
ESPERA_429_MAX = 5        # espera máxima tras un "Too Many Requests" antes de reintentar
ESPERA_429_PREDETERMINADA = 2  # si el servidor no dice `Retry-After`, una pausa corta y cortés
# Un servidor que acaba de fallar pasa al final de la cola durante un rato: así la siguiente
# búsqueda no vuelve a gastar el tiempo del usuario en el mismo servidor que limita esta IP.
# Nunca se descarta: si todos están enfriándose se preguntan igual, empezando por el que
# antes queda libre. Esta memoria vive solo en el proceso; no se guarda en disco.
ENFRIAMIENTO_429 = 300    # límite por IP (HTTP 429): 5 minutos al final de la cola
ENFRIAMIENTO_FALLO = 120  # sin conexión, rechazo o respuesta inválida: 2 minutos
ENFRIAMIENTO_MAX = 900    # ningún servidor se aparta más de 15 minutos
RADIO_MINIMO = 1          # crear_consulta_osm no acepta menos de 1 km


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
    "Próximo_seguimiento", "Actividad_DENUE", "Estrato_Denue",
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
    """Error de red, ubicación o fuente de datos apto para mostrar en la interfaz.

    `intentos` es una lista opcional de (servidor, motivo) para que la pantalla pueda
    mostrar el detalle servidor por servidor. Solo contiene hosts públicos y motivos
    redactados por la app: nunca rutas, parámetros ni texto de excepciones de red.
    `diagnostico` es un resumen igualmente seguro de la respuesta recibida (estado,
    tipo de contenido, bytes y forma); nunca incluye el cuerpo, la URL ni el token.
    """

    def __init__(self, mensaje, intentos=(), diagnostico=None):
        super().__init__(mensaje)
        self.intentos = tuple(intentos)
        self.diagnostico = dict(diagnostico) if diagnostico else {}


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
               productos_negocio="", tamano="", clientela="", actividad="", fuente_perfil="",
               actividad_denue="", estrato_denue=""):
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
        "Actividad_DENUE": texto(actividad_denue, 180), "Estrato_Denue": texto(estrato_denue, 60),
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


class _FalloOverpass(Exception):
    """Ningún servidor de Overpass entregó resultados; guarda el motivo de cada uno.

    A propósito NO arrastra el texto de la excepción de red ni las cabeceras: un error de
    proxy o de TLS puede incrustar credenciales en su mensaje y eso nunca debe llegar a la
    pantalla. `resumen()` solo nombra servidor y motivo.
    """

    def __init__(self, intentos, saturado=False):
        self.intentos = list(intentos)
        self.saturado = saturado  # algún servidor aceptó la consulta pero no la terminó
        super().__init__(self.resumen())

    def resumen(self):
        return "; ".join(f"{servidor}: {motivo}" for servidor, motivo in self.intentos) or "sin servidores"

    def resumen_corto(self, maximo=3):
        """Los primeros motivos, para un mensaje legible; el detalle completo va aparte."""
        primeros = "; ".join(f"{servidor}: {motivo}" for servidor, motivo in self.intentos[:maximo])
        restantes = len(self.intentos) - maximo
        if restantes > 0:
            return f"{primeros}; y {restantes} servidor{'es' if restantes > 1 else ''} más"
        return primeros or "sin servidores"

    def mensaje(self):
        if self.saturado:
            return ("Los servidores de OpenStreetMap no alcanzaron a completar la búsqueda "
                    "con este radio. Reduce el radio o elige menos giros y reintenta; si sigue "
                    "igual, usa una alternativa sin red: importar un CSV de negocios públicos "
                    "o dar de alta el prospecto manualmente.")
        return ("No hay respuesta de OpenStreetMap/Overpass desde este despliegue "
                f"({self.resumen_corto()}). Reintenta en unos minutos, reduce el radio o usa una "
                "alternativa sin red: importar un CSV de negocios públicos o dar de alta el "
                "prospecto manualmente.")


def _servidor_de(url):
    """Solo el host del servidor, para poder informar sin exponer rutas ni credenciales."""
    try:
        return urlparse(url).netloc or str(url)
    except (ValueError, TypeError):
        return "servidor"


def _estado(respuesta):
    """Código HTTP de la respuesta, o 0 si el servidor no lo declaró."""
    try:
        return int(respuesta.status_code)
    except (AttributeError, TypeError, ValueError):
        return 0


def _espera_tras_429(respuesta):
    """Segundos que el servidor pide esperar antes de volver a preguntarle (acotados).

    `Retry-After` puede venir en segundos (`120`) o como fecha HTTP
    (`Wed, 30 Sep 2026 20:15:00 GMT`); ambos formatos son válidos y se entienden. Si el
    servidor no lo declara o lo declara mal, se usa una pausa corta y cortés en vez de
    suponer que ya hay turno libre.
    """
    try:
        pedida = respuesta.headers.get("Retry-After")
    except (AttributeError, TypeError):
        return ESPERA_429_PREDETERMINADA
    if pedida in (None, ""):
        return ESPERA_429_PREDETERMINADA
    try:
        segundos = float(pedida)
    except (TypeError, ValueError):
        try:
            momento = parsedate_to_datetime(str(pedida))
        except (TypeError, ValueError):
            return ESPERA_429_PREDETERMINADA
        if momento is None:
            return ESPERA_429_PREDETERMINADA
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=timezone.utc)
        segundos = (momento - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, min(segundos, ESPERA_429_MAX))


def reiniciar_memoria_servidores():
    """Olvida qué servidor funcionó y cuáles fallaron (la usan las pruebas)."""
    global _SERVIDOR_PREFERIDO
    with _SERVIDORES_LOCK:
        _SERVIDORES_EN_ESPERA.clear()
        _SERVIDOR_PREFERIDO = None


def _apartar_servidor(url, segundos):
    """Manda al final de la cola al servidor que acaba de fallar, por un rato."""
    with _SERVIDORES_LOCK:
        ahora = time.monotonic()
        hasta = ahora + max(0.0, min(float(segundos), ENFRIAMIENTO_MAX))
        for vencido in [u for u, v in _SERVIDORES_EN_ESPERA.items() if v <= ahora]:
            _SERVIDORES_EN_ESPERA.pop(vencido, None)  # la memoria no crece con servidores ya libres
        _SERVIDORES_EN_ESPERA[url] = max(hasta, _SERVIDORES_EN_ESPERA.get(url, 0.0))


def _recordar_servidor_util(url):
    """El servidor que entregó resultados se pregunta primero la próxima vez."""
    global _SERVIDOR_PREFERIDO
    with _SERVIDORES_LOCK:
        _SERVIDOR_PREFERIDO = url
        _SERVIDORES_EN_ESPERA.pop(url, None)


def _orden_servidores(urls=None):
    """Servidores a preguntar, en orden, según lo aprendido en búsquedas anteriores.

    Primero el que respondió la última vez (en Streamlit Cloud la IP es compartida y casi
    siempre es el mismo el que tiene turno), después el resto en el orden publicado y al
    final los que acaban de fallar, empezando por el que antes queda libre. **Ningún
    servidor se descarta**: si todos están enfriándose se preguntan igual, porque una
    búsqueda pedida por una persona vale más que la heurística.
    """
    servidores = tuple(urls if urls is not None else OVERPASS_URLS)
    ahora = time.monotonic()
    with _SERVIDORES_LOCK:
        esperando = {u: v for u, v in _SERVIDORES_EN_ESPERA.items() if u in servidores and v > ahora}
        preferido = _SERVIDOR_PREFERIDO
    libres = [u for u in servidores if u not in esperando]
    if preferido in libres:
        libres.remove(preferido)
        libres.insert(0, preferido)
    apartados = sorted((u for u in servidores if u in esperando), key=lambda u: esperando[u])
    return tuple(libres + apartados)


def _preguntar_a_overpass(url, consulta, restante):
    """Una sola petición a un servidor. Devuelve (resultado, dato) sin propagar excepciones.

    Resultados: `ok` con el cuerpo JSON, `429` con los segundos de espera que pidió el
    servidor, `saturado` si aceptó la consulta pero no la terminó, o `fallo` con el motivo.
    El motivo NUNCA incluye el texto de la excepción: un error de proxy o de TLS puede
    incrustar credenciales y eso no debe llegar a la pantalla.
    """
    try:
        respuesta = requests.post(url, data={"data": consulta}, headers=CABECERAS,
                                  timeout=max(5, min(TIEMPO_OVERPASS, restante)))
    except requests.Timeout:
        return "fallo", "no respondió a tiempo"
    except requests.RequestException:
        return "fallo", "sin conexión"
    estado = _estado(respuesta)
    if estado == 429:
        return "429", _espera_tras_429(respuesta)
    try:
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
    except requests.HTTPError:
        return "fallo", f"rechazó la petición (HTTP {estado})" if estado else "rechazó la petición"
    except ValueError:
        return "fallo", "respuesta no válida"
    if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("elements"), list):
        return "fallo", "respuesta incompleta"
    if cuerpo.get("remark"):
        # El servidor arrancó la consulta pero no la terminó (suele ser su [timeout:25]);
        # no es un fallo de la app ni del radio pedido, y el servidor sigue sano.
        return "saturado", "no completó la consulta"
    return "ok", cuerpo


def _consultar_overpass(consulta, presupuesto=PRESUPUESTO_BUSQUEDA):
    """Pregunta la misma consulta a los servidores públicos de Overpass, en dos vueltas.

    Devuelve (cuerpo JSON, servidor que respondió).

    - **Primera vuelta, sin esperas:** cada servidor se pregunta una sola vez, empezando
      por el que funcionó la última vez. Un HTTP 429 (límite por IP) ya no detiene la
      búsqueda ni hace esperar: otro servidor puede tener turno libre **ahora**.
    - **Segunda vuelta, solo con los que limitaron:** se reintenta una vez cada uno
      **después** de la pausa que pidió en `Retry-After`, empezando por el que pidió menos
      espera, como exige la política de uso de Overpass.
    - Todo cabe en un presupuesto de tiempo total para que la interfaz no se cuelgue, y
      cada fallo se recuerda para no volver a empezar por ese servidor en la siguiente
      búsqueda. Si ninguno responde se lanza `_FalloOverpass` con el motivo de cada uno.
    """
    inicio = time.monotonic()
    motivos = {}    # host -> motivo, en el orden en que se preguntó (uno por servidor)
    limitados = []  # (espera pedida, instante del 429, url) para la segunda vuelta
    saturado = False

    def restante():
        return presupuesto - (time.monotonic() - inicio)

    for url in _orden_servidores():
        if motivos and restante() < 5:
            break  # no seguir esperando: mejor avisar y ofrecer la alternativa sin red
        resultado, dato = _preguntar_a_overpass(url, consulta, restante())
        if resultado == "ok":
            _recordar_servidor_util(url)
            return dato, url
        if resultado == "429":
            limitados.append((dato, time.monotonic(), url))
            _apartar_servidor(url, max(dato, ENFRIAMIENTO_429))
            motivos[_servidor_de(url)] = "límite de peticiones (HTTP 429)"
        elif resultado == "saturado":
            saturado = True  # el servidor está sano: no se aparta, la consulta fue muy grande
            motivos[_servidor_de(url)] = dato
        else:
            _apartar_servidor(url, ENFRIAMIENTO_FALLO)
            motivos[_servidor_de(url)] = dato

    for pedida, cuando, url in sorted(limitados, key=lambda intento: intento[0]):
        espera = max(0.0, min(pedida - (time.monotonic() - cuando), ESPERA_429_MAX))
        if restante() < espera + 5:
            break
        if espera:
            time.sleep(espera)  # respetar el turno que pidió el servidor, nunca insistir antes
        resultado, dato = _preguntar_a_overpass(url, consulta, restante())
        if resultado == "ok":
            _recordar_servidor_util(url)
            return dato, url
        if resultado == "saturado":
            saturado = True
            motivos[_servidor_de(url)] = dato
        elif resultado != "429":
            motivos[_servidor_de(url)] = dato

    raise _FalloOverpass(motivos.items(), saturado)


def buscar_osm_detallada(ciudad, radio_km, sectores, catalogo):
    """Busca negocios reales y devuelve también el detalle de cómo se hizo la búsqueda.

    Devuelve (fichas, detalle). `detalle` indica el servidor que respondió, el radio que
    se usó al final y los avisos que la interfaz debe mostrar; no contiene datos de la
    empresa ni credenciales. Si todos los servidores aceptan la consulta pero ninguno la
    completa, se reintenta una sola vez con la mitad del radio y se avisa del cambio.
    """
    # Valida *antes* de llamar a geocodificación.
    crear_consulta_osm(0, 0, radio_km, sectores)
    lat, lon, nombre_ciudad = ubicar_ciudad(ciudad)
    radio = float(radio_km)
    avisos = []
    reducido = False
    while True:
        consulta = crear_consulta_osm(lat, lon, radio, sectores)
        try:
            cuerpo, servidor = _consultar_overpass(consulta)
            break
        except _FalloOverpass as fallo:
            if fallo.saturado and not reducido and radio > RADIO_MINIMO:
                nuevo = max(RADIO_MINIMO, radio / 2)
                avisos.append(f"Ningún servidor completó la búsqueda con {radio:g} km; "
                              f"se repitió con {nuevo:g} km.")
                radio, reducido = nuevo, True
                continue
            raise ErrorBusqueda(fallo.mensaje(), fallo.intentos) from None
    resultados = []
    for elemento in cuerpo["elements"][:MAX_RESULTADOS]:
        prospecto = _elemento_a_candidato(elemento, nombre_ciudad, sectores, catalogo, centro=(lat, lon))
        # Overpass puede incluir un polígono que apenas roza el círculo, cuyo centro
        # cae fuera del radio solicitado. No mostrarlo como negocio dentro del radio.
        if prospecto and (not prospecto["Distancia_km"] or prospecto["Distancia_km"] <= radio):
            resultados.append(prospecto)
    detalle = {
        "servidor": _servidor_de(servidor),
        "radio_pedido": float(radio_km),
        "radio_usado": radio,
        "avisos": avisos,
    }
    return sorted(resultados, key=lambda p: (-p["Puntaje"], p["Empresa"].casefold())), detalle


def buscar_osm(ciudad, radio_km, sectores, catalogo):
    """Busca negocios reales; no persiste datos ni devuelve fichas sin giro/nombre."""
    fichas, _detalle = buscar_osm_detallada(ciudad, radio_km, sectores, catalogo)
    return fichas


def limpiar_token_denue(token):
    """Devuelve el token como se pegó, sin comillas ni caracteres invisibles.

    Copiar y pegar desde el correo del INEGI puede arrastrar comillas, espacios o
    marcas de orden de bytes; se normalizan antes de usarlo en la URL. El valor nunca
    se registra, ni se muestra, ni viaja al navegador.
    """
    valor = unicodedata.normalize("NFKC", str(token if token is not None else ""))
    valor = "".join(c for c in valor if c not in "\u200b\u200c\u200d\ufeff")
    valor = valor.strip().strip('"').strip("'").strip()
    return valor


def huella_token_denue(token):
    """Identificador corto del token configurado: permite compararlo sin exponerlo.

    Sirve para comprobar que el secreto del despliegue es el token que el INEGI envió
    por correo (misma huella) sin publicar su valor en pantalla, en el chat ni en Git.
    """
    valor = limpiar_token_denue(token)
    if not valor:
        return "sin token configurado"
    return f"{hashlib.sha256(valor.encode('utf-8')).hexdigest()[:8]} · {len(valor)} caracteres"


def _token_denue_valido(token):
    """Valida el token privado sin revelar su valor y devuelve la forma ya limpia."""
    valor = limpiar_token_denue(token)
    if not valor:
        raise ErrorBusqueda("DENUE requiere un token del INEGI. Configúralo en los Secrets privados como "
                            "[inegi].denue_token o en NEMET_INEGI_DENUE_TOKEN.")
    if (len(valor) > DENUE_TOKEN_MAX
            or not valor.isascii()
            or any(ord(caracter) < 33 or ord(caracter) > 126 or caracter in "\"'" for caracter in valor)):
        raise ErrorBusqueda("El token configurado para DENUE no tiene un formato válido: debe ser una "
                            "cadena corta y sin espacios (el INEGI lo envía por correo como un código "
                            "único). Revisa [inegi].denue_token o NEMET_INEGI_DENUE_TOKEN; no lo pegues "
                            "en el chat ni en el repositorio.")
    return valor


def _terminos_denue(sectores):
    """Términos estáticos del catálogo local, sin repetir y en orden de aparición."""
    terminos, incluidos = [], set()
    for sector in sectores:
        for termino in DENUE_TERMINOS[sector]:
            clave = normalizar(termino)
            if clave not in incluidos:
                incluidos.add(clave)
                terminos.append(termino)
    return terminos


def _url_consulta_denue(lat, lon, metros, terminos, token):
    """URL del método Buscar: la condición va codificada como un solo segmento."""
    condicion = quote(",".join(terminos), safe=",")
    return (f"{DENUE_API_URL}/Buscar/{condicion}/"
            f"{lat:.5f},{lon:.5f}/{int(metros)}/{quote(token, safe='')}")


def _lotes_denue(lat, lon, metros, terminos, token):
    """Reparte los términos en consultas cuya URL quepa en el presupuesto del servicio.

    El DENUE corta las URLs largas antes de atender la consulta (ver DENUE_URL_MAX), así
    que en vez de una sola consulta gigante —que el servicio rechaza con HTTP 400 o con
    su página de error— se hacen varias consultas cortas cuya unión se deduplica por Id.
    """
    lotes, lote = [], []
    for termino in terminos:
        candidato = lote + [termino]
        if lote and len(_url_consulta_denue(lat, lon, metros, candidato, token)) > DENUE_URL_MAX:
            lotes.append(lote)
            lote = [termino]
        else:
            lote = candidato
    if lote:
        lotes.append(lote)
    return lotes


def crear_consulta_denue(lat, lon, radio_km, sectores, token):
    """Construye una consulta cerrada a la API oficial Buscar del DENUE.

    DENUE limita este método a 5,000 m y su borde corta las URLs largas; la búsqueda
    real reparte los giros en varias consultas con `_lotes_denue`. Los términos vienen
    exclusivamente del catálogo local de giros y el token se codifica como componente
    de ruta; nunca se agrega a la ficha ni a mensajes de diagnóstico.
    """
    if not sectores or any(sector not in SECTORES for sector in sectores):
        raise ValueError("Selecciona al menos un giro válido.")
    if (not isinstance(radio_km, (int, float)) or not math.isfinite(float(radio_km))
            or not 1 <= radio_km <= DENUE_RADIO_MAX_KM):
        raise ValueError("El radio de DENUE debe estar entre 1 y 5 km.")
    centro = coordenadas(lat, lon)
    if not centro:
        raise ValueError("Las coordenadas de búsqueda deben estar dentro de México.")
    token = _token_denue_valido(token)
    latitud, longitud = centro
    metros = int(round(float(radio_km) * 1000))
    return _url_consulta_denue(latitud, longitud, metros, _terminos_denue(sectores), token)


def _denue_aviso_autorizacion(datos):
    """¿El cuerpo (objeto JSON) repite el aviso de credencial rechazada del DENUE?"""
    pendientes = [datos]
    while pendientes:
        actual = pendientes.pop()
        if isinstance(actual, str):
            if _DENUE_AUTORIZACION.search(actual):
                return True
        elif isinstance(actual, dict):
            pendientes.extend(actual.values())
        elif isinstance(actual, (list, tuple)):
            pendientes.extend(actual)
    return False


def _clasificar_respuesta_denue(respuesta):
    """Traduce la respuesta del DENUE a (registros, problema, diagnóstico).

    `problema` llega vacío cuando hay fichas y, si no, es uno de: `sin_resultados`,
    `ambiguo`, `autorizacion`, `limite`, `redireccion`, `url`, `servicio` o `formato`.
    El INEGI responde **HTTP 200** incluso cuando rechaza la credencial: el cuerpo es
    entonces «No Autorizado, utilice una clave valida.» y no una lista JSON, así que el
    código de estado no basta para distinguir una autorización fallida. El diagnóstico
    solo resume estado, tipo de contenido, bytes y forma; nunca el cuerpo ni la URL.
    """
    estado = int(getattr(respuesta, "status_code", 0) or 0)
    cabeceras = getattr(respuesta, "headers", None) or {}
    try:
        tipo = str(cabeceras.get("Content-Type", "") or "")
    except (AttributeError, TypeError):
        tipo = ""
    tipo = tipo.split(";")[0].strip().lower() or "desconocido"
    cuerpo = getattr(respuesta, "text", "")
    if not isinstance(cuerpo, str):
        cuerpo = ""
    contenido = getattr(respuesta, "content", b"")
    if not isinstance(contenido, (bytes, bytearray)):
        contenido = cuerpo.encode("utf-8", "replace")
    diagnostico = {"estado": estado, "tipo_contenido": tipo, "bytes": len(contenido)}

    if estado in (301, 302, 303, 307, 308):
        diagnostico["forma"] = "redirección"
        return [], "redireccion", diagnostico
    if estado in (401, 403):
        diagnostico["forma"] = "autorización HTTP"
        return [], "autorizacion", diagnostico
    if estado == 429:
        diagnostico["forma"] = "límite de consultas"
        return [], "limite", diagnostico
    if estado != 200:
        if _DENUE_ERROR_URL.search(cuerpo):
            diagnostico["forma"] = "consulta rechazada por su longitud"
            return [], "url", diagnostico
        diagnostico["forma"] = f"HTTP {estado}"
        return [], "servicio", diagnostico

    try:
        datos = respuesta.json()
    except (TypeError, ValueError):
        datos = _DENUE_SIN_JSON
    if datos is not _DENUE_SIN_JSON:
        if isinstance(datos, list):
            registros = [registro for registro in datos if isinstance(registro, dict)]
            diagnostico["forma"] = "lista"
            if len(registros) != len(datos):
                diagnostico["forma"] = "lista con elementos no reconocidos"
                return [], "formato", diagnostico
            if not registros:
                diagnostico["forma"] = "lista vacía"
                return [], "sin_resultados", diagnostico
            return registros, "", diagnostico
        if isinstance(datos, dict):
            if _denue_aviso_autorizacion(datos):
                diagnostico["forma"] = "objeto con aviso de autorización"
                return [], "autorizacion", diagnostico
            if not datos:
                diagnostico["forma"] = "objeto vacío"
                return [], "ambiguo", diagnostico
            diagnostico["forma"] = "objeto"
            diagnostico["claves"] = sorted(str(clave) for clave in datos)[:4]
            return [], "formato", diagnostico
        if isinstance(datos, (int, float, bool)) or datos is None:
            # Un `null` o un valor suelto no distingue «sin coincidencias» de una
            # credencial rechazada: se resuelve con la consulta de verificación.
            diagnostico["forma"] = "nulo" if datos is None else "valor simple"
            return [], "ambiguo", diagnostico
        if _DENUE_AUTORIZACION.search(str(datos)):
            diagnostico["forma"] = "texto JSON con aviso de autorización"
            return [], "autorizacion", diagnostico
        diagnostico["forma"] = "texto JSON"
        return [], "formato", diagnostico

    # El cuerpo no es JSON: el DENUE usa texto plano y páginas HTML para sus avisos.
    if not cuerpo.strip():
        diagnostico["forma"] = "vacío"
        return [], "ambiguo", diagnostico
    if _DENUE_AUTORIZACION.search(cuerpo):
        diagnostico["forma"] = "texto sin JSON"
        return [], "autorizacion", diagnostico
    if _DENUE_ERROR_URL.search(cuerpo):
        diagnostico["forma"] = "consulta rechazada por su longitud"
        return [], "url", diagnostico
    if (_DENUE_ERROR_SERVICIO.search(cuerpo) or tipo == "text/html"
            or cuerpo.lstrip().startswith("<")):
        diagnostico["forma"] = "página de error sin JSON"
        return [], "servicio", diagnostico
    diagnostico["forma"] = "texto sin JSON"
    return [], "formato", diagnostico


def _error_denue(problema, diagnostico, token):
    """Mensaje y motivo (host + causa) para cada problema del DENUE, sin datos sensibles."""
    motivo = {
        "autorizacion": "token no válido o sin autorización",
        "limite": "límite temporal de consultas (HTTP 429)",
        "redireccion": "redirección inesperada",
        "url": "consulta rechazada por su longitud",
        "servicio": "error del servicio del INEGI",
        "formato": "formato de respuesta no reconocido",
    }.get(problema, f"respuesta HTTP {diagnostico.get('estado', 0)}")
    if problema == "autorizacion":
        huella = huella_token_denue(token)
        if diagnostico.get("estado") in (401, 403):
            mensaje = (f"DENUE (INEGI) rechazó el token (HTTP {diagnostico.get('estado')}). Comprueba que "
                       "[inegi].denue_token o NEMET_INEGI_DENUE_TOKEN sea el token que el INEGI envió por "
                       f"correo (huella configurada: {huella}); no lo pegues en el chat ni en el repositorio.")
        else:
            # El DENUE contesta HTTP 200 con «No Autorizado, utilice una clave valida.»: el
            # código de estado no delata el rechazo, lo delata el cuerpo.
            mensaje = ("DENUE (INEGI) rechazó el token: el servicio respondió que la clave no es válida "
                       "con HTTP 200 y texto plano, no con un error HTTP. Comprueba que [inegi].denue_token "
                       "o NEMET_INEGI_DENUE_TOKEN sea el token que el INEGI envió por correo "
                       f"(huella configurada: {huella}); no lo pegues en el chat ni en el repositorio.")
    elif problema == "limite":
        mensaje = "DENUE (INEGI) limitó temporalmente las consultas. Espera unos minutos y reintenta."
    elif problema == "redireccion":
        mensaje = ("DENUE (INEGI) intentó redirigir la consulta y no se reenvía el token a otra "
                   "dirección. Reintenta más tarde.")
    elif problema == "url":
        mensaje = ("DENUE (INEGI) rechazó la dirección de la consulta (HTTP 400): el servicio corta las "
                   "consultas muy largas. Reintenta con menos giros si vuelve a ocurrir.")
    elif problema == "formato":
        mensaje = ("DENUE (INEGI) devolvió una respuesta con un formato que no se reconoce "
                   f"(HTTP {diagnostico.get('estado', 0)} · "
                   f"{diagnostico.get('tipo_contenido', 'desconocido')} · "
                   f"{diagnostico.get('forma', 'sin forma')}). No se inventó ninguna ficha; reintenta "
                   "más tarde o usa otra fuente.")
    else:
        mensaje = (f"DENUE (INEGI) no está disponible en este momento "
                   f"(HTTP {diagnostico.get('estado', 0)}). Reintenta más tarde.")
    return ErrorBusqueda(mensaje, (("api.inegi.org.mx", motivo),), diagnostico)


def _consulta_denue(lat, lon, metros, terminos, token):
    """Hace una consulta corta del método Buscar y traduce su respuesta."""
    url = _url_consulta_denue(lat, lon, metros, terminos, token)
    try:
        respuesta = requests.get(url, headers=CABECERAS, timeout=TIEMPO_DENUE, allow_redirects=False)
    except requests.RequestException:
        raise ErrorBusqueda("No se pudo conectar con DENUE (INEGI). Reintenta en unos minutos.",
                            (("api.inegi.org.mx", "fallo de conexión o tiempo de espera"),)) from None
    return _clasificar_respuesta_denue(respuesta)


def verificar_credencial_denue(token, lat=None, lon=None):
    """Comprueba con una consulta mínima si el INEGI acepta la credencial configurada.

    Consulta «todos» en un punto denso (Zócalo de la Ciudad de México, o las coordenadas
    recibidas) para distinguir «el token no está autorizado» de «no hay coincidencias»,
    porque el DENUE responde HTTP 200 en ambos casos. Devuelve
    (estado, mensaje, diagnóstico) con estado `aceptado`, `rechazado`, `indeterminado`,
    `limite`, `servicio` o `red`. Nunca devuelve el token ni la URL.
    """
    token = _token_denue_valido(token)
    centro = coordenadas(lat, lon) if lat is not None and lon is not None else None
    latitud, longitud = centro if centro else DENUE_VERIFICACION_CENTRO
    registros, problema, diagnostico = _consulta_denue(
        float(latitud), float(longitud), DENUE_VERIFICACION_METROS, ("todos",), token)
    if problema in ("", "sin_resultados"):
        return "aceptado", ("El INEGI aceptó la credencial configurada: respondió con datos del DENUE "
                            f"(huella: {huella_token_denue(token)})."), diagnostico
    if problema == "autorizacion":
        return "rechazado", ("El INEGI rechazó la credencial: respondió que la clave no es válida "
                             f"(HTTP {diagnostico.get('estado', 200)}, sin datos). La huella configurada "
                             f"es {huella_token_denue(token)}; compárala con el token del correo del INEGI "
                             "y, si el valor estuvo expuesto, solicita uno nuevo."), diagnostico
    if problema == "limite":
        return "limite", "El INEGI limitó temporalmente las consultas; reintenta la verificación después.", diagnostico
    if problema == "ambiguo":
        estado = int(diagnostico.get("estado", 0) or 0)
        if estado == 200:
            mensaje = ("El INEGI respondió sin datos y sin el aviso de credencial rechazada: no se pudo "
                       f"confirmar la credencial (huella: {huella_token_denue(token)}).")
        else:
            mensaje = "El INEGI no respondió como se esperaba al verificar la credencial."
        return "indeterminado", mensaje, diagnostico
    return "servicio", ("El INEGI no pudo atender la verificación de la credencial en este momento. "
                        "Reintenta más tarde."), diagnostico


def _sector_denue(registro):
    """Clasifica por clase de actividad oficial; solo usa el nombre como segundo respaldo."""
    actividad = normalizar(registro.get("Clase_actividad"))
    for sector, patron in DENUE_PATRONES_ACTIVIDAD:
        if actividad and re.search(patron, actividad):
            return sector
    nombre = normalizar(" ".join((str(registro.get("Nombre") or ""),
                                  str(registro.get("Razon_social") or ""))))
    for sector, patron in DENUE_PATRONES_NOMBRE:
        if nombre and re.search(patron, nombre):
            return sector
    return None


def _registro_denue_a_candidato(registro, ciudad, sectores, catalogo, centro=None):
    """Adapta un registro oficial sin atribuirle datos que el DENUE no publica."""
    if not isinstance(registro, dict):
        return None
    sector = _sector_denue(registro)
    if sector not in sectores:
        return None
    identificador = texto(registro.get("Id"), 24)
    if not re.fullmatch(r"[0-9]{1,24}", identificador) or int(identificador) < 1:
        return None
    empresa = texto(registro.get("Nombre") or registro.get("Razon_social"), 160)
    if not empresa:
        return None

    partes_calle = [texto(registro.get(campo), 100) for campo in ("Tipo_vialidad", "Calle")]
    calle = " ".join(parte for parte in partes_calle if parte)
    exterior = texto(registro.get("Num_Exterior"), 30)
    interior = texto(registro.get("Num_Interior"), 30)
    direccion = " ".join(parte for parte in (calle, exterior) if parte)
    if interior:
        direccion += (" " if direccion else "") + "Int. " + interior
    colonia = texto(registro.get("Colonia"), 100)
    codigo_postal = texto(registro.get("CP"), 12)
    if colonia:
        direccion += (", " if direccion else "") + colonia
    if codigo_postal:
        direccion += (", C.P. " if direccion else "C.P. ") + codigo_postal

    ubicacion = texto(registro.get("Ubicacion"), 120) or texto(ciudad, 120)
    return _candidato(
        f"denue/{identificador}", empresa, sector, ubicacion, direccion,
        registro.get("Telefono", ""), registro.get("Correo_e", ""), registro.get("Sitio_internet", ""),
        FUENTE_DENUE, URL_DENUE, catalogo,
        zona=colonia, latitud=registro.get("Latitud"), longitud=registro.get("Longitud"),
        centro=centro, actividad_denue=registro.get("Clase_actividad", ""),
        estrato_denue=registro.get("Estrato", ""),
    )


def buscar_denue_detallada(ciudad, radio_km, sectores, catalogo, token):
    """Busca establecimientos DENUE en línea con respaldo oficial de INEGI.

    La API permite un radio de hasta 5 km y su borde corta las URLs largas (ver
    DENUE_URL_MAX), así que los giros se reparten en varias consultas cortas cuya unión
    se deduplica por Id. Si una consulta combinada no devuelve fichas, se reintenta giro
    por giro (por si el servicio exige que coincidan todas las palabras). Devuelve
    (fichas, detalle) con el radio realmente usado; nunca persiste los resultados ni
    incluye el token en mensajes, fichas o diagnóstico.
    """
    if not sectores or any(sector not in SECTORES for sector in sectores):
        raise ValueError("Selecciona al menos un giro válido.")
    if (not isinstance(radio_km, (int, float)) or not math.isfinite(float(radio_km))
            or not 1 <= radio_km <= 30):
        raise ValueError("El radio debe estar entre 1 y 30 km.")
    token = _token_denue_valido(token)
    radio_usado = min(float(radio_km), float(DENUE_RADIO_MAX_KM))
    latitud, longitud, nombre_ciudad = ubicar_ciudad(ciudad)
    metros = int(round(radio_usado * 1000))
    pendientes = _lotes_denue(latitud, longitud, metros, _terminos_denue(sectores), token)

    fichas_por_id, avisos, consultas, verificacion = {}, [], 0, ""
    diagnostico = {}
    while pendientes and consultas < DENUE_CONSULTAS_MAX:
        lote = pendientes.pop(0)
        consultas += 1
        registros, problema, diagnostico = _consulta_denue(latitud, longitud, metros, lote, token)
        for registro in registros:
            ficha = _registro_denue_a_candidato(registro, nombre_ciudad, sectores, catalogo,
                                                centro=(latitud, longitud))
            if ficha and (not ficha["Distancia_km"] or ficha["Distancia_km"] <= radio_usado):
                fichas_por_id[ficha["Clave"]] = ficha
        if not problema:
            continue
        if problema == "ambiguo" and not verificacion:
            estado, verificacion, diagnostico_verificacion = verificar_credencial_denue(
                token, latitud, longitud)
            consultas += 1
            diagnostico = dict(diagnostico_verificacion)
            diagnostico["verificacion"] = verificacion
            problema = {"aceptado": "sin_resultados", "rechazado": "autorizacion", "limite": "limite",
                        "servicio": "servicio"}.get(estado, "formato")
        if problema == "sin_resultados":
            # El servicio puede exigir que coincidan todas las palabras de la condición;
            # si la combinación no devolvió nada, se pregunta giro por giro (con tope).
            if len(lote) > 1 and consultas + len(lote) <= DENUE_CONSULTAS_MAX:
                pendientes[:0] = [[termino] for termino in lote]
                avisos.append("La consulta combinada de giros no devolvió fichas; se repitió giro por giro "
                              "para no descartar coincidencias.")
            continue
        if problema == "red":
            raise ErrorBusqueda("No se pudo conectar con DENUE (INEGI). Reintenta en unos minutos.",
                                (("api.inegi.org.mx", "fallo de conexión o tiempo de espera"),), diagnostico)
        raise _error_denue(problema, diagnostico, token)

    resultados = sorted(fichas_por_id.values(),
                        key=lambda prospecto: (-prospecto["Puntaje"], prospecto["Empresa"].casefold()))
    if radio_usado < float(radio_km):
        avisos.append(f"DENUE permite un máximo de {DENUE_RADIO_MAX_KM} km por consulta; "
                      f"se buscaron {radio_usado:g} km alrededor del centro.")
    if pendientes:
        avisos.append(f"Se alcanzó el tope de {DENUE_CONSULTAS_MAX} consultas al DENUE; "
                      "reduce los giros para cubrirlos todos.")
    if len(resultados) > MAX_RESULTADOS:
        avisos.append(f"Se muestran los primeros {MAX_RESULTADOS} resultados; reduce el radio o elige menos giros.")
    detalle = {
        "servidor": "api.inegi.org.mx",
        "radio_pedido": float(radio_km),
        "radio_usado": radio_usado,
        "consultas": consultas,
        "avisos": avisos,
        "huella_token": huella_token_denue(token),
    }
    if diagnostico:
        detalle["diagnostico"] = diagnostico
    return resultados[:MAX_RESULTADOS], detalle


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
        actividad_denue=fila.get("Actividad_DENUE", ""), estrato_denue=fila.get("Estrato_Denue", ""),
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
