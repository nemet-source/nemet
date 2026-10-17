# -*- coding: utf-8 -*-
"""Sistema visual moderno de NEMET.

Streamlit no expone una API de estilos, así que el tema se aplica inyectando una
hoja de estilo con ``st.markdown(..., unsafe_allow_html=True)``. Este módulo
concentra tres cosas:

* la **hoja de estilo** completa (``aplicar()``): tipografía, superficies, botones,
  campos, pestañas, tablas, alertas, barra lateral y vista adaptable a móvil;
* los **componentes** que Streamlit no trae de fábrica y que se dibujan con HTML
  (``encabezado``, ``kpi``, ``chip``, ``panel``, ``pie``);
* los **tokens de color** coherentes con ``PALETA_NEMET`` de ``app.py`` y con el
  tema de ``.streamlit/config.toml`` (crema, tinta, barro, verde y piedra).

Todos los textos que llegan de datos (nombres, SKUs, errores) se escapan con
``escapar()`` antes de inyectarse en el HTML.
"""

import html

import streamlit as st

# ==========================================
# TOKENS DE COLOR
# ==========================================
CREMA = "#F5EFE6"        # fondo de la app (muro encalado)
CREMA_SUAVE = "#FBF8F3"  # superficies secundarias
TINTA = "#26231F"        # texto principal (carbón)
TINTA_SUAVE = "#4C4740"  # texto secundario
MUTE = "#8A837A"         # texto terciario / metadatos
BARRO = "#B4552D"        # acento primario (terracota)
BARRO_Fuerte = "#9A4526"
VERDE = "#2F5D3A"         # acento secundario (epoxi verde)
PIEDRA = "#8F8B84"        # gris medio (cemento)
LINEA = "rgba(38, 35, 31, 0.10)"

TONOS = {
    "barro": BARRO,
    "verde": VERDE,
    "piedra": PIEDRA,
    "tinta": TINTA,
}

# ==========================================
# HOJA DE ESTILO
# ==========================================
CSS = """
/* La @import debe ir primero en la hoja: si no, el navegador la descarta. */
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');

/* ============================================================
   1 · TOKENS
   ============================================================ */
:root {
  --nm-bg:          #F5EFE6;
  --nm-bg-soft:     #FBF8F3;
  --nm-card:        #FFFFFF;
  --nm-ink:         #26231F;
  --nm-ink-2:       #4C4740;
  --nm-muted:       #8A837A;
  --nm-clay:        #B4552D;
  --nm-clay-600:    #9A4526;
  --nm-clay-50:     rgba(180, 85, 45, 0.10);
  --nm-clay-100:    rgba(180, 85, 45, 0.16);
  --nm-green:       #2F5D3A;
  --nm-green-50:    rgba(47, 93, 58, 0.10);
  --nm-stone:       #8F8B84;
  --nm-line:        rgba(38, 35, 31, 0.10);
  --nm-line-2:      rgba(38, 35, 31, 0.16);
  --nm-shadow-sm:   0 1px 2px rgba(38, 35, 31, 0.05);
  --nm-shadow:      0 1px 2px rgba(38, 35, 31, 0.04), 0 14px 30px -22px rgba(38, 35, 31, 0.45);
  --nm-shadow-lg:   0 2px 6px rgba(38, 35, 31, 0.05), 0 28px 56px -30px rgba(38, 35, 31, 0.55);
  --nm-r-xs: 8px;  --nm-r-sm: 11px;  --nm-r: 14px;
  --nm-r-lg: 20px; --nm-r-xl: 28px;  --nm-r-pill: 999px;
  --nm-font:    'Plus Jakarta Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --nm-display: 'Fraunces', 'Playfair Display', Georgia, 'Times New Roman', serif;
  --nm-ease: cubic-bezier(.2, .8, .2, 1);
}

/* ============================================================
   2 · BASE Y TIPOGRAFÍA
   ============================================================ */
html, body, [class*="css"], .stApp, .stMarkdown, .stMarkdownContainer {
  font-family: var(--nm-font);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
html, body { background: var(--nm-bg); }

h1, h2 {
  font-family: var(--nm-display);
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--nm-ink);
  line-height: 1.12;
}
h1 { font-size: clamp(2rem, 3.4vw, 2.85rem); font-weight: 600; }
h2 { font-size: clamp(1.4rem, 2vw, 1.75rem); }
h3, h4, h5, h6 {
  font-family: var(--nm-font);
  font-weight: 700;
  letter-spacing: -0.012em;
  color: var(--nm-ink);
}
h3 { font-size: 1.18rem; }
h4 { font-size: 1.02rem; }

/* Los encabezados de Streamlit traen su propia clase de estilo: hay que ganarle
   en especificidad para que respeten la escala tipográfica de la marca. */
[data-testid="stMarkdownContainer"] h1 {
  font-family: var(--nm-font) !important;
  font-size: clamp(1.9rem, 2.9vw, 2.5rem) !important;
  font-weight: 800 !important;
  letter-spacing: -.035em !important;
  line-height: 1.1 !important;
  color: var(--nm-ink) !important;
}
[data-testid="stMarkdownContainer"] h2 {
  font-family: var(--nm-display) !important;
  font-size: clamp(1.3rem, 1.8vw, 1.6rem) !important;
  font-weight: 600 !important;
  letter-spacing: -.02em !important;
  color: var(--nm-ink) !important;
}
[data-testid="stMarkdownContainer"] h3 {
  font-family: var(--nm-font) !important;
  font-size: 1.1rem !important;
  font-weight: 700 !important;
  letter-spacing: -.012em !important;
  color: var(--nm-ink) !important;
}
[data-testid="stMarkdownContainer"] h4 {
  font-family: var(--nm-font) !important;
  font-size: .98rem !important;
  font-weight: 700 !important;
  color: var(--nm-ink) !important;
}
p, li, span, label, td, th, div { letter-spacing: -0.005em; }

a { color: var(--nm-clay-600); text-decoration-color: rgba(180, 85, 45, .35); }
a:hover { color: var(--nm-clay); }

code {
  font-family: 'SFMono-Regular', ui-monospace, Menlo, Consolas, monospace !important;
  font-size: .84em !important;
  background: var(--nm-clay-50) !important;
  color: var(--nm-clay-600) !important;
  border: 1px solid rgba(180, 85, 45, .16);
  border-radius: 6px;
  padding: .08em .38em !important;
}

/* ============================================================
   3 · FONDO Y CONTENEDOR
   ============================================================ */
.stApp {
  background:
    radial-gradient(1100px 520px at 8% -8%, rgba(255, 255, 255, .85) 0%, rgba(255, 255, 255, 0) 62%),
    radial-gradient(900px 480px at 102% -4%, rgba(180, 85, 45, .075) 0%, rgba(255, 255, 255, 0) 58%),
    var(--nm-bg) !important;
}
[data-testid="stMainBlockContainer"] {
  padding-top: 2.4rem;
  padding-bottom: 3.5rem;
  max-width: 1360px;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { right: 1rem; }
hr { border-color: var(--nm-line) !important; margin: 1.6rem 0 !important; }
[data-testid="stCaptionContainer"] p {
  color: var(--nm-muted) !important;
  font-size: .8rem !important;
  line-height: 1.5;
}
[data-testid="stWidgetLabel"] p {
  font-weight: 600 !important;
  font-size: .82rem !important;
  color: var(--nm-ink-2) !important;
  letter-spacing: .005em;
}

/* ============================================================
   4 · BARRA LATERAL
   ============================================================ */
[data-testid="stSidebar"] {
  background: linear-gradient(178deg, #FFFDF9 0%, #F8F2E9 100%) !important;
  border-right: 1px solid var(--nm-line);
}
/* Barra lateral compacto: menos ancha que el ancho por defecto de Streamlit
   (~336px) para dar ese espacio al contenido, y con menos aire vertical para
   que menú, respaldo y bloque de usuario quepan sin tener que bajar tanto.
   Solo se acota cuando está desplegada: el colapso queda como lo trae Streamlit. */
[data-testid="stSidebar"][aria-expanded="true"] {
  min-width: 248px !important;
  max-width: 272px !important;
}
[data-testid="stSidebarUserContent"] {
  padding: 1.1rem 1rem 1rem !important;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .15rem; }
[data-testid="stSidebar"] .stButton > button,
[data-testid="stSidebar"] .stDownloadButton > button {
  width: 100%;
  padding: .35rem .7rem !important;
  font-size: .85rem !important;
}
/* Los separadores y expansores heredan el aire generoso de la vista principal;
   en la barra lateral compacto se pegan un poco más a su contenido. */
[data-testid="stSidebar"] hr { margin: .55rem 0 !important; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  padding: .45rem .7rem !important;
  font-size: .84rem !important;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
  font-size: .72rem !important;
  line-height: 1.4;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { font-size: .78rem !important; }
/* El logo de la barra lateral se muestra más chico y centrado (ver app.py). */
[data-testid="stSidebar"] [data-testid="stImage"] {
  display: flex;
  justify-content: center;
}
[data-testid="stSidebar"] [data-testid="stImage"] img {
  max-width: 120px;
  height: auto;
}
/* El título del menú se vuelve una micro-etiqueta, no un encabezado gigante. */
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1 {
  font-family: var(--nm-font) !important; font-size: .68rem !important; font-weight: 800 !important;
  letter-spacing: .16em !important; text-transform: uppercase; color: var(--nm-muted) !important;
  text-align: center; margin: .1rem 0 .2rem 0; line-height: 1.4 !important;
}

/* Franja de marca (firma + lema) de las vistas */
.nm-strip {
  display: flex; align-items: center; gap: .9rem;
  padding: .1rem 0 .85rem 0; margin-bottom: 1.1rem;
  border-bottom: 1px solid var(--nm-line);
}
.nm-strip__mark {
  font-family: var(--nm-display); font-size: 1.05rem; font-weight: 600;
  letter-spacing: .22em; color: var(--nm-ink); text-transform: uppercase;
}
.nm-strip__line { flex: 1 1 auto; height: 1px; background: var(--nm-line); }

.nm-side-brand {
  display: flex; flex-direction: column; align-items: center; gap: .35rem;
  padding: .3rem 0 .5rem;
}
.nm-side-brand img { border-radius: var(--nm-r-sm); }
.nm-eyebrow {
  font-size: .66rem; font-weight: 800; letter-spacing: .16em;
  text-transform: uppercase; color: var(--nm-muted);
}
.nm-side-user {
  display: flex; align-items: center; gap: .55rem;
  background: var(--nm-card);
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r);
  padding: .5rem .6rem;
  box-shadow: var(--nm-shadow-sm);
}
.nm-side-user__avatar {
  width: 32px; height: 32px; flex: 0 0 32px;
  border-radius: 10px;
  display: grid; place-items: center;
  font-weight: 800; font-size: .82rem; color: #fff;
  background: linear-gradient(150deg, var(--nm-clay) 0%, var(--nm-clay-600) 100%);
  box-shadow: 0 4px 10px -4px rgba(180, 85, 45, .7);
}
.nm-side-user__name { font-weight: 700; font-size: .82rem; color: var(--nm-ink); line-height: 1.25; }
.nm-side-user__meta { font-size: .68rem; color: var(--nm-muted); }
.nm-role-chip {
  display: inline-block; margin-top: .12rem;
  font-size: .6rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
  color: var(--nm-green); background: var(--nm-green-50);
  border: 1px solid rgba(47, 93, 58, .18);
  border-radius: var(--nm-r-pill); padding: .05rem .45rem;
}

/* ============================================================
   5 · ENCABEZADO DE PÁGINA Y COMPONENTES PROPIOS
   ============================================================ */
.nm-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 1.5rem; flex-wrap: wrap;
  margin: 0 0 1.5rem;
  padding-bottom: 1.1rem;
  border-bottom: 1px solid var(--nm-line);
}
.nm-head__eyebrow {
  display: inline-flex; align-items: center; gap: .45rem;
  font-size: .68rem; font-weight: 800; letter-spacing: .17em; text-transform: uppercase;
  color: var(--nm-clay-600);
  margin-bottom: .5rem;
}
.nm-head__eyebrow::before {
  content: ""; width: 22px; height: 2px; border-radius: 2px;
  background: linear-gradient(90deg, var(--nm-clay), rgba(180, 85, 45, .15));
}
.nm-head__title {
  font-family: var(--nm-display);
  font-size: clamp(1.55rem, 2.4vw, 2.05rem);
  font-weight: 600; letter-spacing: -0.02em; line-height: 1.15;
  color: var(--nm-ink); margin: 0;
}
.nm-head__sub { margin: .4rem 0 0; color: var(--nm-muted); font-size: .92rem; max-width: 68ch; }

.nm-kpi {
  position: relative; overflow: hidden;
  background: var(--nm-card);
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r-lg);
  padding: 1.05rem 1.15rem 1.15rem;
  box-shadow: var(--nm-shadow);
  transition: transform .18s var(--nm-ease), box-shadow .18s var(--nm-ease);
}
.nm-kpi::before {
  content: ""; position: absolute; top: 0; left: 0; bottom: 0; width: 4px;
  background: var(--nm-clay);
}
.nm-kpi--verde::before { background: var(--nm-green); }
.nm-kpi--piedra::before { background: var(--nm-stone); }
.nm-kpi--tinta::before { background: var(--nm-ink); }
.nm-kpi__top { display: flex; align-items: flex-start; justify-content: space-between; gap: .6rem; }
.nm-kpi__label {
  font-size: .68rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase;
  color: var(--nm-muted); line-height: 1.35;
}
.nm-kpi__value {
  margin-top: .5rem;
  font-size: 1.9rem; font-weight: 800; letter-spacing: -.035em; line-height: 1.05;
  color: var(--nm-ink); font-variant-numeric: tabular-nums;
}
.nm-kpi__note { margin-top: .3rem; font-size: .76rem; color: var(--nm-muted); }
.nm-kpi__chip {
  width: 34px; height: 34px; flex: 0 0 34px; border-radius: 11px;
  display: grid; place-items: center; font-size: .95rem;
  background: var(--nm-clay-50); color: var(--nm-clay-600);
}
.nm-kpi--verde .nm-kpi__chip { background: var(--nm-green-50); color: var(--nm-green); }
.nm-kpi--piedra .nm-kpi__chip { background: rgba(143, 139, 132, .14); color: #6E6A63; }
.nm-kpi--tinta .nm-kpi__chip { background: rgba(38, 35, 31, .07); color: var(--nm-ink); }

.nm-chip {
  display: inline-flex; align-items: center; gap: .35rem;
  font-size: .68rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase;
  padding: .28rem .7rem; border-radius: var(--nm-r-pill);
  color: var(--nm-clay-600); background: var(--nm-clay-50);
  border: 1px solid rgba(180, 85, 45, .18);
}
.nm-chip--verde { color: var(--nm-green); background: var(--nm-green-50); border-color: rgba(47, 93, 58, .18); }
.nm-chip--neutro { color: var(--nm-ink-2); background: rgba(38, 35, 31, .05); border-color: var(--nm-line); }

.nm-panel {
  background: var(--nm-card);
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r-lg);
  box-shadow: var(--nm-shadow);
  padding: 1.25rem 1.4rem;
  margin-bottom: 1rem;
}
.nm-panel__title {
  font-family: var(--nm-display); font-size: 1.12rem; font-weight: 600;
  color: var(--nm-ink); margin: 0 0 .25rem;
}
.nm-panel__text { color: var(--nm-muted); font-size: .88rem; margin: 0; }

.nm-hero {
  position: relative; overflow: hidden;
  display: flex; align-items: center; gap: 2rem; flex-wrap: wrap;
  background:
    radial-gradient(760px 320px at 8% 0%, rgba(255, 255, 255, .95) 0%, rgba(255, 255, 255, 0) 60%),
    linear-gradient(122deg, #FFFDF9 0%, #F7EFE4 55%, #F2E4D8 100%);
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r-xl);
  box-shadow: var(--nm-shadow-lg);
  padding: 2rem 2.1rem;
  margin-bottom: 1.6rem;
}
.nm-hero__eyebrow {
  font-size: .68rem; font-weight: 800; letter-spacing: .17em; text-transform: uppercase;
  color: var(--nm-clay-600);
}
.nm-hero__title {
  font-family: var(--nm-display); font-size: clamp(1.7rem, 3vw, 2.4rem);
  font-weight: 600; letter-spacing: -.025em; line-height: 1.1; color: var(--nm-ink);
  margin: .35rem 0 .5rem;
}
.nm-hero__text { color: var(--nm-ink-2); font-size: .98rem; max-width: 56ch; margin: 0 0 1rem; }
.nm-hero__media { position: relative; z-index: 1; flex: 0 1 420px; }
.nm-hero__media img {
  border-radius: var(--nm-r-lg);
  box-shadow: var(--nm-shadow-lg);
  border: 1px solid var(--nm-line);
  display: block; width: 100%;
}
.nm-hero__facts { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .9rem; }

/* Hero del dashboard: el contenedor con clave se convierte en la tarjeta */
.st-key-nm_hero {
  background:
    radial-gradient(760px 320px at 6% 0%, rgba(255, 255, 255, .95) 0%, rgba(255, 255, 255, 0) 62%),
    linear-gradient(122deg, #FFFDF9 0%, #F7EFE4 58%, #F1E2D5 100%) !important;
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r-xl) !important;
  box-shadow: var(--nm-shadow-lg);
  padding: 1.9rem 2rem !important;
  margin-bottom: 1.5rem;
  overflow: hidden;
  position: relative;
}
.st-key-nm_hero::after {
  content: ""; position: absolute; right: -70px; top: -80px;
  width: 280px; height: 280px; border-radius: 50%;
  background: radial-gradient(circle at 32% 32%, rgba(47, 93, 58, .17), rgba(47, 93, 58, 0) 70%);
  pointer-events: none;
}
.st-key-nm_hero [data-testid="stHorizontalBlock"] { align-items: center; }
.st-key-nm_hero [data-testid="stColumn"] { position: relative; z-index: 1; }
.st-key-nm_hero [data-testid="stImage"] img {
  border-radius: var(--nm-r-lg) !important;
  border: 1px solid var(--nm-line);
  box-shadow: var(--nm-shadow-lg);
}

.nm-foot {
  margin-top: 2.4rem; padding-top: 1.1rem; border-top: 1px solid var(--nm-line);
  display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
}
.nm-foot__text { color: var(--nm-muted); font-size: .76rem; }
.nm-foot__brand {
  font-family: var(--nm-display); font-size: .95rem; font-weight: 600;
  color: var(--nm-ink); letter-spacing: .04em;
}

/* ============================================================
   6 · BOTONES
   ============================================================ */
.stButton > button, .stDownloadButton > button,
.stFormSubmitButton > button, button[data-testid^="stBaseButton-"] {
  border-radius: var(--nm-r-sm) !important;
  font-weight: 600 !important;
  letter-spacing: .005em;
  transition: transform .16s var(--nm-ease), box-shadow .16s var(--nm-ease),
              background .16s var(--nm-ease), border-color .16s var(--nm-ease);
}
button[data-testid="stBaseButton-primary"], button[kind="primary"] {
  background: linear-gradient(180deg, #C05C31 0%, var(--nm-clay-600) 100%) !important;
  color: #fff !important;
  border: 1px solid rgba(122, 53, 27, .35) !important;
  box-shadow: 0 8px 18px -10px rgba(164, 71, 37, .85);
}
button[data-testid="stBaseButton-primary"]:hover, button[kind="primary"]:hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 24px -12px rgba(164, 71, 37, .9);
}
button[data-testid="stBaseButton-secondary"], button[kind="secondary"] {
  background: var(--nm-card) !important;
  color: var(--nm-ink) !important;
  border: 1px solid var(--nm-line-2) !important;
  box-shadow: var(--nm-shadow-sm);
}
button[data-testid="stBaseButton-secondary"]:hover, button[kind="secondary"]:hover {
  border-color: rgba(180, 85, 45, .45) !important;
  color: var(--nm-clay-600) !important;
  transform: translateY(-1px);
}
button[data-testid="stBaseButton-primaryFormSubmit"], button[kind="primaryFormSubmit"] {
  background: linear-gradient(180deg, #C05C31 0%, var(--nm-clay-600) 100%) !important;
  color: #fff !important;
  border: 1px solid rgba(122, 53, 27, .35) !important;
  box-shadow: 0 8px 18px -10px rgba(164, 71, 37, .85);
}
button[data-testid="stBaseButton-secondaryFormSubmit"], button[kind="secondaryFormSubmit"] {
  background: var(--nm-card) !important; color: var(--nm-ink) !important;
  border: 1px solid var(--nm-line-2) !important;
}
button[kind="tertiary"], button[data-testid="stBaseButton-tertiary"] {
  background: transparent !important; color: var(--nm-clay-600) !important;
  border-color: transparent !important;
}
button:disabled { opacity: .45 !important; transform: none !important; }
button:focus-visible, input:focus-visible {
  outline: 2px solid rgba(180, 85, 45, .55) !important;
  outline-offset: 2px;
}

/* ============================================================
   7 · CAMPOS DE ENTRADA
   ============================================================ */
[data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"] {
  background: var(--nm-card) !important;
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r-sm) !important;
  transition: border-color .15s var(--nm-ease), box-shadow .15s var(--nm-ease);
}
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within {
  border-color: rgba(180, 85, 45, .55) !important;
  box-shadow: 0 0 0 4px rgba(180, 85, 45, .12) !important;
}
[data-testid="stTextInputRootElement"] input,
[data-testid="stNumberInputContainer"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stDateInput"] input {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  font-family: var(--nm-font) !important;
  font-weight: 500 !important;
  color: var(--nm-ink) !important;
  outline: none !important;
}
[data-testid="stTextInputRootElement"] input::placeholder { color: #B4ADA3; font-weight: 400; }
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"] {
  background: var(--nm-bg-soft) !important;
  border: 1px solid var(--nm-line) !important;
  border-radius: 7px !important;
  color: var(--nm-ink-2) !important;
}
[data-testid="stTextArea"] textarea {
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r-sm) !important;
  background: var(--nm-card) !important;
  padding: .6rem .75rem !important;
}
[data-testid="stTextArea"] textarea:focus {
  border-color: rgba(180, 85, 45, .55) !important;
  box-shadow: 0 0 0 4px rgba(180, 85, 45, .12) !important;
}

[data-testid="stSelectbox"] .react-aria-ComboBox > div,
[data-testid="stMultiSelect"] .react-aria-ComboBox > div {
  background: var(--nm-card) !important;
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r-sm) !important;
  transition: border-color .15s var(--nm-ease), box-shadow .15s var(--nm-ease);
}
[data-testid="stSelectbox"] .react-aria-ComboBox > div:hover,
[data-testid="stMultiSelect"] .react-aria-ComboBox > div:hover { border-color: var(--nm-line-2) !important; }
[data-testid="stSelectbox"] .react-aria-ComboBox > div:focus-within,
[data-testid="stMultiSelect"] .react-aria-ComboBox > div:focus-within {
  border-color: rgba(180, 85, 45, .55) !important;
  box-shadow: 0 0 0 4px rgba(180, 85, 45, .12) !important;
}
[data-testid="stSelectbox"] input[role="combobox"],
[data-testid="stMultiSelect"] input[role="combobox"] {
  background: transparent !important; border: none !important; outline: none !important;
  font-family: var(--nm-font) !important; font-weight: 600 !important; color: var(--nm-ink) !important;
}
[role="listbox"] {
  border-radius: var(--nm-r) !important;
  border: 1px solid var(--nm-line) !important;
  box-shadow: var(--nm-shadow-lg) !important;
  overflow: hidden !important;
  background: var(--nm-card) !important;
}
[role="option"] { border-radius: 8px !important; margin: 2px 6px !important; font-weight: 500 !important; }
[role="option"][aria-selected="true"],
[role="option"][data-selected="true"] {
  background: var(--nm-clay-50) !important; color: var(--nm-clay-600) !important; font-weight: 700 !important;
}

/* ============================================================
   8 · RADIO / CHECKBOX / TOGGLE
   ============================================================ */
[data-testid="stRadioGroup"] { gap: .5rem !important; flex-wrap: wrap; }
[data-testid="stRadioGroup"] > div > label[data-testid="stRadioOption"] {
  background: var(--nm-card);
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r-pill);
  padding: .38rem .85rem;
  transition: all .16s var(--nm-ease);
  margin: 0;
}
[data-testid="stRadioGroup"] > div > label[data-testid="stRadioOption"]:hover {
  border-color: rgba(180, 85, 45, .45);
  transform: translateY(-1px);
}
[data-testid="stRadioGroup"] > div > label[data-testid="stRadioOption"][data-selected="true"] {
  background: linear-gradient(180deg, #C05C31 0%, var(--nm-clay-600) 100%);
  border-color: rgba(122, 53, 27, .35);
  box-shadow: 0 6px 14px -8px rgba(164, 71, 37, .9);
}
[data-testid="stRadioGroup"] > div > label[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p {
  color: var(--nm-ink-2); font-weight: 600; font-size: .84rem;
}
[data-testid="stRadioGroup"] > div > label[data-testid="stRadioOption"][data-selected="true"] [data-testid="stMarkdownContainer"] p {
  color: #fff;
}
[data-testid="stCheckbox"] label, [data-testid="stToggle"] label { font-weight: 500; }
[data-testid="stCheckbox"] [data-testid="stMarkdownContainer"] p { font-size: .86rem; color: var(--nm-ink-2); }

/* ============================================================
   9 · PESTAÑAS
   ============================================================ */
[data-testid="stTabs"] [role="tablist"] {
  gap: .3rem !important;
  background: rgba(239, 231, 219, .75);
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r-pill);
  padding: .25rem;
  width: fit-content;
  max-width: 100%;
  overflow-x: auto;
}
[data-testid="stTabs"] [role="tab"] {
  border-radius: var(--nm-r-pill) !important;
  font-weight: 600 !important;
  font-size: .85rem !important;
  color: var(--nm-ink-2) !important;
  padding: .42rem .95rem !important;
  border: 1px solid transparent !important;
  transition: all .16s var(--nm-ease);
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
  background: var(--nm-card) !important;
  color: var(--nm-clay-600) !important;
  border-color: var(--nm-line) !important;
  box-shadow: var(--nm-shadow-sm);
}

/* ============================================================
   10 · EXPANSOR
   ============================================================ */
[data-testid="stExpander"] {
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r) !important;
  background: var(--nm-card) !important;
  overflow: hidden;
  box-shadow: var(--nm-shadow-sm);
}
[data-testid="stExpander"] summary {
  font-weight: 600 !important;
  font-size: .9rem !important;
  color: var(--nm-ink) !important;
  padding: .7rem .95rem !important;
  transition: background .15s var(--nm-ease);
}
[data-testid="stExpander"] summary:hover { background: var(--nm-bg-soft); }
[data-testid="stExpander"] summary [data-testid="stMarkdownContainer"] p { color: var(--nm-ink) !important; }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] { padding: .2rem .95rem .9rem; }

/* ============================================================
   11 · ALERTAS
   ============================================================ */
[data-testid="stAlert"] { border-radius: var(--nm-r) !important; border: 1px solid transparent !important; }
[data-testid="stAlertContainer"] {
  border-radius: var(--nm-r) !important;
  border: 1px solid var(--nm-line) !important;
  background: var(--nm-card) !important;
  box-shadow: var(--nm-shadow-sm);
  padding: .85rem 1rem !important;
}
[data-testid="stAlertContentSuccess"] { border-left: 3px solid var(--nm-green) !important; }
[data-testid="stAlertContentInfo"]    { border-left: 3px solid var(--nm-stone) !important; }
[data-testid="stAlertContentWarning"] { border-left: 3px solid #C68A16 !important; }
[data-testid="stAlertContentError"]   { border-left: 3px solid #A8322B !important; }
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p { font-size: .88rem; }

/* ============================================================
   12 · TABLAS
   ============================================================ */
[data-testid="stDataFrame"] {
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r) !important;
  overflow: hidden;
  background: var(--nm-card) !important;
  box-shadow: var(--nm-shadow-sm);
}
[data-testid="stDataFrame"] [role="grid"] { font-family: var(--nm-font) !important; font-size: .84rem !important; }
[data-testid="stDataFrame"] [role="columnheader"] {
  background: var(--nm-bg-soft) !important;
  font-weight: 700 !important;
  color: var(--nm-ink-2) !important;
  font-size: .74rem !important;
  letter-spacing: .05em;
  text-transform: uppercase;
}
[data-testid="stDataFrame"] [role="gridcell"] { font-variant-numeric: tabular-nums; }
[data-testid="stDataEditor"] { border-radius: var(--nm-r) !important; overflow: hidden; }

/* ============================================================
   13 · FORMULARIOS Y MÉTRICAS
   ============================================================ */
[data-testid="stForm"] {
  background: var(--nm-card) !important;
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r-lg) !important;
  padding: 1.3rem 1.45rem !important;
  box-shadow: var(--nm-shadow) !important;
}
[data-testid="stMetric"] {
  background: var(--nm-card) !important;
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r-lg) !important;
  padding: 1rem 1.1rem !important;
  box-shadow: var(--nm-shadow) !important;
}
[data-testid="stMetricLabel"] p {
  font-size: .68rem !important; font-weight: 800 !important;
  letter-spacing: .1em !important; text-transform: uppercase; color: var(--nm-muted) !important;
  white-space: normal !important;
}
[data-testid="stMetricLabel"] .stMarkdownEllipsis {
  white-space: normal !important; overflow: visible !important;
}
[data-testid="stMetricValue"] {
  font-size: 1.8rem !important; font-weight: 800 !important;
  letter-spacing: -.035em !important; color: var(--nm-ink) !important;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetricDelta"] { font-size: .78rem !important; }

/* Contenedores con borde de Streamlit → tarjetas de la marca */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--nm-card) !important;
  border: 1px solid var(--nm-line) !important;
  border-radius: var(--nm-r-lg) !important;
  box-shadow: var(--nm-shadow) !important;
}

/* Etiquetas de selección múltiple */
[data-baseweb="tag"] {
  background: var(--nm-clay-50) !important;
  color: var(--nm-clay-600) !important;
  border: 1px solid rgba(180, 85, 45, .2) !important;
  border-radius: var(--nm-r-pill) !important;
  font-weight: 600 !important;
  font-size: .78rem !important;
}
[data-baseweb="tag"] svg, [data-baseweb="tag"] path { color: var(--nm-clay-600) !important; }
[data-testid="stMultiSelect"] .react-aria-ComboBox > div { padding: .35rem .5rem !important; }

/* ============================================================
   14 · SCROLLBAR, SELECCIÓN Y ENLACES
   ============================================================ */
/* Scroll visible: antes la pista era transparente y el pulgar una línea
   casi invisible (16% de tinta recortada con un borde de 3px), así que no
   se notaba que la página —y sobre todo la barra lateral compacto— se podía
   seguir bajando. Ahora la pista se insinúa siempre, el pulgar es más grueso
   y con más contraste, y al pasar el cursor o arrastrarlo se tiñe de barro.
   Firefox no usa los pseudo-elementos -webkit-: se le da el mismo tratamiento
   con scrollbar-width/scrollbar-color (delgada, pero siempre presente). */
html {
  scrollbar-width: thin;
  scrollbar-color: rgba(180, 85, 45, .55) rgba(38, 35, 31, .06);
}
[data-testid="stSidebar"],
[data-testid="stSidebarContent"],
[data-testid="stSidebarUserContent"],
[data-testid="stDataFrame"],
[data-testid="stDataEditor"] {
  scrollbar-width: thin;
  scrollbar-color: rgba(180, 85, 45, .55) rgba(38, 35, 31, .06);
}
::-webkit-scrollbar { width: 12px; height: 12px; }
::-webkit-scrollbar-track {
  background: rgba(38, 35, 31, .06);
  border-radius: var(--nm-r-pill);
}
::-webkit-scrollbar-thumb {
  background: rgba(38, 35, 31, .32);
  border: 2px solid transparent; background-clip: content-box;
  border-radius: var(--nm-r-pill);
}
::-webkit-scrollbar-thumb:hover { background: rgba(180, 85, 45, .65); background-clip: content-box; }
::-webkit-scrollbar-thumb:active { background: rgba(154, 69, 38, .8); background-clip: content-box; }
::-webkit-scrollbar-corner { background: transparent; }
/* En la barra lateral el pulgar va un punto más presente: es el scroll que
   más pasaba desapercibido cuando el bloque de usuario quedaba abajo. */
[data-testid="stSidebar"]::-webkit-scrollbar,
[data-testid="stSidebarContent"]::-webkit-scrollbar { width: 10px; }
[data-testid="stSidebar"]::-webkit-scrollbar-thumb,
[data-testid="stSidebarContent"]::-webkit-scrollbar-thumb {
  background: rgba(180, 85, 45, .5);
  border: 2px solid transparent; background-clip: content-box;
}
::selection { background: var(--nm-clay-100); color: var(--nm-ink); }
[data-testid="stFullScreenFrame"] > div { border-radius: var(--nm-r) !important; }

/* ============================================================
   15 · TARJETAS DE CONTENEDOR (acceso y pie)
   ============================================================ */
.st-key-nm_acceso {
  max-width: 540px !important;
  margin: 0 auto !important;
  background: var(--nm-card) !important;
  border: 1px solid var(--nm-line);
  border-radius: var(--nm-r-xl) !important;
  box-shadow: var(--nm-shadow-lg);
  padding: 2rem 2.1rem !important;
}
.st-key-nm_acceso h3 { font-size: 1.32rem !important; }
.st-key-nm_acceso [data-testid="stForm"] {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 !important;
}
.st-key-nm_pie {
  margin-top: 2.4rem !important;
  padding-top: 1rem !important;
  border-top: 1px solid var(--nm-line);
}
.st-key-nm_pie p { font-size: .76rem !important; color: var(--nm-muted) !important; }

/* ============================================================
   16 · VISTA ADAPTABLE (móvil y tableta)
   ============================================================ */
@media (max-width: 1200px) {
  .nm-hero { padding: 1.6rem 1.7rem; }
  .nm-hero__media { flex-basis: 320px; }
}
@media (max-width: 768px) {
  [data-testid="stMainBlockContainer"] { padding-top: 1.6rem; padding-left: 1rem; padding-right: 1rem; }
  .nm-hero { padding: 1.4rem 1.3rem; border-radius: var(--nm-r-lg); }
  .nm-hero__media { flex-basis: 100%; }
  .nm-hero__media img { border-radius: var(--nm-r); }
  .nm-head { flex-direction: column; align-items: flex-start; gap: .6rem; }
  .nm-foot { flex-direction: column; align-items: flex-start; gap: .35rem; }
  .nm-kpi__value { font-size: 1.6rem; }
  [data-testid="stTabs"] [role="tablist"] { width: 100%; }
  [data-testid="stRadioGroup"] { flex-direction: column; align-items: stretch; }
  [data-testid="stRadioGroup"] > div > label[data-testid="stRadioOption"] { border-radius: var(--nm-r-sm); }
  /* Las columnas de Streamlit no se apilan solas: se fuerzan dos por fila. */
  [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap !important;
    row-gap: .6rem !important;
  }
  [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex: 1 1 46% !important;
    min-width: 46% !important;
  }
  /* El hero sí se apila completo: texto arriba, imagen abajo. */
  .st-key-nm_hero [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex: 1 1 100% !important;
    min-width: 100% !important;
  }
  .st-key-nm_hero [data-testid="stImage"] img { max-height: 220px; object-fit: contain; }
}
"""


# ==========================================
# COMPONENTES EN HTML
# ==========================================
def escapar(texto):
    """Escapa un texto para poder incrustarlo en HTML."""
    return html.escape(str(texto), quote=True)


def aplicar():
    """Inyecta la hoja de estilo del tema en la página."""
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def chip(texto, tono="barro"):
    """Etiqueta tipo pastilla (estado, rol, aviso)."""
    clase = "" if tono == "barro" else f" nm-chip--{escapar(tono)}"
    st.markdown(f"<span class='nm-chip{clase}'>{escapar(texto)}</span>", unsafe_allow_html=True)


def encabezado(etiqueta, titulo, descripcion=None, chip_texto=None, chip_tono="barro"):
    """Encabezado de vista: micro-etiqueta, título y descripción."""
    chip_html = ""
    if chip_texto:
        clase = "" if chip_tono == "barro" else f" nm-chip--{escapar(chip_tono)}"
        chip_html = f"<span class='nm-chip{clase}' style='margin-left:.6rem'>{escapar(chip_texto)}</span>"
    desc_html = f"<p class='nm-head__sub'>{escapar(descripcion)}</p>" if descripcion else ""
    st.markdown(
        f"""
        <div class="nm-head">
          <div>
            <div class="nm-head__eyebrow">{escapar(etiqueta)}</div>
            <h2 class="nm-head__title">{escapar(titulo)}{chip_html}</h2>
            {desc_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi(valor, etiqueta, icono=None, tono="barro", nota=None):
    """Tarjeta de indicador: etiqueta, valor grande y nota al pie."""
    clase = "" if tono == "barro" else f" nm-kpi--{escapar(tono)}"
    icono_html = f"<span class='nm-kpi__chip'>{escapar(icono)}</span>" if icono else ""
    nota_html = f"<div class='nm-kpi__note'>{escapar(nota)}</div>" if nota else ""
    st.markdown(
        f"""
        <div class="nm-kpi{clase}">
          <div class="nm-kpi__top">
            <span class="nm-kpi__label">{escapar(etiqueta)}</span>
            {icono_html}
          </div>
          <div class="nm-kpi__value">{escapar(valor)}</div>
          {nota_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def panel(titulo, texto=None, cuerpo=None):
    """Tarjeta de contenido con título (el cuerpo es HTML ya escapado por el llamador)."""
    texto_html = f"<p class='nm-panel__text'>{escapar(texto)}</p>" if texto else ""
    st.markdown(
        f"""
        <div class="nm-panel">
          <div class="nm-panel__title">{escapar(titulo)}</div>
          {texto_html}
          {cuerpo or ""}
        </div>
        """,
        unsafe_allow_html=True,
    )
