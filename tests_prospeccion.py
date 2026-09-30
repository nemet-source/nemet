#!/usr/bin/env python3
"""Pruebas sin red de la prospección: fuentes, afinidad, seguridad y deduplicación.

Uso: python tests_prospeccion.py
"""
import ast
import csv
from io import StringIO
from pathlib import Path
import unittest
from datetime import date
from unittest.mock import Mock, patch

import pandas as pd
import requests

import prospeccion as pros

CATALOGO = (
    "EPOXY PISOS (A Y B) TRANSPARENTE", "EPOXY PRIMER (A Y B) TRANSPARENTE",
    "EPO-DEEP TRANSPARENTE", "EPO-FAST (A Y B) TRANSPARENTE", "EPO-PAINT (A y B)",
)


def respuesta(datos, status=200, cabeceras=None):
    r = Mock()
    r.status_code = status
    r.headers = cabeceras if cabeceras is not None else {}
    r.json.return_value = datos
    if status != 200:
        r.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return r


class TestReglas(unittest.TestCase):
    def test_respaldo_github_nunca_publica_crm_en_repositorio_publico_o_incierto(self):
        with patch("prospeccion.requests.get") as solicitud:
            solicitud.return_value = respuesta({"private": False, "full_name": "nemet-source/nemet"})
            ok, aviso = pros.respaldo_github_privado("nemet-source/nemet", "token-de-prueba")
            self.assertFalse(ok)
            self.assertIn("PÚBLICO", aviso)
            self.assertNotIn("token-de-prueba", solicitud.call_args.args[0])
            self.assertEqual(solicitud.call_args.kwargs["headers"]["Authorization"], "Bearer token-de-prueba")

            solicitud.return_value = respuesta({"private": True, "full_name": "nemet-source/nemet"})
            self.assertEqual(pros.respaldo_github_privado("nemet-source/nemet", "token-de-prueba"), (True, ""))
            solicitud.return_value = respuesta({"private": True, "full_name": "otra/empresa"})
            self.assertFalse(pros.respaldo_github_privado("nemet-source/nemet", "token-de-prueba")[0])
            solicitud.side_effect = requests.exceptions.SSLError("fallo de red")
            self.assertFalse(pros.respaldo_github_privado("nemet-source/nemet", "token-de-prueba")[0])
        self.assertFalse(pros.respaldo_github_privado("a/b/extra", "token-de-prueba")[0])

    def test_respaldo_app_no_hace_commit_ni_push_si_destino_es_publico(self):
        # Aislar el método para ejecutar su rama de rechazo sin arrancar Streamlit
        # ni tocar el repo/Excel/SQLite reales. Fallaría si el guard se quitara.
        archivo = Path(__file__).with_name("app.py")
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        funcion = next(n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name == "_respaldar_core")
        contexto = {"obtener_secret": lambda seccion, campo: {"token": "token-de-prueba",
                                                               "repo": "nemet-source/nemet"}[campo], "pros": pros}
        exec(compile(ast.Module(body=[funcion], type_ignores=[]), str(archivo), "exec"), contexto)
        with patch.object(pros, "respaldo_github_privado", return_value=(False, "Destino público")) as verificar:
            self.assertEqual(contexto["_respaldar_core"]("Respaldo comercial"),
                             (False, "warning", "Destino público"))
            verificar.assert_called_once_with("nemet-source/nemet", "token-de-prueba")

    def test_query_acotada_sin_texto_libre(self):
        q = pros.crear_consulta_osm(29.0892, -110.9613, 12, ("aplicadores", "carpinterias"))
        self.assertIn("around:12000,29.08920,-110.96130", q)
        self.assertIn("out center 300", q)
        self.assertIn('nwr["craft"', q)
        with self.assertRaises(ValueError):
            pros.crear_consulta_osm(0, 0, 100, ("aplicadores",))
        with self.assertRaises(ValueError):
            pros.crear_consulta_osm(0, 0, 10, ('test\"];out;/*',))
        with self.assertRaises(ValueError):
            pros.crear_consulta_osm(0, 0, 10, ())

    def test_clasifica_por_giro_y_sugiere_solo_catalogo_real(self):
        resultado = pros._elemento_a_candidato({
            "type": "node", "id": 124,
            "tags": {"name": "Pisos Sonora", "craft": "tiler", "office": "architect",
                     "phone": "+52 662 123 4567", "website": "pisos.example.mx",
                     "addr:street": "Reforma", "addr:housenumber": "10"},
        }, "Hermosillo", ("arquitectura", "aplicadores"), CATALOGO)
        self.assertEqual(resultado["Clave"], "osm/node/124")
        self.assertEqual(resultado["Segmento"], pros.SECTORES["aplicadores"]["nombre"])
        self.assertEqual(resultado["Puntaje"], 80)  # giro + teléfono + web; ubicación desconocida
        self.assertEqual(resultado["Prioridad"], "Alta")
        self.assertEqual(resultado["Teléfono"], "+52 662 123 4567")
        self.assertEqual(resultado["Sitio_web"], "https://pisos.example.mx")
        self.assertEqual(resultado["Dirección"], "Reforma 10")
        self.assertEqual(resultado["Fuente"], pros.FUENTE_OSM)
        self.assertEqual(resultado["URL_fuente"], "https://www.openstreetmap.org/node/124")
        self.assertIn("EPOXY PISOS", resultado["Productos"])
        self.assertNotIn("EPO-DEEP", resultado["Productos"])

        carpintero = pros._elemento_a_candidato({"type": "way", "id": 55, "tags": {
            "name": "Taller de ebanistería", "craft": "carpenter"}}, "Guaymas", ("carpinterias",), CATALOGO)
        self.assertIn("EPO-DEEP", carpintero["Productos"])
        self.assertEqual(carpintero["Estado"], "Por investigar")
        self.assertEqual(carpintero["Prioridad"], "Media")
        self.assertEqual(carpintero["URL_fuente"], "https://www.openstreetmap.org/way/55")
        self.assertNotIn("NEMET", pros.seleccionar_productos("aplicadores", []))

    def test_no_inventa_negocios_sin_nombre_ni_giro_ni_contacto(self):
        for e in (
            {"type": "node", "id": 1, "tags": {"craft": "tiler"}},
            {"type": "node", "id": 1, "tags": {"name": "Supermercado", "shop": "supermarket"}},
            {"type": "node", "id": -1, "tags": {"name": "Falso", "craft": "tiler"}},
        ):
            self.assertIsNone(pros._elemento_a_candidato(e, "Hermosillo", ("aplicadores",), CATALOGO))
        candidato = pros._elemento_a_candidato({"type": "node", "id": 9, "tags": {
            "name": "Piso honesto", "craft": "tiler", "website": "javascript:alert(1)",
            "phone": "llama ya", "email": "no es un correo"}}, "Hermosillo", ("aplicadores",), CATALOGO)
        self.assertFalse(candidato["Teléfono"] or candidato["Correo"] or candidato["Sitio_web"])
        self.assertEqual(candidato["Estado"], "Por investigar")
        self.assertEqual(candidato["Puntaje"], 66)

    def test_clientes_guardados_y_duplicados_del_mismo_lote(self):
        datos = [
            {"Clave": "osm/node/1", "Empresa": "Písos del Sol", "Ciudad": "Hermosillo", "Teléfono": "", "Correo": ""},
            {"Clave": "osm/node/2", "Empresa": "Pisos del Sol", "Ciudad": "Hermosillo", "Teléfono": "", "Correo": ""},
            {"Clave": "osm/node/3", "Empresa": "Carpintería Sur", "Ciudad": "Hermosillo", "Teléfono": "", "Correo": ""},
            {"Clave": "osm/way/20", "Empresa": "Negocio cliente", "Ciudad": "Hermosillo", "Teléfono": "", "Correo": ""},
            {"Clave": "osm/node/5", "Empresa": "Otro nombre", "Ciudad": "Guaymas", "Teléfono": "", "Correo": "ventas@cliente.mx"},
        ]
        guardados = [{"Clave": "osm/node/3", "Empresa": "Carpintería Sur", "Ciudad": "Hermosillo",
                      "Estado": "Descartado", "Teléfono": "", "Correo": ""}]
        clientes = [{"Empresa": "Negocio Cliente", "Nombre": "Raúl", "Teléfono": "", "Correo": ""},
                    {"Empresa": "Otra SA", "Nombre": "Julia", "Teléfono": "", "Correo": "ventas@cliente.mx"}]
        nuevos, repetidos, conocidos = pros.filtrar_nuevos(datos, guardados, clientes)
        self.assertEqual([p["Clave"] for p in nuevos], ["osm/node/1"])
        self.assertEqual((repetidos, conocidos), (2, 2))
        mismo_telefono = [{"Clave": "osm/node/42", "Empresa": "Otra razón social", "Ciudad": "Hermosillo",
                           "Teléfono": "+52 662 123 4567", "Correo": ""}]
        sin_duplicado, rep, _ = pros.filtrar_nuevos(mismo_telefono, [
            {"Clave": "osm/node/99", "Empresa": "Nombre distinto", "Ciudad": "Guaymas",
             "Teléfono": "6621234567", "Correo": ""}], [])
        self.assertEqual((sin_duplicado, rep), ([], 1))
        solo_wa = [{"Clave": "osm/node/43", "Empresa": "Un tercer nombre", "Ciudad": "Hermosillo",
                    "WhatsApp": "+52 662 123 4567", "Teléfono": ""}]
        self.assertEqual(pros.filtrar_nuevos(solo_wa, [], [
            {"Nombre": "Otro", "Empresa": "Cliente", "Teléfono": "6621234567"}])[2], 1)

    def test_mapa_google_abre_direccion_o_coordenadas_sin_suponer_ubicacion(self):
        ficha = {"Empresa": "Taller", "Dirección": "Av. Central 12 & 14", "Ciudad": "Ciudad Obregón, Sonora",
                 "Latitud": 27.5, "Longitud": -109.95}
        enlace = pros.enlace_google_maps(ficha)
        self.assertTrue(enlace.startswith("https://www.google.com/maps/search/?api=1&query="))
        self.assertIn("Av.%20Central%2012%20%26%2014", enlace)
        self.assertNotIn("& 14", enlace, "no interpolar dirección sin escape en la URL")
        self.assertIn("M%C3%A9xico", enlace)
        self.assertIn("27.500000,-109.950000", pros.enlace_google_maps(
            {"Latitud": 27.5, "Longitud": -109.95}))
        self.assertEqual(pros.enlace_google_maps({"Dirección": "Av. Central 12"}), "",
                         "sin ciudad ni coordenadas no hay búsqueda confiable")
        self.assertEqual(pros.enlace_google_maps({"Latitud": "999", "Longitud": "0"}), "")

    def test_csv_guarda_fuente_producto_estado_y_enlace_google_maps(self):
        archivo = Path(__file__).with_name("app.py")
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        funcion = next(n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name == "csv_prospectos")
        contexto = {"pros": pros}
        exec(compile(ast.Module(body=[funcion], type_ignores=[]), str(archivo), "exec"), contexto)
        ficha = pros.candidato_manual({
            "Empresa": "Taller Sur", "Giro": "carpinterias", "Ciudad": "Ciudad Obregón, Sonora",
            "Dirección": "Calle Norte 8", "Teléfono": "+52 644 123 4567", "Estado": "Cotización",
            "Origen_datos": "Contacto empresarial público", "Datos_comerciales_autorizados": True,
        }, CATALOGO, hoy=date(2026, 9, 30))
        contenido = contexto["csv_prospectos"](pd.DataFrame([ficha]))
        self.assertTrue(contenido.startswith(b"\xef\xbb\xbf"), "Excel en español debe leer UTF-8 con BOM")
        filas = list(csv.DictReader(StringIO(contenido.decode("utf-8-sig"))))
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["Estado"], "Cotización")
        self.assertIn("EPO-DEEP", filas[0]["Productos"])
        self.assertIn("Calle%20Norte%208", filas[0]["Google_Maps"])
        self.assertEqual(filas[0]["Teléfono"], "'+52 644 123 4567",
                         "el teléfono conserva los dígitos y CSV no ejecuta fórmulas")
        self.assertEqual(filas[0]["Fuente"], "Alta manual: Contacto empresarial público")

    def test_alta_manual_puntua_guarda_fuente_y_recomienda_inventario(self):
        hoy = date(2026, 9, 30)
        datos = {"Empresa": "Carpintería del Valle", "Giro": "carpinterias",
                 "Ciudad": "Ciudad Obregón, Sonora", "Dirección": "Calle Norte 8",
                 "Teléfono": "+52 644 123 4567", "WhatsApp": "https://wa.me/526441234567",
                 "Estado": "Cotización", "Notas": "Contactar el viernes",
                 "Próximo_seguimiento": "2026-10-02", "Origen_datos": "Directorio comercial público",
                 "URL_fuente": "https://empresa.example.mx/contacto", "Datos_comerciales_autorizados": True}
        ficha = pros.candidato_manual(datos, CATALOGO, hoy=hoy)
        self.assertTrue(ficha["Clave"].startswith("manual/"))
        self.assertEqual(ficha["Segmento"], pros.SECTORES["carpinterias"]["nombre"])
        self.assertIn("EPO-DEEP", ficha["Productos"])
        self.assertEqual(ficha["Estado"], "Cotización")
        self.assertEqual(ficha["Próximo_seguimiento"], "2026-10-02")
        self.assertEqual(ficha["Notas"], "Contactar el viernes")
        self.assertEqual(ficha["Fuente"], "Alta manual: Directorio comercial público")
        self.assertIn("Calle%20Norte%208", pros.enlace_google_maps(ficha))
        self.assertTrue(0 <= ficha["Puntaje"] <= 100)
        actualizado = pros.candidato_manual({**datos, "Estado": "Seguimiento"}, CATALOGO, hoy=hoy)
        self.assertEqual(actualizado["Clave"], ficha["Clave"], "mismo negocio no genera otra clave")
        self.assertEqual(pros.filtrar_nuevos([actualizado], [ficha], [])[1], 1)
        tope = pros.candidato_manual({**datos, "Giro": "aplicadores", "Sitio_web": "https://empresa.example.mx",
                "Productos_negocio": "resinas epóxicas", "Tamaño": "Grande", "Tipo_clientela": "Empresas",
                "Última_actividad": "2026-09-25", "Fuente_perfil": "Catálogo comercial publicado",
                }, CATALOGO, hoy=hoy)
        self.assertEqual(tope["Puntaje"], 100)
        self.assertEqual(pros.ESTADOS_COMERCIALES,
                         ("Nuevo", "Contactado", "Interesado", "Cotización", "Seguimiento", "Cliente"))

    def test_alta_manual_rechaza_datos_privados_o_invalidos_sin_perder_errores(self):
        datos = {"Empresa": "Muebles Sur", "Giro": "mobiliario", "Ciudad": "Ciudad Obregón",
                 "Origen_datos": "Ficha pública", "Datos_comerciales_autorizados": True}
        for cambios, fragmento in (
            ({"Datos_comerciales_autorizados": False}, "Confirma"),
            ({"Origen_datos": ""}, "origen"),
            ({"Giro": "giros inventados"}, "tipo de negocio"),
            ({"Teléfono": "celular privado"}, "Teléfono"),
            ({"WhatsApp": "https://wa.me.evil.mx/526441234567"}, "WhatsApp"),
            ({"Correo": "sin arroba"}, "Correo"),
            ({"URL_fuente": "javascript:alert(1)"}, "URL_fuente"),
            ({"Latitud": "27.5"}, "coordenadas"),
            ({"Notas": "a" * 501}, "500"),
            ({"Último_contacto": "2026-10-01"}, "futura"),
            ({"Última_actividad": "no es fecha", "Fuente_perfil": "Catálogo"}, "formato"),
            ({"Productos_negocio": "resina"}, "fuente de verificación"),
            ({"Estado": "No contactar"}, "estado comercial"),
        ):
            with self.subTest(cambios=cambios):
                with self.assertRaisesRegex(ValueError, fragmento):
                    pros.candidato_manual({**datos, **cambios}, CATALOGO, hoy=date(2026, 9, 30))
        sin_whatsapp = pros.candidato_manual({**datos, "Teléfono": "+52 644 123 4567"},
                                              CATALOGO, hoy=date(2026, 9, 30))
        self.assertEqual(sin_whatsapp["WhatsApp"], "", "teléfono convencional no implica WhatsApp")

    def test_archivo_exige_giro_y_deduplica_por_nombre_ciudad(self):
        fila = {"Empresa": "Ebanistería San José", "Giro": "Carpintería y mobiliario", "Ciudad": "Guaymas",
                "Teléfono": "6621234567", "Sitio_web": "taller.mx"}
        primero = pros.candidato_de_archivo(fila, CATALOGO)
        segundo = pros.candidato_de_archivo({**fila, "Correo": "ventas@taller.mx"}, CATALOGO)
        self.assertEqual(primero["Clave"], segundo["Clave"])
        self.assertEqual(primero["Fuente"], "Archivo importado")
        self.assertIn("EPO-DEEP", primero["Productos"])
        self.assertIsNone(pros.candidato_de_archivo({"Empresa": "Sin giro"}, CATALOGO))
        self.assertIsNone(pros.candidato_de_archivo({"Empresa": "Tienda", "Giro": "abarrotes"}, CATALOGO))
        ferreteria = pros.candidato_de_archivo({"Empresa": "Pinturas y más", "Giro": "Ferretería y pisos"}, CATALOGO)
        self.assertEqual(ferreteria["Segmento"], pros.SECTORES["distribuidores"]["nombre"])
        interior = pros.candidato_de_archivo({"Empresa": "Estudio", "Giro": "Diseño de interiores"}, CATALOGO)
        self.assertEqual(interior["Segmento"], pros.SECTORES["decoracion"]["nombre"])

    def test_protege_excel_y_csv_de_formulas_pero_conserva_contactos_validos(self):
        self.assertEqual(pros.proteger_excel("=HYPERLINK(\"http://evil\")"), "'=HYPERLINK(\"http://evil\")")
        self.assertEqual(pros.proteger_csv("+SUM(1,1)"), "'+SUM(1,1)")
        self.assertEqual(pros.proteger_csv(" -SUM(1,1)"), "'-SUM(1,1)")
        self.assertEqual(pros.proteger_csv("@evil"), "'@evil")
        self.assertEqual(pros.telefono_publico("+52 662 123 4567"), "+52 662 123 4567")
        self.assertEqual(pros.correo_publico("contacto@empresa.mx"), "contacto@empresa.mx")

    def test_los_giros_nuevos_se_reconocen_sin_confundir_tiendas_con_fabricantes(self):
        casos_osm = (
            ({"craft": "furniture_maker"}, "mobiliario"),
            ({"craft": "carpenter"}, "carpinterias"),
            ({"craft": "wood_carver"}, "artesanos"),
            ({"shop": "craft"}, "manualidades"),
            ({"office": "interior_design"}, "decoracion"),
            ({"craft": "restorer"}, "restauracion"),
        )
        for i, (giro, sector) in enumerate(casos_osm, 1):
            ficha = pros._elemento_a_candidato(
                {"type": "node", "id": i, "tags": {"name": "Empresa", **giro}},
                "Ciudad Obregón", tuple(pros.SECTORES), CATALOGO)
            self.assertEqual(ficha["Segmento"], pros.SECTORES[sector]["nombre"])
        for giro, sector in (("Fabricante de mesas de madera", "mobiliario"),
                             ("Artesano de resina", "artesanos"),
                             ("Taller restauración de muebles", "restauracion"),
                             ("Tienda de manualidades", "manualidades")):
            self.assertEqual(pros.candidato_de_archivo({"Empresa": "Comercio", "Giro": giro}, CATALOGO)["Segmento"],
                             pros.SECTORES[sector]["nombre"])

    def test_mapa_y_distancia_solo_con_coordenadas_comerciales_validas(self):
        centro = (27.49, -109.94)
        elemento = {"type": "node", "id": 10, "lat": 27.5, "lon": -109.95,
                    "tags": {"name": "Muebles del Sur", "craft": "furniture_maker",
                             "addr:suburb": "Centro", "products": "mesas con resina epóxica"}}
        ficha = pros._elemento_a_candidato(elemento, "Ciudad Obregón", ("mobiliario",), CATALOGO, centro=centro)
        self.assertEqual(ficha["Zona"], "Centro")
        self.assertEqual(ficha["Dirección"], "", "una colonia sola no es una dirección completa")
        self.assertAlmostEqual(ficha["Distancia_km"], 1.5, delta=0.3)
        self.assertIn("menciona resina", ficha["Motivo"])
        way = pros._elemento_a_candidato({"type": "way", "id": 11,
            "center": {"lat": 27.5, "lon": -109.95}, "tags": {"name": "Carpintería", "craft": "carpenter"}},
            "Ciudad Obregón", ("carpinterias",), CATALOGO, centro=centro)
        self.assertEqual(len(pros.puntos_mapa([ficha, way, {"Latitud": "999", "Longitud": "0"}])), 2)
        self.assertIsNone(pros.coordenadas(0, 0))
        self.assertEqual(pros.puntos_mapa([{**ficha, "Latitud": "", "Longitud": ""}]), [])

    def test_whatsapp_solo_si_publicado_y_borrador_no_se_envia(self):
        elemento = {"type": "node", "id": 22, "tags": {"name": "Comercio", "craft": "carpenter",
                    "phone": "+52 644 123 4567"}}
        ficha = pros._elemento_a_candidato(elemento, "Obregón", ("carpinterias",), CATALOGO)
        mensaje = pros.preparar_mensaje("Luis", ficha)
        self.assertIn("Soy Luis de NEMET", mensaje)
        self.assertIn("EPO-DEEP", mensaje)
        self.assertIn("EPO-FAST", pros.preparar_mensaje("Luis", ficha, "EPO-FAST (A Y B) TRANSPARENTE"))
        self.assertNotIn("PRODUCTO FICTICIO", pros.preparar_mensaje("Luis", ficha, "PRODUCTO FICTICIO"))
        self.assertEqual(pros.enlace_whatsapp(ficha, mensaje), "", "el teléfono NO prueba que tenga WhatsApp")
        ficha = pros._elemento_a_candidato({**elemento, "tags": {
            **elemento["tags"], "contact:whatsapp": "https://wa.me/526441234567"}},
            "Obregón", ("carpinterias",), CATALOGO)
        self.assertEqual(ficha["WhatsApp"], "526441234567")
        self.assertTrue(pros.enlace_whatsapp(ficha, mensaje).startswith("https://wa.me/526441234567?text=Hola%2C"))
        self.assertEqual(pros.whatsapp_publico("https://wa.me.evil.com/526441234567"), "")
        self.assertEqual(pros.whatsapp_publico("https://api.whatsapp.com/send?phone=526441234567"),
                         "526441234567")
        self.assertEqual(pros.whatsapp_publico("6441234"), "")

    def test_size_activity_clients_only_add_points_with_evidence(self):
        hoy = date(2026, 9, 30)
        base = pros.candidato_de_archivo({
            "Empresa": "Muebles", "Giro": "Fabricante de mesas", "Productos_negocio": "resina epóxica",
            "Tamaño": "Grande", "Tipo_clientela": "Empresas", "Última_actividad": "2026-09-25",
        }, CATALOGO)
        self.assertEqual(base["Puntaje"], pros.SECTORES["mobiliario"]["puntaje"])
        base["Fuente_perfil"] = "Catálogo público revisado el 30 de septiembre"
        comprobado = pros.recalcular_ficha(base, hoy=hoy)
        self.assertEqual(comprobado["Puntaje"], base["Puntaje"] + 12 + 3 + 4 + 2)
        self.assertIn("actividad comercial reciente", comprobado["Motivo"])
        futuro = pros.recalcular_ficha({**base, "Última_actividad": "2026-10-01"}, hoy=hoy)
        self.assertEqual(futuro["Puntaje"], comprobado["Puntaje"] - 4)

    def test_etiqueta_antigua_y_fecha_edicion_mapa_no_fingen_actividad(self):
        legado = pros.recalcular_ficha({"Segmento": "Carpintería y mobiliario", "Fuente": pros.FUENTE_OSM,
                                      "Última_actividad": "", "Fuente_perfil": ""}, hoy=date(2026, 9, 30))
        self.assertEqual(legado["Puntaje"], pros.SECTORES["carpinterias"]["puntaje"])
        elemento = {"type": "node", "id": 24, "timestamp": "2026-09-30T06:00:00Z",
                    "tags": {"name": "Estudio", "office": "architect"}}
        candidato = pros._elemento_a_candidato(elemento, "Ciudad Obregón", ("arquitectura",), CATALOGO)
        self.assertEqual(candidato["Última_actividad"], "")
        self.assertNotIn("actividad comercial reciente", candidato["Motivo"])

    def test_recordatorios_respeta_estado_y_fecha(self):
        fichas = [
            {"Empresa": "A", "Estado": "En seguimiento", "Próximo_seguimiento": "2026-09-28"},
            {"Empresa": "B", "Estado": "Nuevo", "Próximo_seguimiento": "2026-09-30"},
            {"Empresa": "C", "Estado": "Cliente", "Próximo_seguimiento": "2026-09-25"},
            {"Empresa": "D", "Estado": "No contactar", "Próximo_seguimiento": "2026-09-25"},
            {"Empresa": "E", "Estado": "Contactado", "Próximo_seguimiento": "2026-10-01"},
        ]
        self.assertEqual([f["Empresa"] for f in pros.seguimientos_pendientes(fichas, hoy=date(2026, 9, 30))],
                         ["A", "B"])


class TestRed(unittest.TestCase):
    def tearDown(self):
        pros.ubicar_ciudad.cache_clear()

    @patch("prospeccion.requests.get")
    def test_geocodificacion_con_corte_mexico_y_ciudades_habituales_sin_peticion(self, get):
        self.assertEqual(pros.ubicar_ciudad("Hermosillo, Sonora")[:2], (29.0892, -110.9613))
        self.assertEqual(pros.ubicar_ciudad("Ciudad Obregón, Sonora")[:2], (27.48642, -109.94079))
        self.assertEqual(pros.ubicar_ciudad("Obregon, Sonora")[:2], (27.48642, -109.94079))
        get.assert_not_called()
        get.return_value = respuesta([{"lat": "27.5", "lon": "-110.2"}])
        self.assertEqual(pros.ubicar_ciudad("Guaymas, Sonora")[:2], (27.5, -110.2))
        self.assertEqual(get.call_args.kwargs["params"]["countrycodes"], "mx")
        self.assertIn("NEMET", get.call_args.kwargs["headers"]["User-Agent"])
        pros.ubicar_ciudad("Guaymas, Sonora")
        self.assertEqual(get.call_count, 1, "no consultar la misma ciudad otra vez")

    @patch("prospeccion.requests.get")
    @patch("prospeccion.requests.post")
    def test_ciudad_obregon_busca_con_un_solo_servicio_externo(self, post, get):
        post.return_value = respuesta({"elements": [{"type": "node", "id": 123,
            "lat": 27.49, "lon": -109.94, "tags": {"name": "Pisos del Valle", "craft": "tiler"}}]})
        fichas = pros.buscar_osm("Ciudad Obregón, Sonora", 30, ("aplicadores",), CATALOGO)
        self.assertEqual([f["Empresa"] for f in fichas], ["Pisos del Valle"])
        self.assertIn("27.48642,-109.94079", post.call_args.kwargs["data"]["data"])
        get.assert_not_called()

    @patch("prospeccion.requests.post")
    def test_busqueda_osm_ordena_descarta_otros_giros_y_limita(self, post):
        post.return_value = respuesta({"elements": [
            {"type": "node", "id": 1, "tags": {"name": "Ferretería", "shop": "hardware"}},
            {"type": "way", "id": 2, "tags": {"name": "Pisos", "craft": "tiler", "phone": "6621234567"}},
            {"type": "node", "id": 3, "tags": {"name": "Panadería", "shop": "bakery"}},
        ]})
        leads = pros.buscar_osm("Hermosillo, Sonora", 12, ("distribuidores", "aplicadores"), CATALOGO)
        self.assertEqual([l["Empresa"] for l in leads], ["Pisos", "Ferretería"])
        self.assertIn("[timeout:25]", post.call_args.kwargs["data"]["data"])
        self.assertEqual(post.call_args.kwargs["timeout"], 35)

    @patch("prospeccion.time.sleep")
    @patch("prospeccion.requests.post")
    def test_error_no_crea_lista_ficticia(self, post, dormir):
        post.return_value = respuesta({}, 429)
        with self.assertRaises(pros.ErrorBusqueda):
            pros.buscar_osm("Hermosillo", 12, ("aplicadores",), CATALOGO)
        post.return_value = respuesta({"remark": "runtime error", "elements": []})
        with self.assertRaises(pros.ErrorBusqueda):
            pros.buscar_osm("Hermosillo", 12, ("aplicadores",), CATALOGO)


class TestVariosServidoresOverpass(unittest.TestCase):
    """La app corre en Streamlit Cloud: la IP de salida es compartida y los servidores
    públicos limitan por IP, así que un fallo NO puede depender de un solo servidor."""

    def tearDown(self):
        pros.ubicar_ciudad.cache_clear()

    CARPINTERIA = {"type": "node", "id": 4242, "lat": 27.49, "lon": -109.94,
                   "tags": {"name": "Carpintería del Sol", "craft": "carpenter",
                            "contact:phone": "+52 644 109 4422"}}

    def test_servidores_publicos_son_https_unicos_y_sin_credenciales(self):
        self.assertGreaterEqual(len(pros.OVERPASS_URLS), 3, "un solo servidor no es un plan B")
        self.assertEqual(len(pros.OVERPASS_URLS), len(set(pros.OVERPASS_URLS)))
        for url in pros.OVERPASS_URLS:
            self.assertTrue(url.startswith("https://"), url)
            self.assertNotIn("@", url, "ningún servidor recibe credenciales incrustadas")
        self.assertIn(pros.OVERPASS_URL, pros.OVERPASS_URLS)

    @patch("prospeccion.time.sleep")
    @patch("prospeccion.requests.post")
    def test_limite_por_ip_reintenta_una_vez_y_cambia_de_servidor(self, post, dormir):
        """HTTP 429: se espera lo que pide el servidor y, si insiste, se pregunta a otro."""
        post.side_effect = [respuesta({}, 429, {"Retry-After": "2"}), respuesta({}, 429),
                            respuesta({"elements": [self.CARPINTERIA]}, 200)]
        fichas = pros.buscar_osm("Ciudad Obregón, Sonora", 5, ("carpinterias",), CATALOGO)
        self.assertEqual([f["Empresa"] for f in fichas], ["Carpintería del Sol"])
        self.assertEqual(post.call_count, 3)
        self.assertEqual({pros._servidor_de(c.args[0]) for c in post.call_args_list[:2]},
                         {"overpass.kumi.systems"}, "el reintento es contra el mismo servidor")
        self.assertEqual(pros._servidor_de(post.call_args_list[2].args[0]), "overpass.private.coffee")
        dormir.assert_called_once_with(2.0)

    @patch("prospeccion.requests.post")
    def test_servidor_sin_conexion_se_salta_y_se_usa_el_siguiente(self, post):
        post.side_effect = [requests.ConnectionError("se cortó la conexión"),
                            respuesta({"elements": [self.CARPINTERIA]}, 200)]
        fichas = pros.buscar_osm("Ciudad Obregón, Sonora", 5, ("carpinterias",), CATALOGO)
        self.assertEqual([f["Empresa"] for f in fichas], ["Carpintería del Sol"])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(pros._servidor_de(post.call_args_list[1].args[0]), "overpass.private.coffee")

    @patch("prospeccion.requests.post")
    def test_si_ninguno_responde_el_error_explica_y_no_filtra_credenciales(self, post):
        # Un proxy que pide credenciales las incrusta en el mensaje de la excepción.
        post.side_effect = requests.exceptions.ProxyError(
            "407 Proxy Authentication Required for https://usuario:clave-falsa-de-prueba@proxy:8080/")
        with self.assertRaises(pros.ErrorBusqueda) as fallo:
            pros.buscar_osm("Ciudad Obregón, Sonora", 5, ("carpinterias",), CATALOGO)
        mensaje = str(fallo.exception)
        self.assertNotIn("clave-falsa-de-prueba", mensaje)
        self.assertNotIn("Authorization", mensaje)
        self.assertNotIn("proxy:8080", mensaje)
        self.assertIn("CSV", mensaje)
        self.assertIn("manualmente", mensaje)
        self.assertEqual(post.call_count, len(pros.OVERPASS_URLS), "se agotan los servidores")

    @patch("prospeccion.requests.post")
    def test_servidor_que_no_completa_la_consulta_baja_el_radio_una_vez_y_avisa(self, post):
        post.side_effect = ([respuesta({"remark": "runtime error: Query timed out",
                                        "elements": []}, 200)] * len(pros.OVERPASS_URLS)
                            + [respuesta({"elements": [self.CARPINTERIA]}, 200)])
        fichas, detalle = pros.buscar_osm_detallada("Ciudad Obregón, Sonora", 30, ("carpinterias",), CATALOGO)
        self.assertEqual([f["Empresa"] for f in fichas], ["Carpintería del Sol"])
        self.assertEqual(detalle["radio_pedido"], 30)
        self.assertEqual(detalle["radio_usado"], 15)
        self.assertEqual(len(detalle["avisos"]), 1)
        self.assertIn("30 km", detalle["avisos"][0])
        self.assertIn("15 km", detalle["avisos"][0])

    @patch("prospeccion.requests.post")
    def test_si_ni_con_menos_radio_completa_no_hay_fichas(self, post):
        post.return_value = respuesta({"remark": "runtime error", "elements": []})
        with self.assertRaises(pros.ErrorBusqueda) as fallo:
            pros.buscar_osm("Ciudad Obregón, Sonora", 30, ("carpinterias",), CATALOGO)
        self.assertIn("no alcanzaron a completar", str(fallo.exception))
        self.assertEqual(post.call_count, 2 * len(pros.OVERPASS_URLS),
                         "una sola reducción de radio, no una cascada de peticiones")

    @patch("prospeccion.requests.post")
    def test_detalle_informa_solo_el_servidor_que_respondio(self, post):
        post.return_value = respuesta({"elements": [self.CARPINTERIA]}, 200)
        _, detalle = pros.buscar_osm_detallada("Ciudad Obregón, Sonora", 5, ("carpinterias",), CATALOGO)
        self.assertEqual(detalle["servidor"], "overpass.kumi.systems")
        self.assertNotIn("/", detalle["servidor"], "solo el host, nunca la ruta ni parámetros")
        self.assertEqual(detalle["avisos"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
