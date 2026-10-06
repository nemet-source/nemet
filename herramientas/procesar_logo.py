#!/usr/bin/env python3
"""Sistema de marca NEMET — procesador de logos originales.

Convierte el logo original del usuario en los 3 assets de la app:

  python herramientas/procesar_logo.py /tmp/logo_claro.png
      Logo claro (fotografía del logo en relieve sobre muro crema)
        -> assets/logo_claro.png   (recorte transparente)
        -> assets/favicon.png      (isotipo 'N' sobre pastilla crema, 128 px)
        -> assets/hero_claro.png   (portada crema 1600x900 del dashboard)

El fondo se auto-detecta: si la imagen trae canal alfa se respeta; si es opaca se
estima el color del borde (muro/crema) y se elimina por densidad de color —
así las piezas de barro conservan su textura y los trozos decorativos pegados al
borde de la foto se descartan. La marca ya no usa letterpress oscuro: una entrada
con fondo negro se rechaza.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

BASE_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_ASSETS = os.path.join(BASE_REPO, "assets")

LADO_MAXIMO = 1400      # px máximos del lado largo de los logos
LOGO_PDF_ANCHO = 400    # px del logo que se incrusta en las cotizaciones PDF
DENS_MIN = 52           # densidad mínima de distancia al fondo (contenido "sólido")
SOFT_MIN = 42           # densidad mínima para las filas de texto fino (lema)
CON_SOFT_MIN = 14       # contraste local mínimo complementario para el lema
FEATHER = 1.15          # sigma de suavizado del borde alfa
RELLENO_PAD = 0.045     # margen de recorte relativo al tamaño del contenido

# ---------------------------------------------------------------- utilidades


def estimar_color_fondo(arr_rgb: np.ndarray) -> np.ndarray:
    """Color del fondo = mediana de los píxeles del borde (robusta a salpicaduras)."""
    borde = np.concatenate([arr_rgb[0], arr_rgb[-1], arr_rgb[:, 0], arr_rgb[:, -1]])
    return np.median(borde, axis=0)


def luma(color: np.ndarray) -> float:
    return float(0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2])


def modo_entrada(arr_rgba: np.ndarray) -> str:
    """'alpha' si la imagen ya viene recortada; 'claro'/'oscuro' según el fondo opaco."""
    alpha = arr_rgba[..., 3]
    if float((alpha < 128).mean()) > 0.05:
        return "alpha"
    fondo = estimar_color_fondo(arr_rgba[..., :3].astype(np.float64))
    return "claro" if luma(fondo) >= 140.0 else "oscuro"


def _contraste_local(arr: np.ndarray, sigma: float = 40.0) -> np.ndarray:
    """Magnitud del contraste local (luma + saturación) contra un fondo desenfocado."""
    dif = arr - ndi.gaussian_filter(arr, sigma=(sigma, sigma, 0))
    lum = dif @ np.array([0.299, 0.587, 0.114])
    sat = dif.max(-1) - dif.min(-1)
    return np.maximum(np.abs(lum), sat * 0.7)


def _matar_componentes(mask, min_size=90, margin=5, max_frac=0.30, central=True,
                       corredor=(0.15, 0.85, 0.12, 0.88)):
    """Descarta ruido (islas chicas), decoración pegada al borde y piezas descentradas."""
    lab, n = ndi.label(mask)
    if n == 0:
        return mask
    tam = ndi.sum(mask, lab, np.arange(1, n + 1))
    cajas = ndi.find_objects(lab)
    alto, ancho = mask.shape
    matar = []
    for i in range(n):
        if tam[i] < min_size or tam[i] > max_frac * alto * ancho:
            matar.append(i + 1)
            continue
        ys, xs = cajas[i]
        if ys.start <= margin or xs.start <= margin or ys.stop >= alto - margin or xs.stop >= ancho - margin:
            matar.append(i + 1)
            continue
        if central:
            cx, cy = (xs.start + xs.stop) / 2.0, (ys.start + ys.stop) / 2.0
            if not (corredor[0] * ancho <= cx <= corredor[1] * ancho and
                    corredor[2] * alto <= cy <= corredor[3] * alto):
                matar.append(i + 1)
    return mask & ~np.isin(lab, np.unique(matar)) if matar else mask


def _bandas(mask: np.ndarray, min_grosor=4, min_pix=6):
    """Bandas de filas con contenido (isotipo / nombre / lema)."""
    prof = mask.sum(1)
    activas = np.nonzero(prof > min_pix)[0]
    bandas, actual = [], None
    for y in activas:
        if actual is None:
            actual = [y, y]
        elif y <= actual[1] + 1:
            actual[1] = y
        else:
            bandas.append(tuple(actual))
            actual = [y, y]
    if actual:
        bandas.append(tuple(actual))
    return [b for b in bandas if b[1] - b[0] >= min_grosor]


# ---------------------------------------------------------------- pipelines


def mascara_contenido(arr_rgb: np.ndarray, dens_min=DENS_MIN, soft_min=SOFT_MIN) -> np.ndarray:
    """Máscara de logo para imágenes opacas sobre fondo uniforme/texturizado."""
    arr = arr_rgb.astype(np.float64)
    fondo = estimar_color_fondo(arr)
    dist = np.sqrt(((arr - fondo) ** 2).sum(-1))
    dens = ndi.gaussian_filter(dist, 8)
    con = _contraste_local(arr)

    mask = dens > dens_min
    mask = ndi.binary_opening(mask, np.ones((2, 2)))
    mask = ndi.binary_closing(mask, np.ones((3, 3)))
    mask = _matar_componentes(mask)

    # caja del contenido principal (isotipo + nombre + lema)
    lab, n = ndi.label(mask)
    if n == 0:
        raise SystemExit("No se detectó contenido: revisa el fondo de la imagen.")
    tam = ndi.sum(mask, lab, np.arange(1, n + 1))
    grandes = np.isin(lab, np.nonzero(tam >= 500)[0] + 1)
    ys, xs = np.nonzero(grandes)
    alto, ancho = mask.shape
    pad = int(RELLENO_PAD * max(ys.max() - ys.min(), xs.max() - xs.min()))
    y0, y1 = max(0, ys.min() - pad), min(alto, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(ancho, xs.max() + 1 + pad)
    sub = mask[y0:y1, x0:x1].copy()
    sub = _matar_componentes(sub, min_size=50, margin=3, max_frac=0.85, central=False)

    # el lema (texto gris claro) necesita un umbral suave, solo en su propia banda
    bandas = _bandas(sub)
    if len(bandas) >= 2:
        ultima, previa = bandas[-1], bandas[-2]
        if ultima[0] - previa[1] >= 25:
            zona = np.zeros_like(sub)
            zona[max(0, ultima[0] - 3):ultima[1] + 4] = True
            suave = (((dens > soft_min) | ((dens > 34) & (con > CON_SOFT_MIN))) & ~sub) & zona
            lab2, n2 = ndi.label(suave)
            t2 = ndi.sum(suave, lab2, np.arange(1, n2 + 1))
            buenas = np.nonzero(t2 >= 50)[0] + 1
            if len(buenas):
                sub = sub | np.isin(lab2, buenas)

    # los huecos cerrados del isotipo son piezas de barro color muro: se restauran
    if bandas:
        hoyos = ~sub
        labh, nh = ndi.label(hoyos)
        th = ndi.sum(hoyos, labh, np.arange(1, nh + 1))
        cajas = ndi.find_objects(labh)
        hh, ww = hoyos.shape
        rell = [i + 1 for i in range(nh)
                if 150 < th[i] < 25000
                and cajas[i][0].start > 1 and cajas[i][1].start > 1
                and cajas[i][0].stop < hh - 1 and cajas[i][1].stop < ww - 1
                and cajas[i][0].stop <= bandas[0][1] + 3]
        if rell:
            sub = sub | np.isin(labh, np.unique(rell))
    return sub, (x0, y0, x1, y1)


def procesar_claro(arr_rgba: np.ndarray, max_side=LADO_MAXIMO) -> Image.Image:
    """Ruta del logo claro opaco: máscara por densidad + recorte + alfa planoado."""
    mask, (x0, y0, x1, y1) = mascara_contenido(arr_rgba[..., :3])
    alpha = _alpha_suavizado(mask)
    return _ensamblar(arr_rgba, alpha, (x0, y0, x1, y1), max_side)


def procesar_alpha(arr_rgba: np.ndarray, max_side=LADO_MAXIMO) -> Image.Image:
    """Ruta con alfa: respeta el alfa existente, limpia islas y recorta al contenido."""
    alpha = arr_rgba[..., 3].astype(np.float64)
    mask = alpha > 24
    mask = ndi.binary_opening(mask, np.ones((2, 2)))
    mask = _matar_componentes(mask, min_size=200, margin=2, max_frac=0.90, central=False)
    ys, xs = np.nonzero(mask)
    alto, ancho = mask.shape
    pad = int(RELLENO_PAD * max(ys.max() - ys.min(), xs.max() - xs.min()))
    y0, y1 = max(0, ys.min() - pad), min(alto, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(ancho, xs.max() + 1 + pad)
    sub = mask[y0:y1, x0:x1]
    suav = ndi.gaussian_filter(alpha[y0:y1, x0:x1] * sub, FEATHER) * 1.15
    alpha_out = suav.clip(0, 255)
    return _ensamblar(arr_rgba, alpha_out, (x0, y0, x1, y1), max_side)


def _alpha_suavizado(mask: np.ndarray) -> np.ndarray:
    a = (mask * 255).astype(np.float64)
    a = (ndi.gaussian_filter(a, FEATHER) * 1.85).clip(0, 255)
    a[~ndi.binary_dilation(mask, np.ones((3, 3)), 1)] = 0
    return a


def _ensamblar(arr_rgba: np.ndarray, alpha: np.ndarray, caja, max_side: int) -> Image.Image:
    x0, y0, x1, y1 = caja
    rgba = np.dstack([arr_rgba[y0:y1, x0:x1, :3].astype(np.float64), alpha.astype(np.float64)])
    out = Image.fromarray(np.round(rgba).clip(0, 255).astype(np.uint8), "RGBA")
    if max(out.size) > max_side:
        escala = max_side / max(out.size)
        out = out.resize((round(out.width * escala), round(out.height * escala)), Image.LANCZOS)
    return out


def procesar(arr_rgba: np.ndarray, forzar: str | None = None, max_side=LADO_MAXIMO) -> tuple[Image.Image, str]:
    """Auto-detecta el fondo y devuelve (logoRGBA, modo). Rechaza el fondo oscuro."""
    modo = forzar if forzar in ("alpha", "claro", "oscuro") else modo_entrada(arr_rgba)
    if modo == "oscuro":
        raise SystemExit(
            "La marca NEMET ya no usa el letterpress oscuro: la app usa solo el logo "
            "claro (fondo crema o transparente).")
    if modo == "alpha":
        logo = procesar_alpha(arr_rgba, max_side)
    else:
        logo = procesar_claro(arr_rgba, max_side)
    return logo, modo


# ---------------------------------------------------------------- derivados


def generar_favicon(logo_claro: Image.Image, tam: int = 128, radio: int = 26) -> Image.Image:
    """Isotipo (banda superior del logo) centrado sobre una pastilla crema redondeada.

    El corte del isotipo se hace en la primera fila de tinta oscura (las letras del
    nombre son el único contenido neutro y oscuro; los bordes del isotipo son más claros).
    """
    arr = np.array(logo_claro)
    alpha = arr[..., 3] > 24
    rgb = arr[..., :3].astype(np.float64)
    brillo = rgb @ np.array([0.299, 0.587, 0.114])
    saturacion = rgb.max(-1) - rgb.min(-1)
    tinta = alpha & (brillo < 100) & (saturacion < 30)
    filas_tinta = np.nonzero(tinta.sum(1) >= 4)[0]
    if len(filas_tinta) and filas_tinta.min() > 4:
        y0, y1 = 0, int(filas_tinta.min()) - 2
    else:
        bandas = _bandas(alpha, min_grosor=2, min_pix=1)
        y0, y1 = (0, bandas[0][1] + 2) if len(bandas) >= 2 else (0, alpha.shape[0])
    ys, xs = np.nonzero(alpha[y0:y1])
    if len(ys) == 0:
        raise SystemExit("El logo no tiene isotipo para el favicon.")
    caja = (xs.min(), xs.max(), ys.min(), ys.max())
    ancho = caja[1] - caja[0] + 1
    alto = caja[3] - caja[2] + 1
    lado = max(ancho, alto)
    recorte = logo_claro.crop((caja[0], y0 + caja[2], caja[0] + lado, y0 + caja[2] + lado))
    lado_int = max(1, round(tam * 0.72))
    recorte = recorte.resize((lado_int, lado_int), Image.LANCZOS)
    fondo = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    from PIL import ImageDraw
    dibujante = ImageDraw.Draw(fondo)
    dibujante.rounded_rectangle([0, 0, tam - 1, tam - 1], radius=radio, fill=(240, 233, 221, 255))
    offset = (tam - lado_int) // 2
    fondo.alpha_composite(recorte, (offset, offset))
    return fondo


def generar_hero(logo_claro: Image.Image, ancho=1600, alto=900) -> Image.Image:
    """Portada crema con viñeta radial sutil y el logo claro centrado."""
    yy, xx = np.mgrid[0:alto, 0:ancho]
    r = np.sqrt(((xx - ancho / 2) / (ancho / 2)) ** 2 + ((yy - alto / 2) / (alto / 2)) ** 2)
    base = np.clip(245.0 - 12.0 * r, 224.0, 245.0)  # crema #F5EFE6 con borde sutilmente más profundo
    lienzo = np.dstack([base, base * 0.9755, base * 0.9388]).astype(np.uint8)
    lienzo = np.dstack([lienzo, np.full((alto, ancho), 255, np.uint8)])
    hero = Image.fromarray(lienzo, "RGBA")
    objetivo = int(ancho * 0.52)
    escala = objetivo / logo_claro.width
    logo = logo_claro.resize((objetivo, max(1, round(logo_claro.height * escala))), Image.LANCZOS)
    if logo.height > alto * 0.82:
        e2 = (alto * 0.82) / logo.height
        logo = logo.resize((max(1, round(logo.width * e2)), int(alto * 0.82)), Image.LANCZOS)
    hero.alpha_composite(logo, ((ancho - logo.width) // 2, (alto - logo.height) // 2))
    return hero


# ---------------------------------------------------------------- CLI


def generar_logo_pdf(logo_claro: Image.Image, ancho: int = LOGO_PDF_ANCHO) -> Image.Image:
    """Logo reducido para el PDF de cotizaciones (400 px de ancho, paleta con alfa).

    La versión de la app pesa ~900 KB; incrustarla en cada PDF que se descarga y se
    manda por correo es inviable, así que se guarda aparte una copia ligera que conserva
    el borde difuminado del recorte (tRNS con alfa por entrada de paleta).
    """
    ancho = min(ancho, logo_claro.width)  # nunca se escala hacia arriba
    escala = ancho / logo_claro.width
    reducido = logo_claro.resize((ancho, max(1, round(logo_claro.height * escala))), Image.LANCZOS)
    return reducido.quantize(colors=255, method=Image.FASTOCTREE)


def procesar_archivo(ruta: str, salida: str, max_side=LADO_MAXIMO) -> list[str]:
    im = Image.open(ruta)
    arr = np.array(im.convert("RGBA"))
    logo, modo = procesar(arr, max_side=max_side)  # lanza SystemExit si el fondo es oscuro
    os.makedirs(salida, exist_ok=True)
    escritos = []
    for imagen, nombre in ((logo, "logo_claro.png"),
                           (generar_logo_pdf(logo), "logo_claro_400.png"),
                           (generar_favicon(logo), "favicon.png"),
                           (generar_hero(logo), "hero_claro.png")):
        ruta_out = os.path.join(salida, nombre)
        imagen.save(ruta_out, optimize=True)
        escritos.append(ruta_out)
    print(f"[NEMET] {os.path.basename(ruta)}: fondo detectado = {modo}")
    for ruta_out in escritos:
        info = Image.open(ruta_out)
        print(f"[NEMET]   -> {os.path.relpath(ruta_out, BASE_REPO)} ({info.width}x{info.height})")
    return escritos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Genera los assets de marca NEMET desde el logo original.")
    parser.add_argument("entrada", help="PNG original del logo claro")
    parser.add_argument("--salida", default=DIR_ASSETS, help="Directorio de destino (por defecto: assets/)")
    parser.add_argument("--largo", type=int, default=LADO_MAXIMO, help="Lado máximo en px de los logos")
    args = parser.parse_args(argv)
    if not os.path.exists(args.entrada):
        parser.error(f"No existe el archivo de entrada: {args.entrada}")
    procesar_archivo(args.entrada, args.salida, max_side=args.largo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
