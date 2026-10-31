#!/usr/bin/env python3
"""Aplica la migración de la plantilla del proveedor al libro de la app.

Uso:
    python herramientas/migrar_plantilla.py --revisar     # solo muestra el reporte
    python herramientas/migrar_plantilla.py               # escribe el Excel

Lee `respaldos/Sistema_Inventario_2026-10-31_sin_SKU.xlsx` (la lista de precios del
proveedor) y reescribe en `Sistema_Inventario_NEMET_Final.xlsx`:

* `Inventario` — 88 fichas con nombres de `Cat_Productos`, precio por presentación con
  IVA, `PrecioCompra` del proveedor, `Línea` corregida y SKU en cada renglón;
* `Cat_Productos` — se agregan las familias que no tenían `Rendimiento` ni `Dosis_g_m2`;
* `Escalas_Precios_KG` — se agregan las escalas que solo vivían en la plantilla.

Las demás hojas (`Clientes`, `Historial_Cotizaciones`, …) no se tocan. `--revisar` sirve
para ver qué cambiaría sin escribir nada (útil antes de un respaldo).
"""
import argparse
import os
import re
import sys

import openpyxl
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import migracion_plantilla as mp  # noqa: E402

EXCEL = os.path.join(BASE, "Sistema_Inventario_NEMET_Final.xlsx")
PLANTILLA = os.path.join(BASE, "respaldos", "Sistema_Inventario_2026-10-31_sin_SKU.xlsx")


def leer_inventario(ruta):
    """Inventario como lo lee la app: encabezado en la fila 2 y columnas limpias."""
    df = pd.read_excel(ruta, sheet_name=mp.HOJA_INVENTARIO, header=1)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    df = df.loc[:, ~df.columns.astype(str).str.match(r"^Unnamed")]
    return df.dropna(how="all").reset_index(drop=True)


HOJA_COTIZADOR_EXCEL = "Cotizador_Rendimientos"
_RE_RANGO_CATALOGO = re.compile(r"(Cat_Productos!\$[A-Z]+\$2:\$[A-Z]+\$)\d+")


def ajustar_rangos_catalogo(ruta):
    """Extiende los VLOOKUP/SUMIFS del cotizador del Excel al tamaño actual del catálogo.

    Esa hoja no la usa la app, pero si alguien la abre a mano después de que `Cat_Productos`
    creció, las fórmulas se quedan cortas y devuelven cero. Devuelve cuántas fórmulas tocó.
    """
    libro = openpyxl.load_workbook(ruta)
    if mp.HOJA_CATALOGO not in libro.sheetnames or HOJA_COTIZADOR_EXCEL not in libro.sheetnames:
        return 0
    ultima = libro[mp.HOJA_CATALOGO].max_row
    tocadas = 0
    hoja = libro[HOJA_COTIZADOR_EXCEL]
    for fila in hoja.iter_rows():
        for celda in fila:
            if isinstance(celda.value, str) and celda.value.startswith("=") and "Cat_Productos!" in celda.value:
                nueva = _RE_RANGO_CATALOGO.sub(lambda m: f"{m.group(1)}{ultima}", celda.value)
                if nueva != celda.value:
                    celda.value = nueva
                    tocadas += 1
    if tocadas:
        libro.save(ruta)
    return tocadas


def _reporte(informe, agregadas, escalas_nuevas, validacion):
    filas = informe["filas"]
    print(f"  · {len(filas)} presentaciones con precio y costo de la plantilla")
    cambios = [f for f in filas if abs(f["precio"] - f["precio_antes"]) > 0.01]
    con_costo = [f for f in filas if f["costo_antes"] <= 0 < f["costo"]]
    print(f"  · {len(cambios)} precios cambian respecto al catálogo anterior")
    print(f"  · {len(con_costo)} renglones estrenan PrecioCompra (antes no tenían costo)")
    if informe["sin_dato"]:
        print(f"  · {len(informe['sin_dato'])} presentaciones sin dato en la plantilla (conservan su precio):")
        for fila in informe["sin_dato"]:
            print(f"      - {fila['sku']} · {fila['familia']} · {fila['presentacion']}")
    if informe["plantilla_sin_usar"]:
        print(f"  · {len(informe['plantilla_sin_usar'])} renglones de la plantilla sin destino:")
        for texto in informe["plantilla_sin_usar"]:
            print(f"      - {texto}")
    if informe.get("skus_generados"):
        print(f"  · SKUs generados por sku_nemet: {informe['skus_generados']}")
    if informe.get("lineas_corregidas"):
        print(f"  · {len(informe['lineas_corregidas'])} renglones traían la columna Línea corrida")
    if agregadas:
        print(f"  · Cat_Productos: +{len(agregadas)} familias ({', '.join(agregadas)})")
    if escalas_nuevas:
        print(f"  · Escalas_Precios_KG: +{len(escalas_nuevas)} tramos ({', '.join(sorted(set(escalas_nuevas)))})")
    if validacion["errores"]:
        print("  ✗ Errores de validación:")
        for error in validacion["errores"]:
            print(f"      - {error}")
    if validacion["avisos"]:
        print(f"  ⚠ {len(validacion['avisos'])} avisos:")
        for aviso in validacion["avisos"]:
            print(f"      - {aviso}")


def main():
    parser = argparse.ArgumentParser(description="Migra la plantilla del proveedor al catálogo NEMET.")
    parser.add_argument("--plantilla", default=PLANTILLA, help="libro con los precios del proveedor")
    parser.add_argument("--excel", default=EXCEL, help="libro de la app que se actualiza")
    parser.add_argument("--revisar", action="store_true", help="no escribe el Excel; solo reporta")
    args = parser.parse_args()

    for ruta in (args.plantilla, args.excel):
        if not os.path.exists(ruta):
            sys.exit(f"No se encontró {ruta}")

    print(f"Plantilla: {os.path.relpath(args.plantilla, BASE)}")
    filas_plantilla = mp.leer_plantilla(args.plantilla)
    print(f"  · {len(filas_plantilla)} renglones de precio leídos")

    inventario = leer_inventario(args.excel)
    catalogo = pd.read_excel(args.excel, sheet_name=mp.HOJA_CATALOGO)
    escalas = pd.read_excel(args.excel, sheet_name=mp.HOJA_ESCALAS)

    migrado, informe = mp.migrar_inventario(inventario, filas_plantilla)
    catalogo_nuevo, agregadas = mp.ampliar_catalogo(catalogo, migrado)
    escalas_nuevas_df, escalas_nuevas = mp.ampliar_escalas(escalas, filas_plantilla)
    validacion = mp.validar(migrado, catalogo_nuevo, escalas_nuevas_df)

    print(f"Inventario: {len(migrado)} fichas")
    _reporte(informe, agregadas, escalas_nuevas, validacion)

    if args.revisar:
        print("\n(--revisar) No se escribió nada.")
        return 0 if not validacion["errores"] else 1

    migrado = mp._orden_columnas(migrado)
    with pd.ExcelWriter(args.excel, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        migrado.to_excel(writer, sheet_name=mp.HOJA_INVENTARIO, index=False, startrow=1)
        writer.sheets[mp.HOJA_INVENTARIO].cell(row=1, column=1, value=mp.TITULO_INVENTARIO)
        catalogo_nuevo.to_excel(writer, sheet_name=mp.HOJA_CATALOGO, index=False)
        escalas_nuevas_df.to_excel(writer, sheet_name=mp.HOJA_ESCALAS, index=False)
    if ajustar_rangos_catalogo(args.excel):
        print(f"  · Cotizador_Rendimientos del Excel: rangos ajustados a {len(catalogo_nuevo)} filas de catálogo")
    print(f"\n✔ {os.path.basename(args.excel)} actualizado.")
    return 0 if not validacion["errores"] else 1


if __name__ == "__main__":
    sys.exit(main())
