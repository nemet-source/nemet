#!/usr/bin/env python3
"""Pruebas de la generación automática de SKUs (`sku_nemet.py`).

Cubren el caso que dejó el inventario sin códigos —la hoja `Inventario` llegó con la
columna `SKU` vacía y la app mostraba *NONE* en cada renglón— y fijan las reglas para
que un producto nuevo reciba un código coherente con su familia:

* prefijo histórico por familia (`EPOXY PISOS…` → `EPT`), consecutivo por presentación;
* numeración de la presentación más chica a la más grande, con unidades reales
  (20 ml antes que 1 L, 200 g antes que 1 kg);
* los SKUs ya capturados **nunca** se tocan ni se reenumeran;
* las filas vacías de la hoja se quedan vacías;
* la plantilla del proveedor —con los nombres reescritos («Epoxy pisos 5K»)— produce los
  mismos códigos que el catálogo histórico, para que la migración no invente prefijos.

Uso:  python tests_sku.py     →  "N/N OK" si todo pasa.
"""
import os
import sys
import unittest

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import sku_nemet as sk  # noqa: E402

EXCEL = os.path.join(BASE, "Sistema_Inventario_NEMET_Final.xlsx")


def leer_inventario():
    """Inventario como lo lee la app: encabezado en la fila 2 y nombres normalizados."""
    df = pd.read_excel(EXCEL, sheet_name="Inventario", header=1)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    return df.dropna(how="all")


class TestFormatoDeSku(unittest.TestCase):
    """Formato ``PREFIJO NN`` y utilidades de lectura."""

    def test_formato_de_dos_digitos(self):
        self.assertEqual(sk.formato_sku("EPT", 1), "EPT 01")
        self.assertEqual(sk.formato_sku("políu", 12), "POLIU 12")   # sin acentos

    def test_prefijo_y_numero(self):
        self.assertEqual(sk.prefijo_de_sku("EPT 04"), "EPT")
        self.assertEqual(sk.numero_de_sku("EPT 04"), 4)
        self.assertEqual(sk.numero_de_sku("GRANEL"), 0)

    def test_valores_vacios(self):
        for valor in (None, "", "  ", "NONE", "none", "nan", "N/A", "-", "—"):
            self.assertTrue(sk.sku_vacio(valor), valor)
        for valor in ("EPT 01", "0", "EMI 02"):
            self.assertFalse(sk.sku_vacio(valor), valor)

    def test_nan_de_pandas_es_vacio(self):
        self.assertTrue(sk.sku_vacio(float("nan")))
        self.assertEqual(sk.normalizar(float("nan")), "")


class TestPrefijos(unittest.TestCase):
    """El prefijo hereda la nomenclatura histórica del catálogo."""

    def test_familias_historicas(self):
        casos = {
            "EPOXY PISOS (A Y B) TRANSPARENTE": "EPT",
            "EPO-DEEP TRANSPARENTE": "EDT",
            "EPO-DEEP ULTRA (A Y B) TRANSPARENTE": "EDU",
            "POLIURETANO (A Y B) TRANSPARENTE": "POLIU",
            "CÁSCARA DE NARANJA": "CN",
            "ESTUCO VENECIANO": "ESV",
            "GRANO WALL": "GW",
            "EPOXY-MICA": "EMI",
        }
        for descripcion, esperado in casos.items():
            self.assertEqual(sk.prefijo_para(descripcion), esperado, descripcion)

    def test_nombres_reescritos_heredan_el_prefijo(self):
        # Plantilla alterna: el mismo producto con otro nombre y la presentación en el nombre.
        casos = {
            "Epoxy pisos 5K": "EPT",
            "Epoxy pisos +20 k": "EPT",
            "Epo Deep 7 KG": "EDT",
            "Epo Deep ULTRA 23Kg": "EDU",
            "Epo Fast 10K": "EFT",
            "Poliuretano 20L": "POLIU",
            "Epoxy Pasta 250 GR": "EP",
            "Epoxy Tinta 60 ML": "ET",
            "Epoxy Metal 100 GR": "EM",
            "EPOXY PRIMER 19 kg": "EPRT",
        }
        for descripcion, esperado in casos.items():
            self.assertEqual(sk.prefijo_para(descripcion), esperado, descripcion)

    def test_familia_nueva_recibe_acronimo_legible(self):
        prefijo = sk.prefijo_para("SELLADOR POLIASPÁRTICO 5 L")
        self.assertTrue(prefijo.isalpha() and prefijo.isupper(), prefijo)
        self.assertLessEqual(len(prefijo), 5)

    def test_familias_distintas_no_comparten_prefijo(self):
        uno = sk.prefijo_para("EPOXY PISOS 5K")
        otro = sk.prefijo_para("RODILLO DE PICOS", ocupados={uno})
        self.assertNotEqual(uno, otro)


class TestAsignacion(unittest.TestCase):
    """`asignar_skus_faltantes` sobre datos reales y sintéticos."""

    def test_inventario_actual_ya_tiene_todos_sus_skus(self):
        df = leer_inventario()
        resultado, asignados = sk.asignar_skus_faltantes(df, "Descripcion", "Presentacion", "SKU")
        self.assertEqual(asignados, [])
        self.assertEqual(list(resultado["SKU"]), list(df["SKU"]))
        self.assertNotIn("", [str(s).strip() for s in resultado["SKU"]])
        self.assertEqual(sk.skus_duplicados(resultado), [])

    def test_inventario_sin_skus_se_rellena_completo(self):
        df = leer_inventario().copy()
        df["SKU"] = ""
        resultado, asignados = sk.asignar_skus_faltantes(df, "Descripcion", "Presentacion", "SKU")
        self.assertEqual(len(asignados), len(df))
        self.assertEqual(sk.skus_duplicados(resultado), [])
        self.assertTrue(all(str(s).strip() for s in resultado["SKU"]))
        # Misma familia, prefijo compartido y consecutivos 01, 02, 03…
        pisos = [s for d, s in zip(resultado["Descripcion"], resultado["SKU"])
                 if d == "EPOXY PISOS (A Y B) TRANSPARENTE"]
        self.assertEqual(pisos, ["EPT 01", "EPT 02", "EPT 03", "EPT 04", "EPT 05", "EPT 06"])

    def test_none_se_convierte_en_sku(self):
        df = pd.DataFrame({"Descripcion": ["EPOXY-MICA", "EPOXY-MICA (combinado)"],
                           "Presentacion": ["1 kg", "1 kg"],
                           "SKU": ["NONE", "None"]})
        resultado, asignados = sk.asignar_skus_faltantes(df)
        self.assertEqual(asignados, [("EPOXY-MICA", "EMI 01"), ("EPOXY-MICA (combinado)", "EMC 01")])
        self.assertEqual(list(resultado["SKU"]), ["EMI 01", "EMC 01"])

    def test_no_reescribe_ni_reenumera_lo_existente(self):
        df = pd.DataFrame({"Descripcion": ["EPOXY PISOS (A Y B) TRANSPARENTE"] * 3,
                           "Presentacion": ["1.5 kg", "6 kg", "3 kg"],
                           "SKU": ["EPT 01", "", ""]})
        resultado, asignados = sk.asignar_skus_faltantes(df)
        self.assertEqual(resultado.loc[0, "SKU"], "EPT 01")          # intacto
        self.assertEqual(sorted(s for _, s in asignados), ["EPT 02", "EPT 03"])

    def test_numeracion_por_tamano_de_presentacion(self):
        # Se ordena por tamaño real, no por el orden de las filas: 200 g < 1 kg < 9''('').
        df = pd.DataFrame({"Descripcion": ["FELPA"] * 3,
                           "Presentacion": ["9''", "200 g", "1 kg"],
                           "SKU": ["", "", ""]})
        resultado, _ = sk.asignar_skus_faltantes(df)
        self.assertEqual(list(resultado["SKU"]), ["F 03", "F 01", "F 02"])

    def test_unidades_reales_al_ordenar(self):
        # 20 ml < 500 ml < 1 L (antes se comparaba solo el número: 1 L quedaba al final)
        df = pd.DataFrame({"Descripcion": ["EPOXY TINTA"] * 3,
                           "Presentacion": ["1 L", "20 ml", "500 ml"],
                           "SKU": ["", "", ""]})
        resultado, _ = sk.asignar_skus_faltantes(df)
        self.assertEqual(list(resultado["SKU"]), ["ET 03", "ET 01", "ET 02"])

    def test_filas_vacias_no_reciben_sku(self):
        df = pd.DataFrame({"Descripcion": ["EPOXY-MICA", "", None],
                           "Presentacion": ["1 kg", "", ""],
                           "SKU": ["", "", ""]})
        resultado, asignados = sk.asignar_skus_faltantes(df)
        self.assertEqual(asignados, [("EPOXY-MICA", "EMI 01")])
        self.assertEqual(list(resultado["SKU"])[1:], ["", ""])

    def test_dataframe_original_no_se_modifica(self):
        df = pd.DataFrame({"Descripcion": ["EPOXY-MICA"], "Presentacion": ["1 kg"], "SKU": [""]})
        sk.asignar_skus_faltantes(df)
        self.assertEqual(df.loc[0, "SKU"], "")

    def test_inventario_sin_columna_sku(self):
        df = pd.DataFrame({"Descripcion": ["ARENA SILICA"], "Presentacion": ["1 kg"]})
        resultado, asignados = sk.asignar_skus_faltantes(df)
        self.assertEqual(asignados, [("ARENA SILICA", "AS 01")])

    def test_duplicados_se_reportan(self):
        df = pd.DataFrame({"Descripcion": ["EPOXY-MICA", "EPOXY-MICA (combinado)"],
                           "Presentacion": ["1 kg", "1/2 kg"],
                           "SKU": ["EMI 01", "EMI 01"]})
        self.assertEqual(sk.skus_duplicados(df), ["EMI 01"])


class TestPlantillaMigrada(unittest.TestCase):
    """Caso del 31-oct-2026: la plantilla reescrita debe caer en los SKUs de siempre."""

    @classmethod
    def setUpClass(cls):
        import migracion_plantilla as mp     # import local: no depende de pandas al cargar
        cls.mp = mp
        cls.plantilla = mp.leer_plantilla(os.path.join(BASE, "respaldos",
                                                       "Sistema_Inventario_2026-10-31_sin_SKU.xlsx"))
        cls.inventario = leer_inventario()

    def test_la_plantilla_reproduce_el_prefijo_de_cada_familia(self):
        """Ningún renglón de la plantilla estrena prefijo: todos heredan el histórico."""
        prefijos = {f.prefijo for f in self.mp.FAMILIAS}
        for fila in self.plantilla:
            prefijo = sk.prefijo_de_sku(fila["prefijo"] + " 01")
            self.assertIn(prefijo, prefijos, f"{fila['producto']}: prefijo {prefijo}")
            self.assertEqual(prefijo, sk.prefijo_para(fila["producto"]), fila["producto"])

    def test_el_inventario_migrado_se_recodifica_con_los_mismos_skus(self):
        """Si se borraran los SKU, `sku_nemet` devolvería el mismo juego por familia."""
        df = self.inventario.copy()
        historicos = list(df["SKU"])
        df["SKU"] = ""
        resultado, asignados = sk.asignar_skus_faltantes(df, "Descripcion", "Presentacion", "SKU")
        self.assertEqual(len(asignados), len(df))
        self.assertEqual(sk.skus_duplicados(resultado), [])
        self.assertEqual(sorted(resultado["SKU"]), sorted(historicos))

    def test_los_skus_de_la_plantilla_no_se_repiten_entre_familias(self):
        por_familia = {}
        for fila in self.plantilla:
            por_familia.setdefault(fila["prefijo"], set()).add(fila["clave"])
        # Sólo dos prefijos agrupan dos familias con nombre propio (combinaciones).
        for prefijo, claves in por_familia.items():
            self.assertEqual(len(claves), 1, f"{prefijo} agrupa {sorted(claves)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
