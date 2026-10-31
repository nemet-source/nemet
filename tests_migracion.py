#!/usr/bin/env python3
"""Pruebas de la migración de la plantilla del proveedor (`migracion_plantilla.py`).

Fijan el trabajo del 31-oct-2026 sobre
`respaldos/Sistema_Inventario_2026-10-31_sin_SKU.xlsx`:

* los nombres reescritos de la plantilla (`Epoxy pisos 5K`) caen en la familia canónica
  de `Cat_Productos` (`EPOXY PISOS (A Y B) TRANSPARENTE`) y en su prefijo histórico;
* las escalas $/kg y $/L se convierten a **precio de venta por presentación con IVA**
  (1.5 kg × $480 = $720; 19 L × $440 = $8 360) y el `PrecioCompra` del tramo queda por
  presentación, para que el margen por SKU sea real;
* la columna `Línea` queda por familia (Cáscara y Estuco en *Acabado*, no en *Herramienta*);
* las 88 fichas conservan su SKU (ninguno vacío ni repetido) y el cotizador por área
  encuentra consumo (Rendimiento o Dosis_g_m2) para toda familia cotizable.

Uso:  python tests_migracion.py     →  "N/N OK" si todo pasa.
"""
import os
import re
import sys
import unittest

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import cotizador_calculo as cc  # noqa: E402
import migracion_plantilla as mp  # noqa: E402
import sku_nemet as skun  # noqa: E402

EXCEL = os.path.join(BASE, "Sistema_Inventario_NEMET_Final.xlsx")
PLANTILLA = os.path.join(BASE, "respaldos", "Sistema_Inventario_2026-10-31_sin_SKU.xlsx")

PASTA = "EPOXY PASTA COLORES SÓLIDOS"
PISOS = "EPOXY PISOS (A Y B) TRANSPARENTE"
CASCARA = "CÁSCARA DE NARANJA"
ESTUCO = "ESTUCO VENECIANO"
GRANO = "EPOXY-GRANO"


def leer_inventario(ruta=EXCEL):
    """Inventario como lo lee la app: encabezado en la fila 2, sin columnas basura."""
    df = pd.read_excel(ruta, sheet_name=mp.HOJA_INVENTARIO, header=1)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    return df.dropna(how="all").reset_index(drop=True)


def leer_catalogo():
    df = pd.read_excel(EXCEL, sheet_name=mp.HOJA_CATALOGO)
    return df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]


def leer_escalas():
    return pd.read_excel(EXCEL, sheet_name=mp.HOJA_ESCALAS)


def catalogo_efectivo(df_catalogo):
    """Réplica de `app.cargar_catalogo_rendimientos` (sin Streamlit)."""
    catalogo = {}
    for _, fila in df_catalogo.iterrows():
        nombre = cc.normalizar_clave(fila.get("Producto"))
        if not nombre or nombre in catalogo:
            continue
        rendimiento = pd.to_numeric(fila.get("Rendimiento"), errors="coerce")
        dosis = pd.to_numeric(fila.get("Dosis_g_m2"), errors="coerce")
        if cc.es_familia_dosificada(nombre):
            rendimiento = float("nan")
            if pd.isna(dosis) or dosis <= 0:
                dosis = cc.dosis_por_defecto(nombre)
        tiene_rendimiento = pd.notna(rendimiento) and rendimiento > 0
        tiene_dosis = pd.notna(dosis) and dosis > 0
        if not tiene_rendimiento and not tiene_dosis:
            continue
        prop_a = pd.to_numeric(fila.get("Prop_A"), errors="coerce")
        prop_b = pd.to_numeric(fila.get("Prop_B"), errors="coerce")
        catalogo[nombre] = {
            "rendimiento": float(rendimiento) if tiene_rendimiento else None,
            "prop_a": float(prop_a) if pd.notna(prop_a) and prop_a > 0 else 100.0,
            "prop_b": float(prop_b) if pd.notna(prop_b) and prop_b > 0 else 0.0,
            "dosis_g_m2": float(dosis) if tiene_dosis else None,
        }
    return catalogo


def info_de(catalogo, nombre):
    clave = cc.normalizar_clave(nombre)
    if clave in catalogo:
        return catalogo[clave]
    candidatas = [k for k in catalogo if clave.startswith(k) or k.startswith(clave)]
    return catalogo[max(candidatas, key=len)] if candidatas else None


def base_sintetica():
    """Tres fichas de EPOXY PISOS sin precio, como llega un inventario recién armado."""
    return pd.DataFrame({
        "SKU": ["", "", ""],
        "Descripcion": [PISOS] * 3,
        "Presentacion": ["1.5 kg", "10 kg", "20 kg"],
        "StockMinimo": [5, 5, 5],
        "StockInicial": [0, 0, 0],
        "Entradas": [0, 0, 0],
        "Salidas": [0, 0, 0],
        "StockActual": [0, 0, 0],
        "AlertaStock": ["⚠️ REABASTECER"] * 3,
        "PrecioCompra": [0.0, 0.0, 0.0],
        "PrecioPublicoIVA": [0.0, 0.0, 0.0],
    })


class TestLecturaPlantilla(unittest.TestCase):
    """La plantilla se lee tal cual: 70 renglones de precio del proveedor."""

    @classmethod
    def setUpClass(cls):
        cls.filas = mp.leer_plantilla(PLANTILLA)

    def test_01_lee_los_70_renglones_de_precio(self):
        self.assertEqual(len(self.filas), 70)
        self.assertTrue(all(fila["producto"].strip() for fila in self.filas))
        self.assertTrue(all(fila["precio"] and fila["precio"] > 0 for fila in self.filas))
        self.assertTrue(all(fila["costo"] is not None for fila in self.filas))

    def test_02_interpreta_tramos_tamanos_y_piezas(self):
        self.assertEqual(mp.parsear_rango("0 - 5.999 KG"), (0.0, 5.999))
        self.assertEqual(mp.parsear_rango("0 - 3,999 KG"), (0.0, 3.999))
        self.assertEqual(mp.parsear_rango("+ 20 KG"), (20.0, None))
        self.assertEqual(mp.parsear_rango("1 a 20 kg"), (1.0, 20.0))
        self.assertIsNone(mp.parsear_rango("60 GR"))
        self.assertIsNone(mp.parsear_rango("PZA"))

        # «EPOXY PRIMER +20 kg» trae el «+» en el nombre y el tamaño pelón en la unidad.
        self.assertEqual(mp.interpretar_presentacion("EPOXY PRIMER +20 kg", "20 KG"),
                         {"tipo": "rango", "desde": 20.0, "hasta": None, "magnitud": 20.0})
        self.assertEqual(mp.interpretar_presentacion("Epoxy Tinta 60 ML", "60 ML")["magnitud"], 0.06)
        self.assertEqual(mp.interpretar_presentacion("Epoxy Pasta 1 KG", "1 KG")["magnitud"], 1.0)
        self.assertEqual(mp.interpretar_presentacion("Rodillo 9''", "PZA")["tipo"], "pieza")

    def test_03_todas_las_familias_de_la_plantilla_estan_en_la_tabla(self):
        claves = {clave for familia in mp.FAMILIAS for clave in familia.claves_plantilla}
        for fila in self.filas:
            self.assertIn(fila["clave"], claves,
                          f"{fila['producto']}: su familia no está en migracion_plantilla.FAMILIAS")

    def test_04_los_nombres_reescritos_heredan_el_prefijo_historico(self):
        """`Hojuela 1 Color 60K` debe generar `EH`, no un acrónimo nuevo (`HC`)."""
        esperados = {
            "Epoxy pisos 5K": "EPT",
            "Epo Deep 7 KG": "EDT",
            "Poliuretano 20L": "POLIU",
            "Epoxy Spikes": "ES",
            "Epoxy Spikes Premium": "ESP",
            "Kit Borlas Pulimento": "KB",
            "Lijadora de Piso": "LPE",
            "Hojuela 1 Color 60K": "EH",
            "Hojuela Combinado +60K": "EHC",
            "Cáscara de Naranja 4K": "CN",
        }
        for producto, prefijo in esperados.items():
            self.assertEqual(skun.prefijo_para(producto), prefijo, producto)


class TestMigracionInventario(unittest.TestCase):
    """La plantilla convertida a la estructura que usa la app."""

    @classmethod
    def setUpClass(cls):
        cls.inventario = leer_inventario()
        cls.catalogo = leer_catalogo()
        cls.escalas = leer_escalas()
        cls.filas = mp.leer_plantilla(PLANTILLA)
        cls.migrado, cls.informe = mp.migrar_inventario(cls.inventario, cls.filas)

    def fila(self, sku, df=None):
        df = self.migrado if df is None else df
        return df[df["SKU"].astype(str).str.strip() == sku].iloc[0]

    def test_05_quedan_las_88_fichas_sin_sku_vacio_ni_repetido(self):
        self.assertEqual(len(self.migrado), 88)
        skus = self.migrado["SKU"].astype(str).str.strip()
        self.assertTrue(all(skus), "ningún renglón puede quedarse sin SKU")
        self.assertEqual(skun.skus_duplicados(self.migrado), [])
        for columna in ("Descripcion", "Presentacion", "PrecioCompra", "PrecioPublicoIVA", "Línea"):
            self.assertIn(columna, self.migrado.columns)

    def test_06_los_nombres_son_los_de_cat_productos(self):
        catalogo = {cc.normalizar_clave(nombre) for nombre in self.catalogo["Producto"].dropna()}
        herramientas = set(self.migrado.loc[self.migrado["Línea"] == "Herramienta", "Descripcion"])
        for descripcion in set(self.migrado["Descripcion"]):
            if descripcion in herramientas:
                continue   # los útiles no se dosifican: se venden por pieza
            clave = cc.normalizar_clave(descripcion)
            coincide = any(clave == otra or clave.startswith(otra) or otra.startswith(clave)
                           for otra in catalogo)
            self.assertTrue(coincide, f"{descripcion}: no está en {mp.HOJA_CATALOGO}")

    def test_07_las_escalas_se_convierten_a_precio_por_presentacion(self):
        esperados = {
            # sku: (precio con IVA, costo del proveedor)
            "EPT 01": (720, 542.13),      # 1.5 kg × $480
            "EPT 04": (4400, 3329.50),    # 10 kg × $440
            "EPT 06": (8000, 6029.00),    # 20 kg × $400 (+ 20 kg)
            "EDT 02": (2386, 1967.61),    # 4.26 kg × $560 (tramo 0 - 4.299)
            "EPRT 03": (7409, 5533.91),   # 20.025 kg × $370
            "POLIU 04": (8600, 7381.80),  # 20 L × $430 (antes $8 250 y costo $0)
            "ESV 03": (8360, 6859.00),    # 19 L × $440 (antes se capturó $440 por todo el bote)
            "GW 02": (4275, 3473.77),     # 19 L × $225
            "CN 04": (520, 413.80),       # tramo de compra: $/kg
            "R 02": (200, 193.32),        # pieza: el precio ya era por presentación
        }
        for sku, (precio, costo) in esperados.items():
            fila = self.fila(sku)
            self.assertAlmostEqual(float(fila["PrecioPublicoIVA"]), precio, places=2, msg=sku)
            self.assertAlmostEqual(float(fila["PrecioCompra"]), costo, places=2, msg=sku)

    def test_08_el_precio_lleva_iva_y_el_margen_es_real(self):
        filas = self.migrado[pd.to_numeric(self.migrado["PrecioCompra"], errors="coerce") > 0]
        self.assertGreaterEqual(len(filas), 70, "casi todas las fichas deben traer costo del proveedor")
        for _, fila in filas.iterrows():
            precio = float(fila["PrecioPublicoIVA"])
            costo = float(fila["PrecioCompra"])
            self.assertGreater(precio, costo, f"{fila['SKU']}: se vende por debajo del costo")
            self.assertAlmostEqual(float(fila["IVA 16%"]), precio - precio / (1 + mp.TASA_IVA),
                                   places=3, msg=f"{fila['SKU']}: el IVA no cuadra")
            self.assertLess((precio - costo) / precio, 0.60,
                            f"{fila['SKU']}: margen fuera de rango; revisa el tramo aplicado")

    def test_09_la_columna_linea_ya_no_esta_corrida(self):
        esperado = {
            "CN 01": "Acabado", "CN 04": "Acabado",       # antes: Complemento / Herramienta
            "ESV 01": "Acabado", "ESV 03": "Acabado",     # antes: Herramienta
            "GW 02": "Acabado",
            "EPT 06": "Resina", "EPRT 01": "Resina", "POLIU 03": "Resina",
            "EP 02": "Complemento", "ET 06": "Complemento", "EH 01": "Complemento",
            "RP 01": "Herramienta", "JEP 01": "Herramienta", "LPE 01": "Herramienta",
        }
        for sku, linea in esperado.items():
            self.assertEqual(self.fila(sku)["Línea"], linea, sku)
        self.assertTrue(all(str(l).strip() for l in self.migrado["Línea"]),
                        "ninguna ficha puede quedarse sin línea")

    def test_10_la_migracion_es_idempotente(self):
        otra_vez, informe = mp.migrar_inventario(self.migrado, self.filas)
        self.assertEqual(informe["filas"] and [f["precio"] for f in informe["filas"]],
                         [f["precio"] for f in self.informe["filas"]])
        pd.testing.assert_frame_equal(otra_vez.reset_index(drop=True),
                                      self.migrado.reset_index(drop=True))

    def test_11_no_se_pierde_ningun_renglon_sin_datos_del_proveedor(self):
        """Las 18 fichas que la plantilla no cotiza conservan su precio anterior."""
        sin_dato = {fila["sku"] for fila in self.informe["sin_dato"]}
        self.assertEqual(sin_dato, set(), "la plantilla debe cubrir las 88 fichas de producto")
        # Una familia ausente de la plantilla conserva el precio y se reporta, no se borra.
        base = base_sintetica()
        plantilla = [fila for fila in self.filas if fila["clave"] != "EPOXY PISOS"]
        resultado, informe = mp.migrar_inventario(base, plantilla)
        self.assertEqual(len(resultado), 3)
        self.assertEqual(informe["filas"], [])
        self.assertTrue(all(float(p) == 0.0 for p in resultado["PrecioPublicoIVA"]))

    def test_12_una_presentacion_sin_tramo_conserva_su_precio_y_se_reporta(self):
        base = base_sintetica()
        base.loc[2, "PrecioPublicoIVA"] = 12345.0      # 20 kg: el proveedor no lo cotiza aquí
        tramos = [f for f in self.filas if f["clave"] == "EPOXY PISOS"
                  and f["presentacion"]["hasta"] is not None]
        resultado, informe = mp.migrar_inventario(base, tramos)
        self.assertEqual(float(resultado.loc[2, "PrecioPublicoIVA"]), 12345.0)
        self.assertEqual(len(informe["sin_dato"]), 1)

    def test_13_un_inventario_sin_skus_recibe_codigos_al_migrar(self):
        resultado, informe = mp.migrar_inventario(base_sintetica(), self.filas)
        self.assertEqual([s for _, s in informe["skus_generados"]], ["EPT 01", "EPT 02", "EPT 03"])
        self.assertEqual(list(resultado["SKU"]), ["EPT 01", "EPT 02", "EPT 03"])


class TestCatalogoCompleto(unittest.TestCase):
    """Ninguna familia cotizable queda sin consumo ni las escalas del proveedor sin cargar."""

    @classmethod
    def setUpClass(cls):
        cls.inventario = leer_inventario()
        cls.catalogo = leer_catalogo()
        cls.escalas = leer_escalas()
        cls.efectivo = catalogo_efectivo(cls.catalogo)

    def test_14_toda_familia_cotizable_tiene_rendimiento_o_dosis(self):
        sin_consumo = []
        for _, fila in self.inventario.iterrows():
            if fila["Línea"] == "Herramienta":
                continue
            if info_de(self.efectivo, fila["Descripcion"]) is None:
                sin_consumo.append(fila["Descripcion"])
        self.assertEqual(sorted(set(sin_consumo)), [])

    def test_15_las_familias_nuevas_del_catalogo_traen_valores_utiles(self):
        esperados = {
            CASCARA: {"dosis_g_m2": 100.0},
            GRANO: {"dosis_g_m2": 2500.0},
            ESTUCO: {"rendimiento": 1.0},
            "GRANO WALL": {"rendimiento": 1.5},
            "EPOXY-MICA": {"dosis_g_m2": 8.0},
            "EPOXY CUARZO": {"dosis_g_m2": 2500.0},
        }
        for nombre, valores in esperados.items():
            info = info_de(self.efectivo, nombre)
            self.assertIsNotNone(info, f"{nombre} no está en Cat_Productos")
            for campo, valor in valores.items():
                self.assertAlmostEqual(info[campo], valor, places=4, msg=f"{nombre}.{campo}")
        # Un pigmento nunca conserva rendimiento por mm (era el error de COT-2026-003).
        for _, fila in self.catalogo.iterrows():
            if pd.notna(fila.get("Dosis_g_m2")) and float(fila["Dosis_g_m2"]) > 0:
                self.assertTrue(pd.isna(fila.get("Rendimiento")),
                                f"{fila['Producto']}: deja el Rendimiento vacío si tiene Dosis_g_m2")

    def test_16_las_escalas_del_proveedor_ya_estan_en_la_hoja(self):
        productos = {cc.normalizar_clave(nombre) for nombre in self.escalas["Producto_NEMET"].dropna()}
        for familia in ("POLIURETANO (A Y B) TRANSPARENTE", "ESTUCO VENECIANO", "GRANO WALL"):
            self.assertIn(cc.normalizar_clave(familia), productos, familia)
        for familia in ("POLIURETANO (A Y B) TRANSPARENTE", "ESTUCO VENECIANO", "GRANO WALL"):
            tramos = self.escalas[self.escalas["Producto_NEMET"] == familia]
            tramos = tramos.sort_values("Rango_Desde_Kg")
            self.assertGreaterEqual(len(tramos), 2, familia)
            self.assertTrue(pd.isna(tramos.iloc[-1]["Rango_Hasta_Kg"]),
                            f"{familia}: el último tramo debe ser abierto")
            for posicion in range(len(tramos) - 1):
                self.assertLess(tramos.iloc[posicion]["Rango_Desde_Kg"],
                                tramos.iloc[posicion + 1]["Rango_Desde_Kg"])

    def test_17_el_catalogo_no_repite_producto_y_presentacion(self):
        """Un mismo producto se repite por presentación; lo que no debe repetirse es el par."""
        pares = [(cc.normalizar_clave(p), cc.normalizar_clave(pres))
                 for p, pres in zip(self.catalogo["Producto"], self.catalogo["Presentacion"])
                 if pd.notna(p)]
        repetidos = sorted({par for par in pares if pares.count(par) > 1})
        self.assertEqual(repetidos, [], "un producto repetido oculta la captura del otro")


class TestValidacion(unittest.TestCase):
    """`validar` distingue lo que rompe la cotización de lo que solo falta capturar."""

    @classmethod
    def setUpClass(cls):
        cls.inventario = leer_inventario()
        cls.catalogo = leer_catalogo()
        cls.escalas = leer_escalas()

    def test_18_el_libro_actual_pasa_la_validacion(self):
        resultado = mp.validar(self.inventario, self.catalogo, self.escalas)
        self.assertEqual(resultado["errores"], [])

    def test_19_detecta_skus_vacios_y_repetidos(self):
        df = self.inventario.copy()
        df.loc[0, "SKU"] = ""
        df.loc[1, "SKU"] = df.loc[2, "SKU"]
        errores = " · ".join(mp.validar(df, self.catalogo, self.escalas)["errores"])
        self.assertIn("sin SKU", errores)
        self.assertIn("SKUs repetidos", errores)

    def test_20_detecta_una_familia_sin_consumo_en_el_catalogo(self):
        catalogo = self.catalogo[self.catalogo["Producto"] != PISOS]
        errores = " · ".join(mp.validar(self.inventario, catalogo, self.escalas)["errores"])
        self.assertIn(PISOS, errores)

    def test_21_avisa_de_las_fichas_sin_presentacion_o_sin_costo(self):
        avisos = " · ".join(mp.validar(self.inventario, self.catalogo, self.escalas)["avisos"])
        self.assertIn("sin presentación capturada", avisos)   # EPO-PAINT y ARENA SILICA
        self.assertIn("sin costo de compra", avisos)


class TestCotizadorPorFamilia(unittest.TestCase):
    """El cotizador por área cotiza todas las familias del inventario migrado."""

    @classmethod
    def setUpClass(cls):
        cls.inventario = leer_inventario()
        cls.catalogo = catalogo_efectivo(leer_catalogo())

    def cotizables(self):
        """Familias con presentación cotizable (se quedan fuera EPO-PAINT y ARENA SILICA)."""
        familias = {}
        for _, fila in self.inventario.iterrows():
            descripcion = str(fila["Descripcion"]).strip()
            presentacion = str(fila["Presentacion"]).strip()
            if presentacion in ("", "0", "0.0"):
                continue
            familias.setdefault(descripcion, fila["Línea"])
        return familias

    def test_22_cada_familia_calcula_material_para_16_m2(self):
        familias = self.cotizables()
        self.assertGreaterEqual(len(familias), 36)
        for familia, linea in familias.items():
            if linea == "Herramienta":
                continue
            info = info_de(self.catalogo, familia) or {}
            material = cc.calcular_material(16.0, 1.0, info, familia, dosis_manual_g_m2=info.get("dosis_g_m2"))
            self.assertGreater(material["kg_necesarios"], 0, familia)
            self.assertFalse(material["rendimiento_por_defecto"],
                             f"{familia}: el cotizador tuvo que inventar el rendimiento")

    def test_23_cada_familia_tiene_una_presentacion_que_cubre_el_material(self):
        for familia, linea in self.cotizables().items():
            filas = self.inventario[self.inventario["Descripcion"] == familia]
            if linea == "Herramienta":
                self.assertTrue(filas["Presentacion"].astype(str).str.contains("Pz").any(), familia)
                continue
            info = info_de(self.catalogo, familia) or {}
            material = cc.calcular_material(16.0, 1.0, info, familia, dosis_manual_g_m2=info.get("dosis_g_m2"))
            opciones = []
            for _, fila in filas.iterrows():
                kg = cc.parsear_presentacion_kg(fila["Presentacion"])
                precio = float(pd.to_numeric(fila["PrecioPublicoIVA"], errors="coerce") or 0)
                if not kg or precio <= 0:
                    continue
                unidades = cc.unidades_para_cubrir(material["kg_necesarios"], kg)
                opciones.append({"presentacion": fila["Presentacion"], "precio": unidades * precio,
                                 "unidades": unidades})
            self.assertTrue(opciones, f"{familia}: no hay presentación con precio para cotizar")
            self.assertIsNotNone(cc.elegir_opcion_mas_economica(opciones), familia)


class TestConciliacionConElArchivoSubido(unittest.TestCase):
    """El Inventario guardado refleja, renglón por renglón, el archivo que subió NEMET.

    La lista del proveedor se toma **tal cual**: cada renglón aporta su costo y su precio
    por kg/L del tramo, y el precio de venta se obtiene multiplicando por el tamaño de la
    presentación (decisión del 31-oct-2026: «son $/kg del tramo»). Las 17 fichas que ese
    archivo no trae se conservan con su precio anterior hasta que llegue la lista nueva.
    """

    @classmethod
    def setUpClass(cls):
        cls.inventario = leer_inventario()
        cls.filas = mp.leer_plantilla(PLANTILLA)
        cls.migrado, cls.informe = mp.migrar_inventario(cls.inventario, cls.filas)

    def test_25_cada_renglon_aplicado_esta_guardado_con_su_mismo_precio_y_costo(self):
        guardado = self.inventario.set_index("SKU")
        aplicados = {fila["sku"]: fila for fila in self.informe["filas"]}
        self.assertEqual(len(aplicados), 71, "71 presentaciones vienen de la lista del proveedor")
        for sku, fila in aplicados.items():
            self.assertAlmostEqual(float(guardado.at[sku, "PrecioPublicoIVA"]), fila["precio"],
                                   places=2, msg=f"{sku}: precio distinto al del archivo subido")
            self.assertAlmostEqual(float(guardado.at[sku, "PrecioCompra"]), fila["costo"],
                                   places=2, msg=f"{sku}: costo distinto al del archivo subido")
            # El precio sale de multiplicar el tramo por el tamaño de la presentación…
            self.assertIn("·", fila["origen"])
            self.assertGreater(fila["precio"], fila["costo"])

    def test_26_las_fichas_que_el_archivo_no_trae_se_conservan_intactas(self):
        aplicados = {fila["sku"] for fila in self.informe["filas"]}
        pendientes = {str(sku).strip() for sku in self.inventario["SKU"] if str(sku).strip() not in aplicados}
        self.assertEqual(pendientes, {
            "PF 01", "PF 02", "PF 03", "EG 01", "EG 02", "EGC 01", "EGC 02",
            "EMI 01", "EMI 02", "EMC 01", "EMC 02", "EPA 01", "AS 01",
            "JNE 01", "M 01", "CD 01", "BD 01",
        })
        # Se conservan con su precio anterior: no se borran ni se ponen en cero.
        guardado = self.inventario.set_index("SKU")
        esperados = {"PF 01": 275.0, "EG 01": 65.0, "EMI 01": 470.0, "M 01": 190.0,
                     "CD 01": 1915.0, "BD 01": 250.0, "JNE 01": 1590.0}
        for sku, precio in esperados.items():
            self.assertAlmostEqual(float(guardado.at[sku, "PrecioPublicoIVA"]), precio, places=2, msg=sku)

    def test_27_solo_quedan_sin_destino_los_tramos_que_no_corresponden_a_ningun_bulto(self):
        """`Epo Deep 7 KG` (4.3-7.099) y `Epo Deep ULTRA 13 KG` (6.7-13.299) no aplican:
        el bote de 7.1 kg y el de 13.3 kg caen en el tramo siguiente de la lista."""
        self.assertEqual(len(self.informe["plantilla_sin_usar"]), 2)
        for texto in self.informe["plantilla_sin_usar"]:
            self.assertIn("·", texto)
        self.assertTrue(any("Epo Deep 7 KG" in t for t in self.informe["plantilla_sin_usar"]))
        self.assertTrue(any("Epo Deep ULTRA 13 KG" in t for t in self.informe["plantilla_sin_usar"]))


class TestHojaCotizadorDelExcel(unittest.TestCase):
    """La hoja `Cotizador_Rendimientos` del libro tiene que abarcar todo `Cat_Productos`."""

    def test_24_las_formulas_del_cotizador_abarcan_todo_el_catalogo(self):
        import openpyxl
        libro = openpyxl.load_workbook(EXCEL)
        ultima = libro[mp.HOJA_CATALOGO].max_row
        formulas = [celda.value for fila in libro["Cotizador_Rendimientos"].iter_rows()
                    for celda in fila
                    if isinstance(celda.value, str) and celda.value.startswith("=")
                    and "Cat_Productos!" in celda.value]
        self.assertTrue(formulas)
        for formula in formulas:
            for rango in re.findall(r"Cat_Productos!\$[A-Z]+\$2:\$[A-Z]+\$(\d+)", formula):
                self.assertEqual(int(rango), ultima, f"rango corto en: {formula}")


if __name__ == "__main__":
    resultado = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    n = resultado.testsRun
    if resultado.wasSuccessful():
        print(f"\n{n}/{n} OK — plantilla migrada: nombres, precios por presentación, costos, "
              f"línea y catálogo completo")
        sys.exit(0)
    print(f"\n{n - len(resultado.failures) - len(resultado.errors)}/{n} (revisa los fallos)")
    sys.exit(1)
