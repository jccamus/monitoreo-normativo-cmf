"""Genera docs/index.html implementando los requisitos del brief
'Propuesta - Cambios Normativos.txt' para periodistas que monitorean la CMF.

Estructura en cuatro pestañas:
- **Agenda de tareas**: un calendario horizontal de ±6 meses alrededor de hoy,
  donde cada tarjeta es una fecha en que algo entra a regir. Los meses sin
  nada agendado se apilan en un mazo. Arriba, tres paneles —cuerpo normativo,
  proyectos por mes y las obligaciones sin fecha que el eje no puede
  mostrar— y un buscador sobre todo el corpus.
- **Cambios relevantes**: agrupados por cuerpo normativo (RAN, CNC, MSI…),
  recortado a los últimos 5 años.
- **Revisión manual**: los cambios de archivo del MSI sin fecha de vigencia
  determinable. Rinde contenido aunque esté vacío — que no haya pendientes
  es información, y un panel en blanco se lee como si algo hubiera fallado.
- **Listado completo**: stats, filtros por tipo de acuerdo, búsqueda libre,
  tabla con detalle expandible (descripción, RAN, MSI, archivos, modifica
  por sección) y línea de tiempo por NCG afectada.
"""
import html
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import diff
import revisiones
import store

logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"
DOCS_DIR = Path(__file__).parent.parent / "docs"
OUTPUT = DOCS_DIR / "index.html"

# CARACTER sin tilde y el N° opcional: las descripciones del listado vienen en
# mayúsculas sin acentuar y alternan "NCG N°306" con "NCG 306".
_NCG_NUM_DESC = re.compile(
    r"NORMAS?\s+DE\s+CAR[ÁA]CTER\s+GENERAL\s*(?:N[°o]\s*)?(\d+)", re.IGNORECASE
)
_NCG_NUM_SHORT = re.compile(r"\bNCG\s*(?:N[°o]\s*)?(\d+)", re.IGNORECASE)
_DEROGA_RE = re.compile(r"\b(DEROGA|DERÓGASE|DEROGACIÓN)\b", re.IGNORECASE)

# ── Clasificación por cuerpo normativo (tab "Cambios relevantes") ────────
# Cada grupo: (clave_corta, título_largo, regex) en orden de presentación.
# Una entrada puede caer en múltiples grupos (la Cir 2370 modifica RAN,
# MSI Bancos y CNC simultáneamente). El orden afecta solo presentación.
_RX_RAN = re.compile(
    r'\b(RAN|RECOPILACI[ÓO]N\s+ACTUALIZADA(\s+DE\s+NORMAS)?)\b', re.IGNORECASE)
_RX_MSI_PREFIJO = re.compile(
    r'\b(MSI|MANUAL\s+(?:DEL?\s+)?SISTEMA(?:S)?\s+DE\s+INFORMACI[ÓO]N)\b',
    re.IGNORECASE)
_RX_MSI_BANCOS = re.compile(
    r'(?:MSI|MANUAL\s+(?:DEL?\s+)?SISTEMA(?:S)?\s+DE\s+INFORMACI[ÓO]N)\s*'
    r'(?:NORMATIVO\s+)?(?:PARA\s+|DE\s+)?BANCOS?', re.IGNORECASE)
_RX_MSI_FONDOS = re.compile(
    r'(?:MSI|MANUAL\s+(?:DEL?\s+)?SISTEMA(?:S)?\s+DE\s+INFORMACI[ÓO]N)\s*'
    r'(?:NORMATIVO\s+)?(?:PARA\s+|DE\s+)?FONDOS?', re.IGNORECASE)
_RX_MSI_REDEC = re.compile(r'\bMSI\s+REDEC\b|\bREDEC\b', re.IGNORECASE)
_RX_CONTEXTO_BANCOS = re.compile(
    r'\b(BANCOS?|RAN|RECOPILACI[ÓO]N\s+ACTUALIZADA|'
    r'COMPENDIO\s+DE\s+NORMAS\s+CONTABLES)\b', re.IGNORECASE)
_RX_CNC = re.compile(r'COMPENDIO\s+DE\s+NORMAS\s+CONTABLES', re.IGNORECASE)
_RX_COMPENDIO_PENSIONES = re.compile(
    r'COMPENDIO\s+DE\s+NORMAS\s+DEL\s+SISTEMA\s+DE\s+PENSIONES', re.IGNORECASE)
_RX_FINTEC = re.compile(
    r'\bFINTEC\b|LEY\s+FINTEC|FINANZAS\s+ABIERTAS', re.IGNORECASE)
_RX_SEGUROS = re.compile(
    r'\b(SEGURO(S)?|P[ÓO]LIZA(S)?|REASEGURO(S)?|ASEGURAD(OR(A|ES|AS)?|O|A)|'
    r'CORREDOR(ES)?\s+DE\s+SEGUROS|RENTA(S)?\s+VITALICIA(S)?|SOAP)\b',
    re.IGNORECASE)
_RX_AGF = re.compile(
    r'\b(AGF(S)?|ADMINISTRADORAS?\s+GENERAL(ES)?\s+DE\s+FONDOS|'
    r'ADMINISTRADORAS?\s+DE\s+FONDOS\s+MUTUOS|'
    r'ADMINISTRADORAS?\s+DE\s+FONDOS\s+DE\s+INVERSI[ÓO]N|'
    r'FONDOS?\s+MUTUOS?|FONDOS?\s+DE\s+INVERSI[ÓO]N)\b', re.IGNORECASE)
_RX_VALORES = re.compile(r'\bVALORES\b', re.IGNORECASE)

# Grupos en orden de presentación. (clave, título, descripción)
GRUPOS_CUERPO_NORMATIVO = [
    ("ran", "RAN", "Recopilación Actualizada de Normas de Bancos"),
    ("msi-bancos", "MSI Bancos", "Manual de Sistemas de Información — Bancos"),
    ("msi-fondos", "MSI Fondos", "Manual de Sistemas de Información — Fondos"),
    ("msi-redec", "MSI Redec", "Manual de Sistemas de Información — Redec"),
    ("cnc", "CNC", "Compendio de Normas Contables"),
    ("compendio-pensiones", "Compendio Pensiones",
     "Compendio de Normas del Sistema de Pensiones"),
    ("fintec", "Fintec", "Ley Fintec, Manual MSI Fintec y Finanzas Abiertas"),
    ("seguros", "Seguros",
     "Pólizas, reaseguros, aseguradoras, corredores, rentas vitalicias y SOAP"),
    ("agf", "AGF",
     "Administradoras Generales de Fondos, fondos mutuos y de inversión"),
    ("valores", "Valores",
     "Mercado de valores: intermediarios, registros, bolsas y emisores"),
    ("otros", "Otros",
     "Cambios normativos sin un cuerpo específico identificable"),
]


def _grupos_de_entrada(entrada: dict) -> list[str]:
    """Devuelve las claves de grupos a los que pertenece la entrada.

    Una entrada puede pertenecer a varios grupos si modifica más de un
    cuerpo normativo. Si no matchea ninguno, cae en 'otros'.
    """
    txt = " ".join(filter(None, [
        entrada.get("tema", "") or "",
        entrada.get("descripcion_cmf", "") or "",
    ]))
    asignados: list[str] = []
    if _RX_RAN.search(txt):
        asignados.append("ran")
    # MSI: clasificación por sub-grupo, con fallback a Bancos por contexto
    if _RX_MSI_PREFIJO.search(txt):
        sub = []
        if _RX_MSI_BANCOS.search(txt):
            sub.append("msi-bancos")
        if _RX_MSI_FONDOS.search(txt):
            sub.append("msi-fondos")
        if _RX_MSI_REDEC.search(txt):
            sub.append("msi-redec")
        if not sub and _RX_FINTEC.search(txt):
            pass  # va a Fintec, no a un sub-MSI
        elif not sub and _RX_CONTEXTO_BANCOS.search(txt):
            sub.append("msi-bancos")
        asignados.extend(sub)
    if _RX_CNC.search(txt):
        asignados.append("cnc")
    if _RX_COMPENDIO_PENSIONES.search(txt):
        asignados.append("compendio-pensiones")
    if _RX_FINTEC.search(txt):
        asignados.append("fintec")
    if _RX_SEGUROS.search(txt):
        asignados.append("seguros")
    if _RX_AGF.search(txt):
        asignados.append("agf")
    if _RX_VALORES.search(txt):
        asignados.append("valores")
    if not asignados:
        asignados.append("otros")
    return asignados


def _agrupar_por_cuerpo(entradas: list[dict]) -> dict[str, list[dict]]:
    """Agrupa las entradas por cuerpo normativo según _grupos_de_entrada."""
    grupos: dict[str, list[dict]] = {clave: [] for clave, _, _ in GRUPOS_CUERPO_NORMATIVO}
    for e in entradas:
        for g in _grupos_de_entrada(e):
            grupos[g].append(e)
    # Ordenar cada grupo por fecha desc
    for clave in grupos:
        grupos[clave].sort(
            key=lambda x: (x.get("fecha") or "", x.get("clave") or ""),
            reverse=True,
        )
    return grupos

# Un botón sin ninguna fila detrás sólo puede vaciar la tabla, así que
# `_render_filtros` no rinde los que dan cero. Es data-driven a propósito: la
# alternativa —borrar la categoría— deja el caso mudo si la CMF publica uno,
# y acá el botón reaparece solo cuando aparece el primer dato.
#
# «Prórroga Consulta Pública» sí se eliminó del todo, a pedido: postergaba el
# plazo de una consulta pública y no hay ni un caso en 607 resoluciones. No
# confundirla con «Postergación de vigencia», que son otra cosa y sí existen.
#
# Ojo con «Derogación»: es la única categoría de esta lista que NO tiene patrón
# en `store.TIPO_ACUERDO_MAP`. Se genera acá, en `_tipos_de_entrada`, a partir de
# `_accion_sobre_norma` («Derogada por») y de `_es_derogacion` sobre la
# descripción. Buscar su patrón en `store.py` no lleva a ninguna parte, y es la
# segunda categoría más poblada del histórico.
TIPOS_FILTRO = [
    ("todos", "Todos"),
    ("Consulta Pública", "Consulta Pública"),
    ("Postergación de vigencia", "Postergación de vigencia"),
    ("Modificación NCG", "Modificación NCG"),
    ("Nueva Normativa", "Nueva Normativa"),
    ("Circular", "Circular"),
    ("Derogación", "Derogación"),
]


# ── Carga ────────────────────────────────────────────────────────────────

def _cargar_diferenciales() -> list[dict]:
    diff = []
    for path in sorted(DAILY_DIR.glob("*.json"), reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                diff.append(json.load(f))
        except Exception as e:
            logger.warning("Error leyendo %s: %s", path, e)
    return diff


def _flatten_entradas(diferenciales: list[dict]) -> list[dict]:
    entradas: list[dict] = []
    for d in diferenciales:
        entradas.extend(d.get("new_entries", []))
    return entradas


# ── Helpers de dominio ──────────────────────────────────────────────────

def _es_derogacion(descripcion: str) -> bool:
    return bool(_DEROGA_RE.search(descripcion or ""))


def _normas_afectadas(entrada: dict) -> list[str]:
    """NCGs afectadas combinando modifica[], campo ncg y regex de descripción."""
    nums: set[int] = set()
    for m in entrada.get("modifica", []) or []:
        # Las entradas con fuente "descripcion_cmf" que hay guardadas en
        # data/daily/ se generaron con un regex que capturaba cualquier "N° x"
        # de la descripción y lo rotulaba NCG, así que traen números de
        # circulares, leyes y decretos disfrazados de norma. Se ignoran y el
        # número se vuelve a sacar de la descripción más abajo, con los dos
        # patrones que sí exigen la designación de NCG. Se corrige acá y no
        # sólo en `store` porque el arreglo del store únicamente alcanza a lo
        # que entre de aquí en adelante: la descripción viaja dentro de la
        # entrada, así que el histórico se repara al renderizar, sin reparse.
        if m.get("fuente") == "descripcion_cmf":
            continue
        n = m.get("numero_norma")
        if isinstance(n, int):
            nums.add(n)
    if isinstance(entrada.get("ncg"), int):
        nums.add(entrada["ncg"])
    desc = entrada.get("descripcion_cmf", "") or ""
    for m in _NCG_NUM_DESC.findall(desc):
        nums.add(int(m))
    for m in _NCG_NUM_SHORT.findall(desc):
        nums.add(int(m))

    # Una NCG no se modifica a sí misma. `ncg` y la descripción traen el número
    # propio del documento cuando el documento *es* una NCG, y aparecía listado
    # entre las normas afectadas: "NCG N°568 → afecta a NCG N°538, NCG N°568".
    # Sólo se descarta cuando el documento es una NCG: en un oficio circular que
    # modifica la NCG N°530, ese 530 sí es una norma afectada.
    doc = entrada.get("documento") or {}
    if doc.get("tipo") == "NCG" and isinstance(doc.get("numero"), int):
        nums.discard(doc["numero"])

    return [f"NCG N°{n}" for n in sorted(nums)]


_LABEL_INICIO = {
    "inmediata": "Inmediata",
    "cierre_mes_siguiente": "Cierre mes siguiente",
    "no especificado": "—",
    "ver texto": "Ver documento",
}


_MESES_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _fmt_inicio(v: dict) -> str:
    """Etiqueta legible de `inicio`, respetando la precisión declarada.

    Un plazo que el documento fija sólo por mes se guarda como el día 1 para
    poder ordenarlo, pero mostrarlo como "2024-12-01" afirma una precisión que
    el documento no da. Se rotula como el mes.
    """
    inicio = v.get("inicio") or "—"
    if v.get("precision") == "mes" and isinstance(inicio, str) and len(inicio) == 10:
        try:
            mes = _MESES_ES[int(inicio[5:7]) - 1]
        except (ValueError, IndexError):
            return _LABEL_INICIO.get(inicio, inicio)
        return f"{mes} de {inicio[:4]}"
    return _LABEL_INICIO.get(inicio, inicio)


def _vigencia_fmt(v: dict | None) -> str:
    if not v:
        return "—"
    label = _fmt_inicio(v)
    # Una fecha puesta por una persona nunca se presenta igual que una extraída
    # del PDF: es el mismo criterio con que se marcan los plazos por mes y las
    # fechas candidatas.
    if v.get("fuente") == "revision_manual":
        label += " · confirmada"
    elif v.get("calculo"):
        label += " · calculada"
    plazos = v.get("plazos") or []
    if plazos:
        # El `inicio` global de un documento escalonado describe sólo el primer
        # tramo; sin esta marca la celda diría "inmediata" y ocultaría que hay
        # otra fecha por cumplir.
        label = f"{label} · {len(plazos)} plazos"
    plazo = v.get("plazo_transicion")
    return f"{label} · transición hasta {plazo}" if plazo else label


def _render_plazos(v: dict | None) -> str:
    """Bloque de detalle con los plazos escalonados de un documento."""
    plazos = (v or {}).get("plazos") or []
    if not plazos:
        return ""
    items = "".join(
        f'<li><b>{html.escape(_fmt_inicio(p))}</b>'
        f' · {html.escape(p.get("texto") or "")}</li>'
        for p in plazos
    )
    return (
        f'<div class="d-bloque"><span class="d-label">Plazos de entrada en vigencia</span>'
        f'<ul>{items}</ul></div>'
    )


def _stats(entradas: list[dict]) -> dict[str, int]:
    """Cuenta por las mismas categorías con que filtra la tabla.

    Contaba sobre `tipo_acuerdo`, que es una sola categoría, mientras los
    botones filtran sobre `_tipos_de_entrada`, que son varias: la píldora del
    Resumen decía «Circular 0» y el botón «Circular» devolvía 2 filas. Un
    conteo que no cuadra con lo que muestra el filtro de al lado se lee como
    que uno de los dos está roto.
    """
    counts: dict[str, int] = {}
    for e in entradas:
        for t in set(_tipos_de_entrada(e)):
            counts[t] = counts.get(t, 0) + 1
    return counts


def _tipos_de_entrada(entrada: dict) -> list[str]:
    """Todas las categorías con las que la fila debe responder a los filtros.

    Toma la lista completa y no sólo el `tipo_acuerdo` guardado, que es un
    string y por tanto una única categoría: un documento que emite una
    circular y además modifica una NCG tiene que aparecer bajo los dos
    filtros, porque bajo ambos criterios es un resultado correcto.

    Y suma lo que dice `_accion_sobre_norma`, que es la misma función con la
    que la línea de tiempo rotula cada evento. Sin eso son **dos mecanismos
    midiendo lo mismo por caminos distintos**, y divergen: la NCG N°209
    mostraba «1 de 7 eventos · 5 la modifican», porque cuatro de esos cinco
    dicen "APRUEBA MODIFICACIONES A LA NORMA DE CARÁCTER GENERAL N°209" y no
    calzaban con el patrón de categoría. Ampliar el patrón arreglaba esos
    cuatro y dejaba otros catorce; derivar ambas cosas del mismo análisis los
    vuelve coherentes por construcción.
    """
    tipos = store.inferir_tipos_acuerdo(entrada.get("descripcion_cmf") or "")
    for norma in _normas_afectadas(entrada):
        m = re.search(r"\d+", norma)
        if not m:
            continue
        accion = _accion_sobre_norma(entrada, int(m.group()))
        if accion == "Modificada por":
            tipos.append("Modificación NCG")
        elif accion == "Derogada por":
            tipos.append("Derogación")
    if _es_derogacion(entrada.get("descripcion_cmf", "")):
        tipos.append("Derogación")
    # "Otro" es el centinela de "ninguna categoría calzó": deja de aplicar en
    # cuanto una calza, y si se queda infla su conteo y contradice al resto.
    reales = [t for t in dict.fromkeys(tipos) if t != "Otro"]
    return reales or ["Otro"]


def _parse_iso(s: str | None) -> datetime | None:
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _fechas_futuras(entrada: dict, hoy: datetime) -> list[datetime]:
    """Vigencias (entrada y modifica[]) cuya fecha cae en el futuro respecto a hoy."""
    fechas: list[datetime] = []
    fuentes: list[dict] = [entrada.get("vigencia") or {}]
    fuentes.extend(m.get("vigencia") or {} for m in (entrada.get("modifica") or []))
    # Los plazos por viñeta cuentan como fechas propias: un documento con
    # aplicación inmediata para unos capítulos y una fecha futura para otros
    # tiene que aparecer en la Agenda de tareas por esa fecha futura, aunque su
    # `inicio` global diga "inmediata".
    fuentes.extend(p for v in list(fuentes) for p in (v.get("plazos") or []))
    for v in fuentes:
        for k in ("inicio", "plazo_transicion"):
            d = _parse_iso(v.get(k))
            if d and d >= hoy:
                fechas.append(d)
    return fechas


def _fuentes_vigencia(entrada: dict) -> list[dict]:
    """Todos los dicts de vigencia de una entrada: propia, modifica[] y plazos."""
    fuentes: list[dict] = [entrada.get("vigencia") or {}]
    fuentes.extend(m.get("vigencia") or {} for m in (entrada.get("modifica") or []))
    fuentes.extend(p for v in list(fuentes) for p in (v.get("plazos") or []))
    return fuentes


def _fechas_vigencia(entrada: dict) -> list[tuple[datetime, bool]]:
    """Fechas en que algo de esta entrada entra a regir, con marca de inmediatez.

    Una vigencia "inmediata" no trae fecha propia: rige desde la publicación,
    así que se fecha con la del documento. Sin esta equivalencia, todo lo que
    aplica de inmediato —lo más urgente— queda fuera de cualquier vista con eje
    temporal.
    """
    publicacion = _parse_iso(entrada.get("fecha"))
    fechas: list[tuple[datetime, bool]] = []
    for v in _fuentes_vigencia(entrada):
        if v.get("inicio") == "inmediata":
            if publicacion:
                fechas.append((publicacion, True))
            continue
        for k in ("inicio", "plazo_transicion"):
            d = _parse_iso(v.get(k))
            if d:
                fechas.append((d, False))
    return fechas




# ── Agenda: el calendario de vigencias ───────────────────────────────────
#
# Reemplaza las tres columnas por plazo (≤30 / 31–60 / 61+ días) por un eje
# temporal continuo que cruza el día de hoy. Las columnas por plazo obligaban
# a leer «faltan 47 días» y traducirlo mentalmente a un mes; el eje lo dice
# directo, y además deja ver los meses vacíos, que también son información.

MESES_AGENDA = 6   # meses hacia atrás y hacia adelante que cubre el riel


def _indice_mes(dt: datetime) -> int:
    """Mes como entero absoluto, para poder restar meses sin pelear con años."""
    return dt.year * 12 + dt.month - 1


def _clave_mes(indice: int) -> str:
    return f"{indice // 12:04d}-{indice % 12 + 1:02d}"


def _precision_de(entrada: dict, iso: str) -> str:
    """La precisión con que el documento declaró esa fecha: 'dia' o 'mes'.

    Una fecha de precisión mensual se guarda normalizada al día 1 para poder
    ordenarla. Mostrarla como «1 de diciembre» afirmaría una exactitud que el
    documento no da, así que la precisión tiene que viajar hasta el render.
    """
    for v in _fuentes_vigencia(entrada):
        for k in ("inicio", "plazo_transicion"):
            if v.get(k) == iso:
                return v.get("precision") or "dia"
    return "dia"


def _fuente_vigencia(entrada: dict) -> str:
    """De dónde salió la fecha, para poder mostrarlo junto al dato.

    No son equivalentes: `seccion` la declara el PDF, `clausula_aplicacion` es
    un respaldo más frágil y `revision_manual` la puso una persona que leyó el
    documento. Presentarlas iguales sería repetir el error que originó los bugs
    de vigencia de este proyecto.
    """
    vig = entrada.get("vigencia") or {}
    fuente = vig.get("fuente")
    if fuente in ("revision_manual", "clausula_aplicacion"):
        return fuente
    # Una anotación manual manda sobre el cálculo; fuera de eso, `calculo`
    # marca las fechas que el PDF declara como regla y no como fecha escrita.
    if any(v.get("calculo") for v in _fuentes_vigencia(entrada)):
        return "calculada"
    return "seccion"


def _calculo_de(entrada: dict) -> dict | None:
    """El rastro del cálculo, si la fecha de esta entrada se derivó de un plazo."""
    for v in _fuentes_vigencia(entrada):
        if v.get("calculo"):
            return v["calculo"]
    return None


def _hitos_agenda(entradas: list[dict], hoy: datetime) -> list[dict]:
    """Un hito por cada (documento, mes en que algo suyo entra a regir).

    El eje son fechas de vigencia, no documentos: un documento con dos plazos
    en meses distintos son dos obligaciones y aparece dos veces. Dentro de un
    mismo mes se muestra una sola vez: son la misma obligación vista dos veces.
    """
    con_timeline = set(_agrupar_por_norma(entradas).keys())
    hitos: list[dict] = []
    for e in entradas:
        afectadas = [n for n in _normas_afectadas(e) if n in con_timeline]
        vistos: set[str] = set()
        for fecha, inmediata in _fechas_vigencia(e):
            mes = fecha.strftime("%Y-%m")
            if mes in vistos:
                continue
            vistos.add(mes)
            iso = fecha.strftime("%Y-%m-%d")
            hito = dict(e)
            hito["_fecha_aplicacion"] = iso
            hito["_mes"] = mes
            hito["_indice_mes"] = _indice_mes(fecha)
            hito["_inmediata"] = inmediata
            # Una vigencia inmediata se fecha con la publicación, así que su
            # precisión es la del documento y no la de un plazo declarado.
            hito["_precision"] = "dia" if inmediata else _precision_de(e, iso)
            hito["_vencida"] = fecha <= hoy
            hito["_fuente_vigencia"] = _fuente_vigencia(e)
            hito["_afectadas"] = afectadas[:3]
            hitos.append(hito)
    return hitos


def _calendario_agenda(
    hitos: list[dict], hoy: datetime, meses: int = MESES_AGENDA
) -> list[dict]:
    """La ventana del riel: un tramo por mes, del más antiguo al más futuro."""
    ancla = _indice_mes(hoy)
    salida = []
    for i in range(ancla - meses, ancla + meses + 1):
        items = sorted(
            (h for h in hitos if h["_indice_mes"] == i),
            key=lambda h: h["_fecha_aplicacion"],
        )
        salida.append({"mes": _clave_mes(i), "offset": i - ancla, "items": items})
    return salida


def _hitos_lejanos(
    hitos: list[dict], hoy: datetime, meses: int = MESES_AGENDA
) -> list[dict]:
    """Lo agendado más allá del borde derecho del riel."""
    corte = _indice_mes(hoy) + meses
    return sorted(
        (h for h in hitos if h["_indice_mes"] > corte),
        key=lambda h: h["_fecha_aplicacion"],
    )


def _sin_fecha_agenda(entradas: list[dict]) -> list[dict]:
    """Obligaciones que el calendario no puede mostrar porque no tienen cuándo.

    Es el punto ciego de una vista con eje temporal, y por eso se declara en
    vez de omitirse: un cambio a un archivo del MSI genera obligación de
    reporte, y un plazo relativo («120 días después de su emisión») también es
    trabajo comprometido. Dejarlos fuera en silencio haría que el calendario
    mintiera por omisión, que es exactamente el modo de falla que este proyecto
    ya pagó caro con las fechas inventadas.
    """
    salida = []
    for e in entradas:
        vig = e.get("vigencia") or {}
        por_archivo = _requiere_revision(e)
        relativo = vig.get("inicio") == "ver texto"
        if not (por_archivo or relativo):
            continue
        item = dict(e)
        item["_motivo"] = "archivo" if por_archivo else "relativo"
        salida.append(item)
    salida.sort(key=lambda e: e.get("fecha") or "", reverse=True)
    return salida


def _situacion(e: dict, hoy: datetime) -> str:
    """En qué estado está una entrada respecto del eje temporal."""
    if _fechas_futuras(e, hoy):
        return "futuro"
    if _fechas_vigencia(e):
        return "pasado"
    vig = e.get("vigencia") or {}
    if _requiere_revision(e) or vig.get("inicio") == "ver texto":
        return "sinfecha"
    return "sinvigencia"


def _indice_busqueda(entradas: list[dict], hoy: datetime) -> list[dict]:
    """Índice del buscador: TODO el corpus, no sólo la ventana del riel.

    Si el buscador mirara únicamente los 13 meses del calendario, responder
    «no hay cambios normativos en relación con el archivo consultado» sería
    falso para cualquier cosa fuera de la ventana — que es donde está la
    inmensa mayoría de los documentos.
    """
    indice = []
    for e in entradas:
        fechas = _fechas_vigencia(e)
        vig = e.get("vigencia") or {}
        indice.append({
            "c": e.get("clave") or "",
            "n": _etiqueta_documento(e),
            "f": e.get("fecha") or "",
            "d": " ".join((e.get("tema") or e.get("descripcion_cmf") or "").split())[:110],
            "g": [t for c, t, _ in GRUPOS_CUERPO_NORMATIVO if c in _grupos_de_entrada(e)],
            # El código del archivo vive en `nombre`, no en `codigo`.
            "a": [a.get("nombre") for a in (e.get("archivos_afectados") or []) if a.get("nombre")],
            "m": _normas_afectadas(e)[:4],
            "v": fechas[0][0].strftime("%Y-%m-%d") if fechas else (vig.get("inicio") or ""),
            "u": e.get("url_documento") or "",
            "s": _situacion(e, hoy),
        })
    return indice


# Una norma tocada una sola vez no tiene línea de tiempo que mostrar: es un
# evento suelto, y ese evento ya está en la tabla de arriba con todo su detalle.
# Sobre el histórico son 69 de 92 grupos, y son los que hacían que la sección se
# leyera como un listado. Acá quedan las 23 normas con historia de verdad.
_TIMELINE_MIN_EVENTOS = 2


def _agrupar_por_norma(entradas: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for e in entradas:
        for norma in _normas_afectadas(e):
            grupos.setdefault(norma, []).append(e)
    grupos = {
        norma: items
        for norma, items in grupos.items()
        if len(items) >= _TIMELINE_MIN_EVENTOS
    }
    for norma in grupos:
        grupos[norma].sort(key=lambda x: x.get("fecha") or "")

    def _key(item):
        # Primero las normas con más eventos: el sentido de una línea de tiempo
        # es mostrar cuáles se han tocado repetidamente y cuándo. Ordenada por
        # número ascendente abría en la NCG N°1 con un evento suelto, que es
        # justo el caso donde no hay ninguna línea que ver. A igualdad de
        # eventos, por número, para que el orden sea estable.
        m = re.search(r"\d+", item[0])
        return (-len(item[1]), int(m.group()) if m else 9999)

    return dict(sorted(grupos.items(), key=_key))


# ── Punto de entrada ────────────────────────────────────────────────────

def generar_html() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    diferenciales = _cargar_diferenciales()
    entradas = _flatten_entradas(diferenciales)

    # `tipo_acuerdo` se recalcula sobre lo ya guardado. Está grabado en cada
    # JSON de data/daily/, así que el arreglo del calce en `store` sólo
    # alcanzaría a lo que entre de aquí en adelante y el histórico quedaría
    # con la clasificación vieja. La descripción viaja dentro de la entrada,
    # de modo que se reclasifica acá y no hace falta reparsear. Se llama a la
    # misma función de `store` a propósito: una sola fuente de verdad, para
    # que lo que muestra el dashboard no pueda divergir de lo que se guarda.
    reclasificadas = 0
    for e in entradas:
        nuevo = store.inferir_tipo_acuerdo(e.get("descripcion_cmf") or "")
        if nuevo != e.get("tipo_acuerdo"):
            e["tipo_acuerdo"] = nuevo
            reclasificadas += 1
    if reclasificadas:
        logger.info(
            "tipo_acuerdo recalculado en %d de %d entradas guardadas",
            reclasificadas, len(entradas),
        )

    # Las anotaciones manuales se aplican acá, antes de clasificar: al
    # renderizar y no al guardar, para que los datos parseados queden intactos
    # y una anotación nueva sólo requiera regenerar el HTML.
    revisiones.aplicar(entradas, revisiones.cargar())
    for e in revisiones.discrepancias(entradas):
        logger.warning(
            "Anotación manual de %s (%s) discrepa del parser, que ahora propone %s "
            "— revisar si la anotación todavía hace falta",
            e.get("clave"), (e.get("vigencia") or {}).get("inicio"),
            (e.get("vigencia") or {}).get("discrepa"),
        )

    # `hoy` es el día del render, truncado a medianoche porque toda la
    # aritmética de la agenda cuenta días enteros. Es el ancla del calendario y
    # tiene que ser el día de verdad —dónde estamos parados—, distinto de
    # `ultima_consulta`, que es cuándo se habló con la CMF.
    hoy = datetime.now(timezone.utc).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0
    )

    # «Última actualización» responde «¿esto está al día?», así que es cuándo se
    # consultó la CMF y no cuándo se armó este HTML. Los dos difieren cada vez
    # que se regenera la página sin tocar la fuente —`python scraper/dashboard.py`,
    # el paso redundante del workflow, cualquier cambio de diseño—, y usar la
    # hora del render hacía que la página se declarara recién actualizada
    # habiendo hablado con la CMF por última vez semanas antes.
    #
    # Lo escribe `diff.registrar_consulta()` desde `main.py`. Si falta —state.json
    # de antes de que esto existiera— se cae al `generated_at` del diferencial
    # más reciente, que es la mejor cota inferior disponible: se sabe que ese día
    # sí hubo consulta. La primera corrida del pipeline lo deja exacto.
    ultima_consulta = diff.ultima_consulta() or (
        diferenciales[0].get("generated_at") if diferenciales else None
    )

    # Las novedades son las del archivo diario más reciente (`_cargar_dife-
    # renciales` ordena descendente). Alimentan el resaltado de filas nuevas
    # de la tabla, que hasta ahora recibía una lista vacía fija: el cálculo
    # estaba escrito y funcionando, pero nunca se le pasaba nada.
    novedades = diferenciales[0].get("new_entries", []) if diferenciales else []

    grupos = _agrupar_por_norma(entradas)
    grupos_cuerpo = _agrupar_por_cuerpo(entradas)
    html_doc = _render(
        entradas, grupos, grupos_cuerpo, hoy, ultima_consulta, novedades,
    )
    # El salto se fija en vez de dejar el default, que en Windows traduce cada
    # "\n" a "\r\n". El HTML lleva CRLF dentro del propio contenido —hay
    # descripciones de la CMF que los traen, y viajan al índice de búsqueda—,
    # así que esa traducción los convertía en "\r\r\n": el archivo generado en
    # Windows no era el mismo que el del runner Linux, y cada regeneración local
    # reescribía sus 2.760 líneas. No es cosmético: el contenido se alteraba.
    OUTPUT.write_text(html_doc, encoding="utf-8", newline="\n")

    hitos = _hitos_agenda(entradas, hoy)
    calendario = _calendario_agenda(hitos, hoy)
    en_ventana = sum(len(m["items"]) for m in calendario)
    logger.info(
        "Dashboard generado: %s (%d entradas | agenda: %d hitos en %d de %d meses, "
        "%d más allá de %d meses, %d sin fecha)",
        OUTPUT, len(entradas), en_ventana,
        sum(1 for m in calendario if m["items"]), len(calendario),
        len(_hitos_lejanos(hitos, hoy)), MESES_AGENDA, len(_sin_fecha_agenda(entradas)),
    )



# Todo el pipeline trabaja en UTC, pero la CMF y quien lee esto están en Chile:
# el proceso corre a las 8 de la mañana y un sello sin convertir diría "12:15",
# o sea mediodía. `tzdata` está en requirements.txt porque Windows no trae base
# de zonas horarias y sin ella `ZoneInfo` levanta ZoneInfoNotFoundError.
#
# Si aun así falta, se informa UTC **rotulado como UTC**. Un offset fijo sería
# peor que inútil: Chile cambia de hora, así que acertaría medio año y mentiría
# el otro medio, sin que nada lo delate.
_TZ_PRESENTACION = "America/Santiago"


def _fecha_hora_partes(momento: datetime | str | None) -> tuple[str, str]:
    """`("2026-07-16", "08:15 hrs")`. La hora va aparte para poder rotularla."""
    if isinstance(momento, str):
        # Una fecha pelada no se convierte de zona. `fromisoformat` la lee como
        # medianoche UTC y pasarla a Chile la retrocede al día anterior a las
        # 20:00: el sello terminaba informando un día que no era. Sin hora en el
        # origen no hay hora que mostrar, y la fecha se devuelve tal cual.
        if "T" not in momento and " " not in momento:
            return momento[:10], ""
        try:
            momento = datetime.fromisoformat(momento)
        except ValueError:
            # Un `generated_at` que no parsea igual trae la fecha adelante; se
            # muestra sin hora antes que perder el dato entero.
            return momento[:10], ""
    if momento is None:
        return "", ""
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    try:
        local = momento.astimezone(ZoneInfo(_TZ_PRESENTACION))
        return local.strftime("%Y-%m-%d"), local.strftime("%H:%M hrs")
    except ZoneInfoNotFoundError:
        utc = momento.astimezone(timezone.utc)
        return utc.strftime("%Y-%m-%d"), utc.strftime("%H:%M UTC")


def _fecha_hora(momento: datetime | str | None) -> str:
    fecha, hora = _fecha_hora_partes(momento)
    return f"{fecha}, {hora}" if hora else fecha


# ── Render ───────────────────────────────────────────────────────────────

def _render(
    entradas: list[dict],
    grupos: dict[str, list[dict]],
    grupos_cuerpo: dict[str, list[dict]],
    hoy: datetime,
    ultima_consulta: str | None,
    novedades: list[dict],
) -> str:
    cuadro_html = _render_agenda(
        entradas, hoy, _indice_busqueda(entradas, hoy), ultima_consulta
    )
    relevantes_html = _render_cambios_relevantes(grupos_cuerpo, hoy)
    revision_html = _render_revision_manual(entradas)
    n_revision = sum(1 for e in entradas if _requiere_revision(e))
    # Un solo conteo alimenta las píldoras del Resumen y los botones de filtro,
    # para que no puedan decir cosas distintas.
    counts = _stats(entradas)
    stats_html = _render_stats(counts, len(entradas))
    filtros_html = _render_filtros(counts)
    tabla_html = _render_tabla(entradas, novedades)
    timeline_html = _render_timeline(grupos)

    return (
        _TEMPLATE
        .replace("__CUADRO__", cuadro_html)
        .replace("__RELEVANTES__", relevantes_html)
        .replace("__REVISION__", revision_html)
        .replace(
            "__REVISION_BADGE__",
            f'<span class="tab-badge">{n_revision}</span>' if n_revision else "",
        )
        .replace("__STATS__", stats_html)
        .replace("__FILTROS__", filtros_html)
        .replace("__TABLA__", tabla_html)
        .replace("__TIMELINE__", timeline_html)
        .replace("__ACTUALIZADO__", html.escape(_fecha_hora(ultima_consulta) or "—"))
    )


def _vigencia_resuelta(valor: str | None) -> bool:
    """La vigencia dice algo accionable: una fecha concreta o 'inmediata'."""
    return bool(valor) and (valor == "inmediata" or _parse_iso(valor) is not None)


def _requiere_revision(e: dict) -> bool:
    """Cambia archivos normativos pero no se pudo determinar desde cuándo rige.

    Es el caso que hay que mirar a mano: un cambio a un archivo del MSI crea una
    obligación de reporte, y sin fecha no se sabe para cuándo. Ocurre porque
    parte de los oficios circulares no declaran vigencia en ninguna forma que se
    pueda extraer — no porque el dato se haya perdido.
    """
    archivos = e.get("archivos_afectados") or []
    if not archivos:
        return False
    # Ya lo miró una persona: con fecha asignada o con la constancia de que el
    # documento no la declara. En ninguno de los dos casos sigue pendiente.
    if e.get("_revision"):
        return False
    return not any(_vigencia_resuelta(a.get("vigencia")) for a in archivos)


def _etiqueta_documento(e: dict) -> str:
    """Identifica el documento: 'Circular N°2.364', 'Oficio Circular N°1.375'.

    Sin esto la fila sólo dice qué archivos cambian y desde cuándo no se sabe,
    pero no de qué norma sale, y hay que abrir el PDF para averiguarlo.

    `documento` es la identidad propia del documento y no la de las normas que
    modifica — ver `_identidad_documento` en el parser. El respaldo por nombre
    de archivo existe para las entradas antiguas que se guardaron antes de que
    ese campo existiera y que no hayan pasado por reparse.
    """
    doc = e.get("documento") or {}
    tipo, numero = doc.get("tipo"), doc.get("numero")
    if tipo and numero:
        return f"{tipo} N°{numero:,}".replace(",", ".")

    url = (e.get("url_documento") or "").rsplit("/", 1)[-1]
    m = re.match(r"(ncg|cir|ofc)_(\d+)_", url)
    if m:
        etiquetas = {"ncg": "NCG", "cir": "Circular", "ofc": "Oficio Circular"}
        return f"{etiquetas[m.group(1)]} N°{int(m.group(2)):,}".replace(",", ".")
    return "—"


def _render_candidatas(e: dict) -> str:
    """Fechas del documento que podrían ser la vigencia, como pista de revisión.

    Se muestran con su contexto y rotuladas como candidatas, nunca como la
    vigencia: el pipeline no pudo decidir cuál es, y presentarlas como dato
    firme sería exactamente el error que se corrigió en el parser.
    """
    candidatas = (e.get("vigencia") or {}).get("candidatas") or []
    if not candidatas:
        return ""
    items = "".join(
        f'<li><b>{html.escape(c.get("fecha") or "")}</b> · '
        f'{html.escape(c.get("contexto") or "")}</li>'
        for c in candidatas
    )
    return (
        f'<div class="rv-cand"><span class="rv-cand-lbl">Fechas en el documento '
        f'(sin confirmar cuál rige):</span><ul>{items}</ul></div>'
    )


def _render_revision_manual(entradas: list[dict]) -> str:
    """Panel del tab 'Revisión manual'.

    A diferencia de los otros tabs, éste rinde algo aunque esté vacío: que no
    haya pendientes es información —significa que todo cambio de archivo tiene
    fecha— y un panel en blanco se lee como si algo hubiera fallado.
    """
    pendientes = [e for e in entradas if _requiere_revision(e)]
    if not pendientes:
        return (
            '<section id="revision-manual" class="rv-vacio">'
            '<h2>Revisión manual</h2>'
            '<p class="rv-nota">Ningún cambio de archivo normativo quedó sin fecha '
            'de vigencia. No hay nada que revisar a mano.</p>'
            + _render_revisados(entradas) + '</section>'
        )
    pendientes.sort(key=lambda e: e.get("fecha") or "", reverse=True)
    filas = "".join(
        f'<li><span class="rv-fecha">{html.escape(e.get("fecha") or "—")}</span>'
        f'<span class="rv-doc">{html.escape(_etiqueta_documento(e))}</span>'
        f'<span class="rv-arch">'
        + "".join(
            f'<span class="chip">{html.escape(a.get("nombre",""))}</span>'
            for a in (e.get("archivos_afectados") or [])
        )
        + f'</span><span class="rv-tema">{html.escape(_resumen_minimo(e))}'
        + _render_candidatas(e)
        + "</span>"
        + (
            f'<a class="rv-pdf" href="{html.escape(e.get("url_documento") or "")}" '
            f'target="_blank" rel="noopener">PDF ↗</a>'
            if e.get("url_documento") else ""
        )
        + "</li>"
        # Sin recorte: es un tab dedicado, y la lista completa es justamente el
        # producto. Recortarla escondería trabajo pendiente.
        for e in pendientes
    )
    con_pistas = sum(
        1 for e in pendientes if (e.get("vigencia") or {}).get("candidatas")
    )
    return (
        f'<section id="revision-manual">'
        f'<header><h2>Cambios de archivo sin fecha de vigencia</h2>'
        f'<span class="rv-count">{len(pendientes)}</span></header>'
        f'<p class="rv-nota">Estos documentos modifican archivos normativos del MSI '
        f'—lo que genera una obligación de reporte— pero no declaran desde cuándo '
        f'rige el cambio en una forma que se pueda extraer del PDF. '
        f'En {con_pistas} de ellos se listan las fechas que aparecen en el cuerpo '
        f'del documento como pista; cuál de ellas rige es una decisión que hay que '
        f'tomar leyendo el PDF.</p>'
        f'{_render_como_anotar()}'
        f'<ul class="rv-lista">{filas}</ul>{_render_revisados(entradas)}</section>'
    )


def _render_como_anotar() -> str:
    """El procedimiento para resolver un pendiente, junto a la lista.

    La lista de pendientes sin el procedimiento al lado se lee como un informe
    de solo lectura: quien la mira concluye que el contador no puede bajar. Y el
    dato clave —que anotar una fecha la saca de acá **y** la mete al Calendario
    en la misma pasada— no estaba dicho en ninguna parte de la página.
    """
    return (
        '<details class="rv-como">'
        '<summary>¿Encontraste la fecha? Así se anota</summary>'
        '<ol>'
        '<li><code>python scraper/revisar.py</code> — deja en '
        '<code>data/revisiones.csv</code> una fila por pendiente, con el PDF, '
        'los archivos detectados y las fechas candidatas para decidir sin abrir '
        'el documento.</li>'
        '<li>Abrir esa planilla en Excel y llenar la columna <b>vigencia</b>: '
        '<code>2025-11-01</code>, <code>01-11-2025</code> o <code>2025-11</code> '
        'si el documento sólo fija el mes. <code>inmediata</code> si rige desde '
        'su publicación, y <b>sin_fecha</b> = <code>si</code> si el documento '
        'de verdad no lo declara — es una respuesta válida, no un pendiente. '
        'Una fecha por archivo se escribe '
        '<code>RDC40=2026-01-01;RDC02=2025-11-01</code>.</li>'
        '<li><code>python scraper/dashboard.py</code> — aplica lo anotado.</li>'
        '</ol>'
        '<p>Ese último paso hace las dos cosas de una vez: el documento sale de '
        'esta lista <b>y</b> aparece en el Calendario de modificaciones en la '
        'fecha anotada, marcado «confirmada» para no confundirlo con lo que se '
        'extrajo del PDF. Refrescar la planilla nunca pisa lo ya escrito.</p>'
        '</details>'
    )


def _render_revisados(entradas: list[dict]) -> str:
    """Constancia de lo ya revisado a mano.

    Que un documento salga de la lista de pendientes no puede significar que
    desaparezca sin dejar rastro: sin esta línea no habría forma de distinguir
    "nadie lo miró todavía" de "lo miraron y no declara fecha".
    """
    revisados = [e for e in entradas if e.get("_revision")]
    if not revisados:
        return ""
    sin_fecha = sum(1 for e in revisados if e["_revision"].get("sin_fecha"))
    con_fecha = len(revisados) - sin_fecha
    partes = []
    if con_fecha:
        partes.append(f"{con_fecha} con fecha asignada")
    if sin_fecha:
        partes.append(f"{sin_fecha} sin fecha declarada en el documento")
    return (
        f'<p class="rv-revisados"><b>{len(revisados)}</b> documento'
        f'{"s" if len(revisados) != 1 else ""} ya revisado'
        f'{"s" if len(revisados) != 1 else ""} a mano: {" · ".join(partes)}.</p>'
    )


def _mes_legible(mes: str) -> str:
    """'2026-07' -> 'julio de 2026'."""
    try:
        return f"{_MESES_ES[int(mes[5:7]) - 1]} de {mes[:4]}"
    except (ValueError, IndexError):
        return mes



def _render_agenda(
    entradas: list[dict], hoy: datetime, indice: list[dict],
    ultima_consulta: str | None,
) -> str:
    """La Agenda completa: buscador, paneles, calendario y lo que viene después.

    Sustituye las tres columnas por plazo. El eje temporal continuo dice
    directamente en qué mes cae cada obligación, en vez de obligar a traducir
    «faltan 47 días» a una fecha, y deja ver los meses sin nada agendado, que
    también son información.
    """
    hitos = _hitos_agenda(entradas, hoy)
    calendario = _calendario_agenda(hitos, hoy)
    lejanos = _hitos_lejanos(hitos, hoy)
    sin_fecha = _sin_fecha_agenda(entradas)
    en_ventana = [h for m in calendario for h in m["items"]]

    por_cuerpo = Counter(
        t for h in en_ventana for c, t, _ in GRUPOS_CUERPO_NORMATIVO
        if c in _grupos_de_entrada(h)
    )
    # Los últimos 12 meses miden actividad del regulador —cuándo publicó— y no
    # trabajo pendiente, así que cuentan por fecha de publicación.
    corte = _indice_mes(hoy) - 12
    doce = Counter(
        t for e in entradas
        for c, t, _ in GRUPOS_CUERPO_NORMATIVO
        if c in _grupos_de_entrada(e)
        and (f := _parse_iso(e.get("fecha"))) and corte < _indice_mes(f) <= _indice_mes(hoy)
    )

    datos = json.dumps(
        {"indice": indice, "hoy": hoy.strftime("%Y-%m-%d")},
        ensure_ascii=False, separators=(",", ":"),
    )
    return (
        f'<div id="agenda">'
        f'{_render_ag_buscador(len(indice))}'
        f'{_render_ag_stats(en_ventana, calendario, lejanos, sin_fecha, ultima_consulta)}'
        f'<div class="ag-paneles">'
        f'{_render_ag_panel_cuerpo(por_cuerpo, doce, len(en_ventana))}'
        f'{_render_ag_panel_meses(calendario, hoy)}'
        f'{_render_ag_panel_sinfecha(sin_fecha)}'
        f'</div>'
        f'{_render_ag_ultimo(entradas, hoy)}'
        f'{_render_ag_riel(calendario, hoy, len(sin_fecha), len(en_ventana))}'
        f'{_render_ag_lejanos(lejanos, hoy)}'
        f'<script type="application/json" id="ag-datos">'
        f'{datos.replace("</", chr(60) + chr(92) + "/")}</script>'
        f'</div>'
    )


def _render_ag_buscador(total: int) -> str:
    ejemplos = "".join(
        f'<button type="button" data-q="{html.escape(q)}">{html.escape(q)}</button>'
        for q in ("R06", "RDC40", "C11", "NCG N°550")
    )
    return (
        f'<section class="ag-buscador">'
        f'<div class="ag-caja">'
        f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
        f'stroke-width="1.7" aria-hidden="true"><circle cx="7" cy="7" r="4.6"/>'
        f'<path d="M10.4 10.4 L14 14"/></svg>'
        f'<input id="ag-q" type="search" autocomplete="off" spellcheck="false" '
        f'aria-label="Buscar en los cambios normativos" '
        f'placeholder="Busca un archivo (R06), una norma (NCG 550) o cualquier texto…">'
        f'<button type="button" class="ag-x" id="ag-q-x" hidden '
        f'aria-label="Limpiar la búsqueda">×</button>'
        f'</div>'
        f'<div class="ag-ejemplos"><span>Prueba con</span>{ejemplos}'
        f'<span>· busca en los {total} documentos del histórico, no sólo en el calendario</span>'
        f'</div>'
        f'<div class="ag-respuesta" id="ag-respuesta"></div>'
        f'</section>'
    )


def _render_ag_stats(
    en_ventana: list[dict], calendario: list[dict],
    lejanos: list[dict], sin_fecha: list[dict], ultima_consulta: str | None,
) -> str:
    con_datos = sum(1 for m in calendario if m["items"])
    gen_fecha, gen_hora = _fecha_hora_partes(ultima_consulta)
    # El desglose viaja hasta el rótulo: este total y el contador del tab
    # «Revisión manual» cuentan conjuntos distintos —el tab sólo los que tocan
    # un archivo del MSI, esta celda además los que quedaron sin fecha legible—
    # y verlos uno al lado del otro sin explicación se lee como que el
    # dashboard se contradice.
    por_archivo = sum(1 for e in sin_fecha if e.get("_motivo") == "archivo")
    otras = len(sin_fecha) - por_archivo
    ayuda_sf = (
        f"{por_archivo} {'modifica' if por_archivo == 1 else 'modifican'} un archivo "
        f"del MSI y {'está' if por_archivo == 1 else 'están'} en el tab "
        f"«Revisión manual»; {otras} {'no tiene' if otras == 1 else 'no tienen'} "
        f"fecha legible por otra razón"
    ) if otras else "Todas modifican un archivo del MSI: están en el tab «Revisión manual»"

    celdas = [
        (str(len(en_ventana)), "hitos de vigencia en la ventana", True, ""),
        (f'{con_datos} <span class="ag-de">/ {len(calendario)}</span>',
         "meses con actividad", False, ""),
        (str(len(lejanos)), f"más allá de {MESES_AGENDA} meses", False, ""),
        (str(len(sin_fecha)), "obligaciones sin fecha", False, ayuda_sf),
        (f'{html.escape(gen_fecha)} <span class="ag-de">{html.escape(gen_hora)}</span>',
         "última actualización", False, ""),
    ]
    return '<div class="ag-stats">' + "".join(
        f'<div class="ag-stat{" es-clave" if clave else ""}'
        f'{" es-conayuda" if ayuda else ""}"'
        f'{f" title={chr(34)}{html.escape(ayuda)}{chr(34)}" if ayuda else ""}>'
        f'<b class="ag-num">{v}</b><span>{html.escape(t)}</span></div>'
        for v, t, clave, ayuda in celdas
    ) + '</div>'


def _render_ag_barras(filas: list[tuple[str, int]], fill: str, filtrable: bool) -> str:
    """Barras horizontales ordenadas por magnitud.

    «Otros» va siempre al final y rayado: es el residuo de la clasificación,
    no un cuerpo normativo, y ordenado por tamaño aparecía encabezando.
    """
    if not filas:
        return '<p class="ag-vacio">Sin datos en este período.</p>'
    orden = sorted(filas, key=lambda f: (f[0] == "Otros", -f[1]))
    tope = max(n for _, n in orden) or 1
    salida = []
    for nombre, n in orden:
        residual = " es-residual" if nombre == "Otros" else ""
        titulo = f"Filtrar el calendario por {nombre}" if filtrable else f"{nombre}: {n}"
        salida.append(
            f'<button type="button" class="ag-hbar{residual}" '
            f'data-cuerpo="{html.escape(nombre)}" title="{html.escape(titulo)}"'
            f'{"" if filtrable else " disabled"}>'
            f'<span class="ag-hbar-lbl">{html.escape(nombre)}</span>'
            f'<span class="ag-hbar-track"><span class="ag-hbar-fill" '
            f'style="width:{max(n / tope * 100, 3):.1f}%;--ag-fill:{fill}"></span></span>'
            f'<span class="ag-hbar-val ag-num">{n}</span></button>'
        )
    return "".join(salida)


def _render_ag_panel_cuerpo(
    por_cuerpo: Counter, doce: Counter, total_ventana: int
) -> str:
    """Un panel con conmutador en vez de dos gráficos gemelos.

    Las dos medidas comparten forma y dimensión —cuerpo normativo— y sólo
    cambia qué se cuenta, así que en paneles separados se leían como si fueran
    lo mismo dos veces.
    """
    return (
        f'<section class="ag-panel">'
        f'<div class="ag-panel-head"><h3>Por cuerpo normativo</h3>'
        f'<div class="ag-switch" role="tablist" aria-label="Medida">'
        f'<button type="button" role="tab" data-medida="tareas" aria-selected="true">Tareas</button>'
        f'<button type="button" role="tab" data-medida="cambios" aria-selected="false">Cambios</button>'
        f'</div></div>'
        f'<p class="ag-sub" data-medida="tareas">Hitos de vigencia en la ventana. '
        f'Una tarea puede tocar más de un cuerpo, así que las barras suman más '
        f'que los {total_ventana} hitos. <b>Haz clic para filtrar el calendario.</b></p>'
        f'<p class="ag-sub" data-medida="cambios" hidden>Documentos publicados en los '
        f'últimos 12 meses. Mide actividad del regulador, no trabajo pendiente.</p>'
        f'<div class="ag-medida" data-medida="tareas">'
        f'{_render_ag_barras(list(por_cuerpo.items()), "var(--ag-dato-1)", True)}</div>'
        f'<div class="ag-medida" data-medida="cambios" hidden>'
        f'{_render_ag_barras(list(doce.items()), "var(--ag-dato-2)", False)}</div>'
        f'</section>'
    )


def _render_ag_panel_meses(calendario: list[dict], hoy: datetime) -> str:
    tope = max((len(m["items"]) for m in calendario), default=0) or 1
    cols, ejes = [], []
    for m in calendario:
        n = len(m["items"])
        clases = " ".join(filter(None, [
            "es-futuro" if m["offset"] > 0 else "",
            "es-cero" if n == 0 else "",
            "es-hoy" if m["offset"] == 0 else "",
        ]))
        cols.append(
            f'<div class="ag-col {clases}" title="{html.escape(_mes_legible(m["mes"]))}: {n}">'
            f'{"<span class=ag-seam></span>" if m["offset"] == 0 else ""}'
            f'<span class="ag-col-n ag-num">{n}</span>'
            f'<span class="ag-col-bar" style="height:{n / tope * 82:.1f}%"></span></div>'
        )
        mes_num = int(m["mes"][5:7])
        etiqueta = m["mes"][2:4] if mes_num == 1 else _MESES_ES[mes_num - 1][0]
        ejes.append(
            f'<span class="ag-col-x{" es-hoy" if m["offset"] == 0 else ""}">'
            f'{html.escape(etiqueta)}</span>'
        )
    return (
        f'<section class="ag-panel"><h3>Proyectos por mes</h3>'
        f'<p class="ag-sub">Seis meses cumplidos y seis por delante. '
        f'La línea marca hoy.</p>'
        f'<div class="ag-cols">{"".join(cols)}</div>'
        f'<div class="ag-cols-x">{"".join(ejes)}</div>'
        f'<div class="ag-legend">'
        f'<span><i style="background:var(--ag-dato-pasado)"></i>Ya debió aplicarse</span>'
        f'<span><i style="background:var(--ag-dato-1)"></i>Por venir</span>'
        f'</div></section>'
    )


def _render_ag_panel_sinfecha(sin_fecha: list[dict]) -> str:
    """El punto ciego del eje temporal, declarado en vez de omitido."""
    total = len(sin_fecha)
    if not total:
        return (
            '<section class="ag-panel es-alerta"><h3>Obligaciones sin fecha</h3>'
            '<p class="ag-sub">Todo lo que genera trabajo tiene una fecha asociada. '
            'Nada queda fuera del calendario.</p></section>'
        )
    por_archivo = sum(1 for s in sin_fecha if s["_motivo"] == "archivo")
    relativas = total - por_archivo
    filas = "".join(
        f'<div class="ag-sf-fila" title="{html.escape(ayuda)}">'
        f'<div class="ag-sf-top"><span>{html.escape(nombre)}</span>'
        f'<b class="ag-num">{n}</b></div>'
        f'<div class="ag-sf-track"><div class="ag-sf-fill{cls}" '
        f'style="width:{n / total * 100:.1f}%"></div></div></div>'
        for nombre, n, cls, ayuda in (
            ("Modifican un archivo del MSI", por_archivo, "",
             "Generan obligación de reporte y no se pudo determinar desde cuándo. "
             "Son los que aparecen en el tab «Revisión manual»"),
            ("Sin fecha legible", relativas, " es-alt",
             "El documento no declara cuándo rige, o lo declara de una forma que "
             "no se puede resolver. No tocan archivos del MSI, así que no están "
             "en «Revisión manual»"),
        )
    )
    lista = "".join(
        f'<div class="ag-sf-item"><b>{html.escape(_etiqueta_documento(e))}</b>'
        f'<span>{html.escape(_tema_corto(e, 70))}</span>'
        f'<em>{html.escape(e.get("fecha") or "")}'
        f'{_sufijo_archivos(e)}</em></div>'
        for e in sin_fecha[:14]
    )
    return (
        f'<section class="ag-panel es-alerta"><h3>Obligaciones sin fecha</h3>'
        f'<p class="ag-sub">Generan trabajo pero no tienen cuándo, así que '
        f'<b>no aparecen en el calendario</b>. Es el punto ciego de esta vista.</p>'
        f'<div class="ag-sf-total"><b class="ag-num">{total}</b>'
        f'<span>documentos fuera del calendario</span></div>{filas}'
        f'<p class="ag-sf-pie">En su mayoría no es deuda de regex: la fecha viene '
        f'entrelazada con el ciclo de reporte («al cierre de agosto y, por lo tanto, '
        f'enviarse en septiembre»), y cuál de las dos rige es un juicio. Se resuelven anotando '
        f'en <code>revisiones.csv</code>.</p>'
        f'<details class="ag-datos"><summary>Ver las más recientes</summary>'
        f'<div class="ag-sf-lista">{lista}</div></details></section>'
    )


def _tema_corto(e: dict, largo: int) -> str:
    txt = " ".join((e.get("tema") or e.get("descripcion_cmf") or "").split())
    return txt if len(txt) <= largo else txt[: largo - 1] + "…"


def _sufijo_archivos(e: dict) -> str:
    """Códigos de archivo del MSI, ya escapados.

    Escapa acá y no en quien llama: el valor se interpola directo en el HTML,
    y el resto de este módulo escapa en el punto de interpolación, así que un
    helper que devuelve texto crudo se lee como si ya estuviera a salvo. Hoy
    el patrón del parser sólo produce letras y dígitos (`_COD_ARCHIVO`), pero
    eso es una propiedad de otro archivo y puede cambiar sin que nadie mire
    esta línea.
    """
    codigos = [a.get("nombre") for a in (e.get("archivos_afectados") or []) if a.get("nombre")]
    return f" · {html.escape(', '.join(str(c) for c in codigos[:4]))}" if codigos else ""


_ROTULO_FUENTE = {
    "seccion": ("", "sección", "Fecha declarada en la sección Vigencia del PDF"),
    "clausula_aplicacion": ("es-clausula", "cláusula",
                            "Deducida de una cláusula de aplicación — respaldo más frágil"),
    "revision_manual": ("es-manual", "confirmada",
                        "Confirmada a mano tras leer el PDF"),
    # El documento no escribe una fecha sino la regla para obtenerla ("en el
    # plazo de un mes contado desde su publicación"). La fecha es firme, pero
    # se calculó: mostrarla igual que una escrita en el PDF borraría esa
    # diferencia, y el tooltip lleva la regla y la base para poder auditarla.
    "calculada": ("es-calculada", "calculada",
                  "Calculada desde el plazo que declara el PDF"),
}


def _render_ag_ultimo(entradas: list[dict], hoy: datetime) -> str:
    """La resolución publicada más recientemente, con su detalle desplegable.

    La agenda mira hacia adelante —cuándo hay que tener algo hecho— y por eso
    no contestaba la otra pregunta con que se abre el dashboard: qué salió
    último. Había que saltar al Listado completo y leer la primera fila.

    Dos decisiones que evitan que esto se despegue del listado:

    - El orden es el mismo que usa `_render_tabla` (`fecha`, `clave`,
      descendente), así que «el último publicado» y «la primera fila de allá»
      no pueden divergir. Si cambias uno, cambia el otro.
    - El detalle sale de `_render_detalle`, el mismo del listado, en vez de un
      resumen propio: cuando esa función gane un bloque, este lo hereda solo.
    """
    if not entradas:
        return ""

    e = max(entradas, key=lambda x: (x.get("fecha") or "", x.get("clave") or ""))
    clave = e.get("clave", "")
    url = e.get("url_documento") or ""
    normas = _normas_afectadas(e)

    # El "hace N días" se calcula sobre la fecha guardada, que puede ser el
    # placeholder YYYY-01-01 (ver el modo de falla 1 en CLAUDE.md). No se
    # corrige acá: si el dato está mal, que se note en la portada de la agenda
    # es mejor que maquillarlo.
    f = _parse_iso(e.get("fecha"))
    dias = (hoy - f).days if f else None
    if dias is None:
        rel = "sin fecha legible"
    elif dias <= 0:
        rel = "hoy"
    elif dias == 1:
        rel = "ayer"
    else:
        rel = f"hace {dias} días"

    badges = "".join(_tipo_tag(t) for t in _tipos_de_entrada(e))
    meta = "".join(
        f'<div class="ag-ult-dato"><span>{k}</span><b>{v}</b></div>'
        for k, v in (
            ("Norma(s) afectada(s)", ", ".join(html.escape(n) for n in normas)
             if normas else "—"),
            ("Vigencia", html.escape(_vigencia_fmt(e.get("vigencia")))),
        )
    )
    pdf = (
        f'<a class="ag-ult-pdf" href="{html.escape(url)}" target="_blank" '
        f'rel="noopener">PDF ↗</a>' if url else ""
    )

    return (
        # <div> y no <section>: la regla global `section {…}` le pone fondo
        # blanco, borde y sombra al bloque, y `section h2 {…}` le mete el título
        # en una barra gris propia. Eso encajona el encabezado en un parche que
        # no se parece a nada en esta pestaña — el riel y «Más allá de 6 meses»
        # cuelgan su `ag-sec-head` directo del fondo de la página. El único
        # blanco acá lo pone la tarjeta de adentro, que sí es una tarjeta.
        f'<div class="ag-ultimo">'
        f'<div class="ag-sec-head"><h2>Último cambio publicado</h2>'
        f'<span class="ag-hint">{html.escape(rel)} · lo mismo que encabeza el '
        f'Listado completo</span></div>'
        f'<article class="ag-ult-card">'
        f'<div class="ag-ult-top">'
        f'<span class="ag-ult-fecha">{html.escape(e.get("fecha") or "—")}</span>'
        f'<b class="ag-ult-doc">{html.escape(_etiqueta_documento(e))}</b>'
        f'<span class="ag-ult-tags">{badges}</span></div>'
        f'<p class="ag-ult-tema">{html.escape(_resumen_minimo(e))}</p>'
        f'<div class="ag-ult-meta">{meta}</div>'
        f'<div class="ag-ult-acciones">'
        f'<button type="button" class="btn-revisar" data-ult-toggle '
        f'aria-expanded="false"><span class="rv-txt">Revisar ▾</span></button>'
        f'<button type="button" class="ag-ult-ir" data-fila="{html.escape(clave)}">'
        f'Ver en el Listado completo ↗</button>'
        f'{pdf}</div>'
        f'<div class="ag-ult-detalle" hidden>{_render_detalle(e)}</div>'
        f'</article></div>'
    )


def _render_ag_riel(
    calendario: list[dict], hoy: datetime, n_sin_fecha: int, n_ventana: int
) -> str:
    """El eje temporal. Los meses vacíos consecutivos se apilan en un mazo.

    El mes en curso nunca se apila, aunque esté vacío: es el ancla del eje y
    esconderlo deja el riel sin punto de referencia.
    """
    piezas: list[str] = []
    pendientes: list[dict] = []

    def vaciar() -> None:
        if pendientes:
            piezas.append(_render_ag_mazo(pendientes))
            pendientes.clear()

    for m in calendario:
        if not m["items"] and m["offset"] != 0:
            pendientes.append(m)
            continue
        vaciar()
        piezas.append(_render_ag_mes(m))
    vaciar()

    aviso = (
        f'<div class="ag-nota-riel"><b class="ag-num">{n_sin_fecha}</b> '
        f'obligaciones sin fecha determinada quedan fuera de este eje — '
        f'están en el panel de arriba.</div>'
        if n_sin_fecha else ""
    )
    return (
        f'<div class="ag-sec-head"><h2>Calendario de modificaciones</h2>'
        f'<div class="ag-riel-ctrl">'
        f'<span class="ag-hint">A la izquierda lo cumplido, a la derecha lo que viene</span>'
        f'<button type="button" id="ag-izq" aria-label="Meses anteriores">‹</button>'
        f'<button type="button" id="ag-hoy" class="es-hoy">Hoy</button>'
        f'<button type="button" id="ag-der" aria-label="Meses siguientes">›</button>'
        f'</div></div>'
        f'{aviso}'
        f'<div class="ag-filtro" id="ag-filtro" data-total="{n_ventana}"></div>'
        f'<div class="ag-riel-outer">'
        f'<div class="ag-riel" id="ag-riel" tabindex="0" role="region" '
        f'aria-label="Calendario de modificaciones, {len(calendario)} meses">'
        f'{"".join(piezas)}</div></div>'
    )


def _render_ag_mes(m: dict) -> str:
    estado = "es-pasado" if m["offset"] < 0 else ("es-hoy" if m["offset"] == 0 else "es-futuro")
    mes_num = int(m["mes"][5:7])
    if m["items"]:
        cuerpo = "".join(_render_ag_tarjeta(h) for h in m["items"])
    else:
        cuerpo = (
            '<div class="ag-mes-vacio"><span class="ag-regla"></span>'
            '<span>No hay proyectos de cambio agendados para este mes</span></div>'
        )
    marca = (
        '<span class="ag-hoy-chip">hoy</span>' if m["offset"] == 0
        else f'<span class="ag-mes-n ag-num">{len(m["items"])}</span>'
    )
    return (
        f'<article class="ag-mes {estado}" data-mes="{html.escape(m["mes"])}"'
        f'{" id=ag-mes-hoy" if m["offset"] == 0 else ""}>'
        f'<div class="ag-mes-head"><span class="ag-mes-nom">'
        f'{html.escape(_MESES_ES[mes_num - 1])}<small>{html.escape(m["mes"][:4])}</small>'
        f'</span>{marca}</div>'
        f'<div class="ag-mes-body">{cuerpo}'
        f'<div class="ag-mes-filtrado" hidden><span class="ag-regla"></span>'
        f'<span>Ningún proyecto de este cuerpo normativo en el mes</span></div>'
        f'</div></article>'
    )


def _render_ag_mazo(meses: list[dict]) -> str:
    """Los meses sin actividad, uno sobre otro.

    Apilarlos en vez de repetir tarjetas idénticas hace que el ojo salte los
    tramos quietos, y de paso los vuelve visibles como tramo: cinco meses en
    blanco seguidos dicen algo que cinco tarjetas iguales no dicen.
    """
    plural = len(meses) > 1
    capas = "".join('<div class="ag-mazo-capa"></div>' for _ in range(min(len(meses) - 1, 2)))
    etiquetas = "".join(
        f'<span class="ag-mazo-mes">{html.escape(_MESES_ES[int(m["mes"][5:7]) - 1][:3])}'
        f'<small> {html.escape(m["mes"][2:4])}</small></span>'
        for m in meses
    )
    titulo = ", ".join(_mes_legible(m["mes"]) for m in meses)
    return (
        f'<div class="ag-mazo" title="{html.escape(titulo)}: sin actividad">'
        f'<div class="ag-mazo-stack">{capas}'
        f'<div class="ag-mazo-front"><div class="ag-mazo-meses">{etiquetas}</div>'
        f'<span class="ag-regla"></span>'
        f'<span class="ag-mazo-msg">No hay proyectos de cambio agendados para '
        f'est{"os meses" if plural else "e mes"}</span>'
        f'</div></div></div>'
    )


def _fecha_hito(h: dict) -> str:
    """Día y mes, salvo cuando el documento sólo declaró el mes."""
    iso = h["_fecha_aplicacion"]
    mes = _MESES_ES[int(iso[5:7]) - 1][:3]
    return mes if h.get("_precision") == "mes" else f"{int(iso[8:10])} {mes}"


def _render_ag_tarjeta(h: dict) -> str:
    cls_fuente, rotulo, ayuda = _ROTULO_FUENTE.get(
        h.get("_fuente_vigencia") or "seccion", _ROTULO_FUENTE["seccion"]
    )
    # Para una fecha calculada, el tooltip lleva la regla y la fecha base: es
    # lo que permite verificar el dato sin volver al PDF, y la contrapartida
    # de mostrar una fecha que el documento no trae escrita.
    calculo = _calculo_de(h)
    if calculo:
        ayuda = (f"{ayuda}: {calculo.get('expresion', '')}"
                 f" (base: {calculo.get('fecha_base', '')})")
    cuerpos = [t for c, t, _ in GRUPOS_CUERPO_NORMATIVO if c in _grupos_de_entrada(h)]
    archivos = [a for a in (h.get("archivos_afectados") or []) if a.get("nombre")][:4]
    chips = "".join(filter(None, [
        '<span class="ag-chip es-inm">vigencia inmediata</span>' if h.get("_inmediata") else "",
        *(f'<span class="ag-chip es-archivo" title="Archivo del MSI'
          f'{" · rige " + html.escape(str(a.get("vigencia"))) if a.get("vigencia") else ""}">'
          f'{html.escape(str(a["nombre"]))}</span>' for a in archivos),
        *(f'<span class="ag-chip">{html.escape(c)}</span>' for c in cuerpos),
    ]))
    saltos = "".join(
        f'<button type="button" class="ag-chip es-norma" data-timeline="{html.escape(n)}" '
        f'title="Ver la línea de tiempo de {html.escape(n)}">↗ {html.escape(n)}</button>'
        for n in (h.get("_afectadas") or [])
    )
    url = h.get("url_documento")
    pdf = (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener">PDF</a>'
        if url else ""
    )
    return (
        f'<div class="ag-tarea{" es-vencida" if h.get("_vencida") else ""}" '
        f'data-cuerpos="{html.escape("|".join(cuerpos))}">'
        f'<div class="ag-tarea-top">'
        f'<span class="ag-tarea-norma">{html.escape(_etiqueta_documento(h))}</span>'
        f'<span class="ag-tarea-fecha ag-num">{html.escape(_fecha_hito(h))}</span></div>'
        f'<div class="ag-tarea-desc">{html.escape(_tema_corto(h, 90))}</div>'
        f'<div class="ag-chips">{chips}</div>'
        f'{f"<div class=ag-chips>{saltos}</div>" if saltos else ""}'
        f'<div class="ag-tarea-links">{pdf}'
        f'<button type="button" data-fila="{html.escape(h.get("clave") or "")}">'
        f'Ver en el listado</button>'
        f'<span class="ag-fuente {cls_fuente}" title="{html.escape(ayuda)}">{rotulo}</span>'
        f'</div></div>'
    )


def _render_ag_lejanos(lejanos: list[dict], hoy: datetime) -> str:
    if not lejanos:
        return ""
    por_fecha: dict[str, list[dict]] = {}
    for h in lejanos:
        por_fecha.setdefault(h["_fecha_aplicacion"], []).append(h)
    ancla = _indice_mes(hoy)
    grupos = []
    for fecha in sorted(por_fecha):
        items = por_fecha[fecha]
        meses = (int(fecha[:4]) * 12 + int(fecha[5:7]) - 1) - ancla
        filas = "".join(
            f'<div class="ag-lejos-item">'
            f'<span class="ag-lejos-norma">{html.escape(_etiqueta_documento(h))}</span>'
            f'<span class="ag-lejos-desc">{html.escape(_tema_corto(h, 120))}</span></div>'
            for h in items
        )
        grupos.append(
            f'<div class="ag-lejos-grupo"><div class="ag-lejos-fecha">'
            f'<b class="ag-num">{int(fecha[8:10])} '
            f'{html.escape(_MESES_ES[int(fecha[5:7]) - 1][:3])} {fecha[:4]}</b>'
            f'<span>en {meses} meses · {len(items)} '
            f'{"tarea" if len(items) == 1 else "tareas"}</span></div>'
            f'<div class="ag-lejos-items">{filas}</div></div>'
        )
    ultima = max(por_fecha)
    return (
        f'<div class="ag-sec-head"><h2>Más allá de {MESES_AGENDA} meses</h2>'
        f'<span class="ag-hint">{len(lejanos)} tareas en {len(por_fecha)} fechas, '
        f'hasta {html.escape(_mes_legible(ultima[:7]))}</span></div>'
        f'<div class="ag-lejos">{"".join(grupos)}</div>'
    )



def _render_cambios_relevantes(
    grupos_cuerpo: dict[str, list[dict]], hoy: datetime
) -> str:
    """Renderiza el tab 'Cambios relevantes' con una sección por cuerpo normativo.

    Filtra a los últimos 5 años respecto a `hoy`. Cada grupo se renderiza
    colapsado; el botón 'Revisar →' del header expande la tabla.
    """
    año_corte = hoy.year - 5
    secciones: list[str] = []
    for clave, titulo, descripcion in GRUPOS_CUERPO_NORMATIVO:
        items = grupos_cuerpo.get(clave) or []
        seccion = _render_grupo_cuerpo(
            clave, titulo, descripcion, items, año_corte
        )
        if seccion:
            secciones.append(seccion)
    if not secciones:
        return '<p class="cr-vacio">Sin cambios relevantes en los últimos 5 años.</p>'
    intro = (
        f'<p class="cr-intro">Cambios normativos desde {año_corte} agrupados '
        f'por cuerpo normativo. Click en <b>Revisar →</b> para abrir cada grupo.</p>'
    )
    return f'<div id="cambios-relevantes">{intro}{"".join(secciones)}</div>'


def _render_grupo_cuerpo(
    clave: str,
    titulo: str,
    descripcion: str,
    items: list[dict],
    año_corte: int,
) -> str:
    """Renderiza un grupo (cuerpo normativo) si tiene entradas recientes.

    Devuelve "" si todas las entradas del grupo son anteriores a año_corte.
    """
    recientes = [
        e for e in items
        if (e.get("fecha") or "")[:4].isdigit()
        and int((e.get("fecha") or "0000")[:4]) >= año_corte
    ]
    n_recientes = len(recientes)
    n_total = len(items)
    if n_recientes == 0:
        return ""

    extra_total = (
        f'<span class="cr-total">de {n_total} histórica{"s" if n_total != 1 else ""}</span>'
        if n_total > n_recientes else ""
    )
    filas = "".join(_render_fila_cuerpo(e) for e in recientes)
    return (
        f'<section class="cr-grupo" data-grupo="{html.escape(clave)}">'
        f'<header class="cr-cab">'
        f'<div class="cr-cab-tit">'
        f'<h2>{html.escape(titulo)}</h2>'
        f'<span class="cr-count">{n_recientes}</span>'
        f'{extra_total}'
        f'<button type="button" class="cr-revisar" aria-expanded="false" '
        f'onclick="toggleGrupoCR(this)"><span class="rv-txt">Revisar ▾</span>'
        f'</button>'
        f'</div>'
        f'<p class="cr-desc">{html.escape(descripcion)}</p>'
        f'</header>'
        f'<div class="cr-cuerpo" style="display:none">'
        f'<table class="cr-tabla">'
        f'<thead><tr>'
        f'<th class="cr-th-fecha">Fecha</th>'
        f'<th class="cr-th-doc">Norma</th>'
        f'<th>Tema</th>'
        f'<th class="cr-th-cambios">Cambios</th>'
        f'<th class="cr-th-pdf">PDF</th>'
        f'</tr></thead>'
        f'<tbody>{filas}</tbody>'
        f'</table>'
        f'</div>'
        f'</section>'
    )


def _render_fila_cuerpo(
    e: dict, fecha: str | None = None, inmediata: bool | None = None
) -> str:
    """Fila de la tabla Fecha | Norma | Tema | Cambios | PDF.

    `fecha` permite mostrar la de entrada en vigencia en vez de la de
    publicación: en la retrospectiva de la agenda el eje es cuándo empezó a
    regir, no cuándo se publicó. `inmediata` marca las que rigen desde su
    publicación, que no traen fecha propia.
    """
    fecha_pub = e.get("fecha") or "—"
    fecha = fecha or fecha_pub
    marca = (
        '<span class="rt-inm" title="Rige desde su publicación">inmediata</span>'
        if inmediata else ""
    )
    tema = _resumen_minimo(e)
    bullets = e.get("resumen_acciones") or []
    archivos = e.get("archivos_afectados") or []
    url = e.get("url_documento") or ""

    detalle_html = _render_detalle_tarea(bullets, archivos, e.get("vigencia"))
    # El número tiene que contar lo que el detalle realmente muestra. Contaba
    # sólo los bullets, así que un documento con archivos afectados y sin
    # bullets se anunciaba como "0 cambios" y aun así abría un panel con
    # contenido: eran 49 de las 176 filas.
    n_cambios = len(bullets) + len(archivos)
    if n_cambios:
        etiqueta_detalle = f'<b>{n_cambios}</b> cambio{"s" if n_cambios != 1 else ""}'
    else:
        etiqueta_detalle = "plazos"
    # Botón y no enlace: "3 cambios →" se leía como un dato de la fila y no
    # como un control, así que el desglose quedaba sin descubrir. El conteo se
    # conserva porque dice cuánto hay detrás antes de abrir.
    cambios_cell = (
        f'<button type="button" class="btn-revisar cr-detalle-toggle" '
        f'aria-expanded="false" onclick="toggleDetalleCR(this)">'
        f'{etiqueta_detalle} · <span class="rv-txt">Revisar ▾</span></button>'
        if detalle_html else
        # Sin detalle no hay nada que contar: un "0" suelto se lee como un dato
        # y en realidad significa que del PDF no se pudo extraer el desglose.
        '<span class="cr-sin-detalle">—</span>'
    )
    pdf_cell = (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener">PDF ↗</a>'
        if url else "—"
    )
    detalle_row = (
        # El estado va en una clase y no en `style="display:none"` + un
        # `style.display = 'table-row'` desde el JS. El display en línea gana
        # sobre cualquier media query, así que con el valor incrustado no había
        # forma de que en celular la fila abierta dejara de ser `table-row`
        # dentro de un contenedor que allá es `block`. Con la clase, cada
        # breakpoint decide cómo se muestra.
        f'<tr class="cr-detalle-row" data-open="0">'
        f'<td colspan="5">{detalle_html}</td></tr>'
        if detalle_html else ""
    )

    return (
        f'<tr class="cr-fila">'
        f'<td class="cr-td-fecha">{html.escape(str(fecha))}{marca}</td>'
        f'<td class="cr-td-doc">{html.escape(_etiqueta_documento(e))}</td>'
        f'<td class="cr-td-tema">{html.escape(tema)}</td>'
        f'<td class="cr-td-cambios">{cambios_cell}</td>'
        f'<td class="cr-td-pdf">{pdf_cell}</td>'
        f'</tr>{detalle_row}'
    )



def _render_detalle_tarea(
    bullets: list[str], archivos: list[dict], vigencia: dict | None = None
) -> str:
    plazos = (vigencia or {}).get("plazos") or []
    if not bullets and not archivos and not plazos:
        return ""
    bloques: list[str] = []
    if plazos:
        # Primero: cuando un documento tiene plazos escalonados, saber qué rige
        # cuándo es la pregunta que trae a alguien a esta tarjeta.
        items = "".join(
            f'<li><b>{html.escape(_fmt_inicio(p))}</b>'
            f' · {html.escape(p.get("texto") or "")}</li>'
            for p in plazos
        )
        bloques.append(
            f'<div class="cm-det-bloque"><span class="cm-det-label">Plazos de entrada '
            f'en vigencia</span><ul class="cm-bullets">{items}</ul></div>'
        )
    if bullets:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in bullets)
        bloques.append(
            f'<div class="cm-det-bloque"><span class="cm-det-label">Cambios</span>'
            f'<ul class="cm-bullets">{items}</ul></div>'
        )
    if archivos:
        items = "".join(
            f'<li><span class="chip chip-{html.escape(a.get("accion",""))}">'
            f'{html.escape(a.get("accion","").upper())}</span> '
            f'{html.escape(a.get("nombre",""))}</li>'
            for a in archivos
        )
        bloques.append(
            f'<div class="cm-det-bloque"><span class="cm-det-label">Archivos afectados</span>'
            f'<ul class="cm-archivos">{items}</ul></div>'
        )
    return f'<div class="cm-detalle">{"".join(bloques)}</div>'


def _resumen_minimo(t: dict) -> str:
    """Una línea sobre qué hay que hacer.

    Usa el campo `tema` (bloque REF del PDF) si existe; cae a la primera frase
    de la descripción CMF si no, en sentence-case para evitar el ruido visual
    del listado en mayúsculas.
    """
    tema = (t.get("tema") or "").strip()
    if tema:
        return tema if len(tema) <= 220 else tema[:220].rsplit(" ", 1)[0] + "…"
    desc = (t.get("descripcion_cmf") or "").strip()
    if not desc:
        return "Sin resumen disponible."
    primera = desc.split(".")[0]
    if primera.isupper():
        primera = primera.capitalize()
    return primera if len(primera) <= 220 else primera[:220].rsplit(" ", 1)[0] + "…"


def _render_stats(counts: dict[str, int], total: int) -> str:
    pills = [f'<span class="stat"><b>{total}</b> resoluciones monitoreadas</span>']
    for tipo, _ in TIPOS_FILTRO:
        if tipo == "todos":
            continue
        c = counts.get(tipo, 0)
        if c:
            cls = _tipo_class(tipo)
            pills.append(f'<span class="stat {cls}"><b>{c}</b> {html.escape(tipo)}</span>')
    return '<div id="stats">' + "".join(pills) + "</div>"


def _render_filtros(counts: dict[str, int]) -> str:
    botones = []
    for tipo, label in TIPOS_FILTRO:
        # Un botón que no tiene ninguna fila detrás sólo puede vaciar la tabla.
        # Se omite en vez de rendirlo muerto — y reaparece solo el día que
        # llegue el primer caso, sin tocar código.
        if tipo != "todos" and not counts.get(tipo):
            continue
        cls = "filtro-btn activo" if tipo == "todos" else "filtro-btn"
        botones.append(
            f'<button class="{cls}" data-tipo="{html.escape(tipo)}" '
            f'onclick="setTipo(this)">{html.escape(label)}</button>'
        )
    return (
        '<div id="filtros">'
        + "".join(botones)
        + '<input id="search" type="search" placeholder="Buscar por NCG, descripción, RAN, archivo…" '
        'oninput="aplicarFiltros()">'
        + "</div>"
    )


def _tipo_class(tipo: str) -> str:
    return {
        "Consulta Pública": "tag-consulta",
        "Nueva Normativa": "tag-nueva",
        "Modificación NCG": "tag-mod",
        "Circular": "tag-circular",
        "Postergación de vigencia": "tag-postergacion",
        "Derogación": "tag-deroga",
    }.get(tipo, "tag-otro")


def _tipo_tag(tipo: str) -> str:
    return f'<span class="tag {_tipo_class(tipo)}">{html.escape(tipo)}</span>'


def _render_tabla(entradas: list[dict], novedades: list[dict]) -> str:
    if not entradas:
        return '<tr><td colspan="7" class="td-vacio">Sin datos aún.</td></tr>'

    claves_nuevas = {e.get("clave") for e in novedades}
    filas: list[str] = []
    for e in sorted(entradas, key=lambda x: (x.get("fecha") or "", x.get("clave") or ""), reverse=True):
        filas.append(_render_fila(e, e.get("clave") in claves_nuevas))
    return "\n".join(filas)


def _render_fila(e: dict, es_nueva: bool) -> str:
    fecha = e.get("fecha") or (e.get("resolucion") or {}).get("fecha") or "—"
    res = e.get("resolucion") or {}
    num_res = res.get("numero") or "—"
    documento = _etiqueta_documento(e)
    tipos = _tipos_de_entrada(e)
    descripcion = e.get("descripcion_cmf", "") or ""
    normas = _normas_afectadas(e) or ["—"]
    vigencia = _vigencia_fmt(e.get("vigencia"))
    url = e.get("url_documento") or ""
    clave = e.get("clave", "")

    badges = "".join(_tipo_tag(t) for t in tipos)
    normas_html = ", ".join(html.escape(n) for n in normas)
    link = (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener">PDF ↗</a>'
        if url else "—"
    )

    search_blob = " ".join([
        # `documento` va en el blob para poder buscar "circular 2370" o "2.370"
        # tal como aparece en la columna; `num_res` sólo cuando el PDF declara
        # una resolución exenta de verdad, que es raro pero es buscable.
        clave, documento, str(num_res), descripcion,
        " ".join(normas),
        " ".join(e.get("ran_referencias") or []),
        " ".join(a.get("nombre", "") for a in e.get("archivos_afectados") or []),
    ]).lower()

    cls_nueva = " nueva" if es_nueva else ""
    detalle = _render_detalle(e)

    return (
        f'<tr class="fila-principal{cls_nueva}" '
        f'data-clave="{html.escape(clave)}" '
        f'data-tipos="{html.escape("|".join(tipos))}" '
        f'data-search="{html.escape(search_blob)}" '
        f'onclick="toggleDetail(this)">'
        # Todas las celdas llevan clase, incluidas fecha y tipos, que antes no
        # la necesitaban: en celular la fila se reordena con `grid-area` y una
        # celda sin nombre no se puede colocar.
        f'<td class="td-fecha">{html.escape(fecha)}</td>'
        f'<td class="td-doc"><b>{html.escape(documento)}</b></td>'
        f'<td class="td-tipos">{badges}</td>'
        f'<td class="td-normas">{normas_html}</td>'
        f'<td class="td-vig">{html.escape(vigencia)}</td>'
        f'<td class="td-link">{link}</td>'
        # El botón no lleva onclick propio: el click burbujea al <tr>, que ya
        # gobierna el despliegue. Poniéndole uno, la fila se abría y se cerraba
        # en el mismo click. Su valor es doble — anuncia que la fila es
        # pinchable, cosa que `cursor: pointer` sólo revela si ya pasaste el
        # mouse por encima, y hace la tabla operable con teclado, porque el
        # botón sí es tabulable y el <tr> con onclick nunca lo fue.
        f'<td class="td-revisar">'
        f'<button type="button" class="btn-revisar" tabindex="0" '
        f'aria-expanded="false"><span class="rv-txt">Revisar ▾</span>'
        f'</button></td>'
        f'</tr>'
        f'<tr class="detail-row" data-open="0"><td colspan="7">{detalle}</td></tr>'
    )


def _render_detalle(e: dict) -> str:
    bloques: list[str] = []

    desc = e.get("descripcion_cmf", "") or ""
    if desc:
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Descripción CMF</span>'
            f'<p>{html.escape(desc)}</p></div>'
        )

    # Sólo las resoluciones exentas que el PDF declara. Son pocas —la mayoría de
    # los documentos no ejecutan una resolución nominada— pero cuando existe es
    # el acto administrativo que da origen al cambio.
    res = e.get("resolucion") or {}
    if res.get("numero"):
        fecha_res = res.get("fecha") or "—"
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Resolución</span>'
            f'<p>{html.escape(res.get("tipo") or "Exenta")} '
            f'N°{html.escape(str(res["numero"]))} · {html.escape(fecha_res)}</p></div>'
        )

    sesion = e.get("sesion") or {}
    if sesion:
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Sesión del Consejo</span>'
            f'<p>{html.escape(sesion.get("tipo",""))} N°{html.escape(str(sesion.get("numero","")))} '
            f'· {html.escape(sesion.get("fecha","") or "—")}</p></div>'
        )

    bloque_plazos = _render_plazos(e.get("vigencia"))
    if bloque_plazos:
        bloques.append(bloque_plazos)

    modifica = e.get("modifica") or []
    if modifica:
        items = []
        for m in modifica:
            seccion = f' (Sección {html.escape(m["seccion_romana"])})' if m.get("seccion_romana") else ""
            acciones = ", ".join(html.escape(a) for a in m.get("acciones") or []) or "—"
            vig = _vigencia_fmt(m.get("vigencia"))
            items.append(
                f'<li><b>{html.escape(m.get("norma",""))}</b>{seccion} · '
                f'{acciones} · vigencia: {html.escape(vig)}</li>'
            )
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Modificaciones desglosadas</span>'
            f'<ul>{"".join(items)}</ul></div>'
        )

    rans = e.get("ran_referencias") or []
    if rans:
        chips = "".join(f'<span class="chip">{html.escape(r)}</span>' for r in rans)
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Capítulos RAN '
            f'(Recopilación Actualizada de Normas de Bancos)</span>'
            f'<div class="chips">{chips}</div></div>'
        )

    msi = e.get("msi_referencias") or []
    if msi:
        items = "".join(
            f'<li>…{html.escape((m.get("contexto") or "").strip())}…</li>' for m in msi[:3]
        )
        extra = (
            f'<p class="d-extra">+{len(msi)-3} menciones más en el documento.</p>'
            if len(msi) > 3 else ""
        )
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Manual de Sistemas de Información (MSI)</span>'
            f'<ul class="d-msi">{items}</ul>{extra}</div>'
        )

    archivos = e.get("archivos_afectados") or []
    if archivos:
        items = "".join(
            f'<li><span class="chip chip-{html.escape(a.get("accion","")) }">'
            f'{html.escape(a.get("accion","").upper())}</span> '
            f'{html.escape(a.get("nombre",""))}</li>'
            for a in archivos
        )
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Archivos afectados</span>'
            f'<ul class="d-archivos">{items}</ul></div>'
        )

    if not e.get("parsed", False):
        bloques.append(
            '<div class="d-bloque d-warn">'
            # Sin glifo de advertencia: la guía de marca prohíbe el emoji en
            # contexto institucional y U+26A0 se rinde como tal en Windows y
            # Android. El tono de alerta lo da .d-warn, no un símbolo.
            '<span class="d-label">PDF no procesado</span>'
            '<p>El parser no pudo extraer el texto del documento. '
            'Usar el enlace al PDF para revisión manual.</p></div>'
        )

    if not bloques:
        return '<p class="d-vacio">Sin detalles adicionales en el JSON.</p>'

    return '<div class="detalle">' + "".join(bloques) + "</div>"


# Un punto es fin de oración salvo que venga de una abreviatura ("D.L.") o de
# un separador de miles ("N°3.500"), que es donde se cortaba mal: la mención de
# la NCG N°318 quedaba en una "oración" que empezaba en "L. N° 3500 DE 1980, Y
# A LA", sin el verbo que la gobierna.
_FIN_ORACION = re.compile(r"(?<![ .][A-Z])(?<![0-9])\.")


def _inicio_de_oracion(texto: str, pos: int) -> int:
    cortes = [m.end() for m in _FIN_ORACION.finditer(texto, 0, pos)]
    return cortes[-1] if cortes else 0


def _ultimo(patron: re.Pattern, texto: str) -> int | None:
    """Posición del último match, o None. Para saber qué verbo manda."""
    fin = None
    for m in patron.finditer(texto):
        fin = m.start()
    return fin


# Verbos de acción normativa, sobre texto ya normalizado (sin tildes).
#
# Los lookahead excluyen las nominalizaciones que nombran la *materia* y no un
# cambio a la norma: el Oficio Circular N°502 imparte instrucciones sobre "la
# INCORPORACIÓN de bienes raíces habitacionales como inversión representativa,
# SEGÚN NCG N°152", y con `INCORPOR\w*` a secas ese sustantivo hacía pasar por
# modificación lo que es una simple referencia. "MODIFICACIONES" en cambio se
# conserva: "APRUEBA MODIFICACIONES A LA NCG N°209" sí es un cambio.
_VERBO_MODIFICA = re.compile(
    r"MODIFIC\w*|REEMPLAZ\w*|SUSTITU\w*|AJUST\w*|AGREG\w*|ELIMIN\w*"
    r"|ACTUALIZ\w*|POSTERG\w*"
    r"|INCORPORA(?!CION)\w*|INTRODUC(?!CION)\w*|COMPLEMENTA(?!CION)\w*"
)

# `_DEROGA_RE` lista DERÓGASE y DEROGACIÓN con tilde, así que sobre el texto
# normalizado sólo calzaría "DEROGA". Acá hace falta la familia completa.
_VERBO_DEROGA = re.compile(r"DEROG\w*")


def _accion_sobre_norma(entrada: dict, numero: int) -> str:
    """Cómo actúa este documento sobre *esa* norma.

    Tres respuestas posibles y las tres importan:

    - «Derogada por» — la deja sin efecto.
    - «Modificada por» — le cambia el contenido.
    - «Referida por» — sólo la nombra. El Oficio Circular N°502 imparte
      instrucciones "SEGÚN NORMA DE CARÁCTER GENERAL N°152": la invoca, no la
      toca. Llamar a eso una modificación es afirmar un cambio normativo que
      no ocurrió, que es el error más caro que puede cometer este panel.

    Por lo mismo no sirve `_es_derogacion` a secas, que sólo mira si la
    palabra DEROGA aparece en algún lugar de la descripción: un documento que
    modifica la NCG N°152 y de paso deroga una circular la contiene. Tanto la
    derogación como la modificación tienen que estar referidas al número que
    encabeza el grupo, no al documento en general.
    """
    # 1. Lo que dice el parser leyendo el PDF, que es la fuente más confiable.
    #    Las de fuente "descripcion_cmf" que hay guardadas traen la acción
    #    aplicada en bloque: si la descripción dice DEROGA en algún lado,
    #    *todos* sus números quedaron marcados "Derógase". El 2009_0264
    #    modifica la NCG N°152 y deroga el Oficio Circular N°502, y con esas
    #    acciones la 152 aparecía derogada. Se ignoran, igual que en
    #    `_normas_afectadas`.
    for m in entrada.get("modifica") or []:
        if m.get("fuente") == "descripcion_cmf" or m.get("numero_norma") != numero:
            continue
        if any(_DEROGA_RE.search(a or "") for a in m.get("acciones") or []):
            return "Derogada por"
        return "Modificada por"

    # 2. Manda el último verbo que aparece antes de la mención dentro de su
    #    misma oración. Exigir que el verbo esté pegado no sirve, porque la
    #    CMF enumera: "DEROGA CIRCULAR N°1360, NORMA DE CARÁCTER GENERAL N°42
    #    Y OFICIO CIRCULAR N°652" deroga las tres. Y una ventana de N
    #    caracteres tampoco, porque el verbo puede quedar lejos y seguir
    #    rigiendo: "APRUEBA MODIFICACIONES A LA NCG N°209 … Y A LA NCG N°318".
    desc = store.normalizar(entrada.get("descripcion_cmf") or "")
    mencion = rf"(?:NORMAS?\s+DE\s+CARACTER\s+GENERAL|NCG)\s*(?:N[°O]\s*)?0*{numero}\b"
    menciones = list(re.finditer(mencion, desc))
    for m in menciones:
        oracion = desc[_inicio_de_oracion(desc, m.start()):m.start()]
        mod = _ultimo(_VERBO_MODIFICA, oracion)
        der = _ultimo(_VERBO_DEROGA, oracion)
        if der is not None and (mod is None or der > mod):
            return "Derogada por"
        if mod is not None:
            return "Modificada por"

    # 3. Sin mención en la descripción no hay nada que interpretar: la norma
    #    entró al grupo por los datos del PDF, o sea que el documento sí la
    #    afecta. Marcarla "Referida por" por descarte sería negar un cambio.
    return "Referida por" if menciones else "Modificada por"


_DESGLOSE = {
    "Modificada por": ("{n} la modifica", "{n} la modifican"),
    "Derogada por": ("{n} la deroga", "{n} la derogan"),
    "Referida por": ("{n} sólo la menciona", "{n} sólo la mencionan"),
}

# La barra de acento de cada evento dice la acción sin tener que leerla.
_ACCION_CLASE = {
    "Modificada por": "mod",
    "Derogada por": "der",
    "Referida por": "ref",
}


def _desglose_acciones(acciones: list[str]) -> str:
    """«7 la modifican · 1 sólo la menciona».

    Va en el encabezado del grupo y no sólo en cada evento porque con un
    filtro activo los eventos que no calzan quedan ocultos: bajo «Modificación
    NCG» la NCG N°152 mostraba «7 de 8» y sus siete modificaciones, sin manera
    de saber qué era el octavo. La composición describe la historia completa
    de la norma, así que no cambia con el filtro y responde esa pregunta sin
    tener que sacar el filtro.
    """
    partes = []
    for accion, (singular, plural) in _DESGLOSE.items():
        n = acciones.count(accion)
        if n:
            partes.append((singular if n == 1 else plural).format(n=n))
    return " · ".join(partes)


def _render_timeline(grupos: dict[str, list[dict]]) -> str:
    if not grupos:
        return '<p class="tl-vacio">Sin datos de línea de tiempo aún.</p>'
    bloques = []
    for norma, items in grupos.items():
        if len(items) == 0:
            continue
        # `data-clave` es lo que ata cada evento a su fila de la tabla: el filtro
        # decide qué filas se ven y la línea de tiempo se limita a seguirlas, en
        # vez de reimplementar el filtrado por tipo y la búsqueda. Una sola
        # fuente de verdad, y no pueden quedar en desacuerdo.
        # El evento identifica al documento que actuó, no al tipo de acuerdo.
        # Decía «2021-07-30 · Modificación NCG», que dentro de un grupo
        # titulado «NCG N°152» y bajo el filtro «Modificación NCG» repetía dos
        # veces lo que ya se sabía y no decía lo único que falta: cuál norma la
        # modificó. Ahora dice «2021-07-30 · Modificada por NCG N°458».
        m_num = re.search(r"\d+", norma)
        numero = int(m_num.group()) if m_num else -1
        acciones = [_accion_sobre_norma(i, numero) for i in items]
        items_html = "".join(
            f'<a class="tl-item tl-{_ACCION_CLASE[accion]}" '
            f'data-clave="{html.escape(i.get("clave") or "")}" '
            f'href="{html.escape(i.get("url_documento") or "")}" target="_blank" rel="noopener" '
            f'title="{html.escape((i.get("descripcion_cmf") or "")[:200])}">'
            f'<b>{html.escape(i.get("fecha","?"))}</b> · '
            f'{html.escape(accion)} '
            f'<span class="tl-actor">{html.escape(_etiqueta_documento(i))}</span>'
            f'</a>'
            for i, accion in zip(items, acciones)
        )
        count = len(items)
        bloques.append(
            f'<div class="tl-norma">'
            f'<h3>{html.escape(norma)} '
            f'<span class="tl-count" data-total="{count}">'
            f'{count} evento{"s" if count!=1 else ""}</span>'
            f'<span class="tl-desglose">{html.escape(_desglose_acciones(acciones))}</span>'
            f'</h3>'
            f'<div class="tl-items">{items_html}</div>'
            f'</div>'
        )
    return "\n".join(bloques)


# ── Template HTML ───────────────────────────────────────────────────────

_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Monitoreo normativo CMF</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap">
  <style>
    /* ═══ CMF Design System — tokens ═══════════════════════════════════
       Copiados textualmente de clauditapalma-prog/CMF-Design-System@c94ed2d
       (cmf-design-system-8adfb247-…/tokens/): colors.css, typography.css,
       spacing.css y base.css, en el orden que fija su styles.css.

       Van EMBEBIDOS y no enlazados a propósito: el dashboard se publica
       como un único HTML sin paso de build, así que un archivo de assets
       que no llegue al commit dejaría la página entera sin estilos —y como
       los tokens definen hasta el color del texto, se vería como si el
       generador se hubiera roto. Para resincronizar con upstream, cambia
       este bloque completo; no lo edites a mano.

       tokens/fonts.css es la única excepción: era un @import a Google
       Fonts, que como <link> en el <head> carga antes y no bloquea el
       resto del CSS. Trae sólo Source Sans 3 (--font-sans); JetBrains
       Mono se omite porque el único uso de monoespaciada acá es un <code>
       y --font-mono ya cae a la del sistema.
       ═════════════════════════════════════════════════════════════════ */

    /* ---- tokens/colors.css ---- */
    :root {
      /* Brand purple (primary identity) */
      --cmf-purple-900: #3a1a53;
      --cmf-purple-800: #4a2169;
      --cmf-purple-700: #5b2b82;   /* Pantone 268 C — primary brand */
      --cmf-purple-500: #8547ad;   /* Pantone 2587 C — secondary purple */
      --cmf-purple-300: #b68fd0;
      --cmf-purple-200: #d8c4e6;
      --cmf-purple-100: #ece1f3;
      --cmf-purple-50:  #f6f1fa;
      /* Neutral / ink (Pantone 424 C family) */
      --cmf-ink-900: #2c2c2b;
      --cmf-ink-800: #444444;
      --cmf-ink-700: #575756;
      --cmf-ink-500: #717271;      /* Pantone 424 C */
      --cmf-ink-400: #969695;
      --cmf-ink-300: #bcbcbb;
      --cmf-ink-200: #d7d7d6;
      --cmf-ink-100: #e9e9e8;
      --cmf-ink-50:  #f5f5f4;
      --cmf-white:   #ffffff;
      /* Graphic-support / accent palette */
      --cmf-navy:       #162c55;
      --cmf-indigo:     #3f3a7e;
      --cmf-teal:       #12a095;
      --cmf-teal-deep:  #0e6e68;
      --cmf-teal-200:   #97d6d2;
      --cmf-teal-50:    #e4f4f2;
      /* Functional / status */
      --cmf-success:    #1e8a5b;
      --cmf-success-bg: #e6f3ec;
      --cmf-warning:    #b97708;
      --cmf-warning-bg: #fbf0dc;
      --cmf-danger:     #c0392b;
      --cmf-danger-bg:  #f9e7e4;
      --cmf-info:       #162c55;
      --cmf-info-bg:    #e7ecf4;
      /* Semantic aliases — usar estos en los componentes */
      --color-brand:            var(--cmf-purple-700);
      --color-brand-strong:     var(--cmf-purple-800);
      --color-brand-soft:       var(--cmf-purple-500);
      --color-brand-tint:       var(--cmf-purple-100);
      --color-brand-tint-faint: var(--cmf-purple-50);
      --color-accent:           var(--cmf-teal);
      --color-accent-deep:      var(--cmf-teal-deep);
      --color-accent-tint:      var(--cmf-teal-50);
      --text-strong:    var(--cmf-ink-900);
      --text-body:      var(--cmf-ink-700);
      --text-muted:     var(--cmf-ink-500);
      --text-faint:     var(--cmf-ink-400);
      --text-on-brand:  var(--cmf-white);
      --text-link:      var(--cmf-purple-700);
      --text-link-hover:var(--cmf-purple-800);
      --surface-page:    var(--cmf-ink-50);
      --surface-card:    var(--cmf-white);
      --surface-sunken:  var(--cmf-ink-100);
      --surface-brand:   var(--cmf-purple-700);
      --surface-navy:    var(--cmf-navy);
      --surface-inverse: var(--cmf-navy);
      --border-subtle:  var(--cmf-ink-200);
      --border-default: var(--cmf-ink-300);
      --border-strong:  var(--cmf-ink-500);
      --border-brand:   var(--cmf-purple-700);
      --focus-ring: var(--cmf-purple-500);
    }

    /* ---- tokens/typography.css ---- */
    :root {
      --font-brand: "Verdana", "Source Sans 3", system-ui, sans-serif;
      --font-sans:  "Source Sans 3", "Verdana", system-ui, -apple-system, "Segoe UI", sans-serif;
      --font-mono:  "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
      --fw-regular: 400;
      --fw-medium:  500;
      --fw-semibold:600;
      --fw-bold:    700;
      --fs-display: 3.052rem;
      --fs-h1:      2.441rem;
      --fs-h2:      1.953rem;
      --fs-h3:      1.563rem;
      --fs-h4:      1.25rem;
      --fs-lg:      1.125rem;
      --fs-body:    1rem;
      --fs-sm:      0.875rem;
      --fs-xs:      0.75rem;
      --lh-tight:   1.15;
      --lh-snug:    1.3;
      --lh-normal:  1.5;
      --lh-relaxed: 1.65;
      --ls-tight:   -0.01em;
      --ls-normal:  0;
      --ls-wide:    0.04em;
      --ls-caps:    0.08em;
    }

    /* ---- tokens/spacing.css ---- */
    :root {
      --space-0: 0;
      --space-1: 0.25rem;
      --space-2: 0.5rem;
      --space-3: 0.75rem;
      --space-4: 1rem;
      --space-5: 1.5rem;
      --space-6: 2rem;
      --space-7: 3rem;
      --space-8: 4rem;
      --space-9: 6rem;
      --radius-xs: 3px;
      --radius-sm: 5px;
      --radius-md: 8px;
      --radius-lg: 12px;
      --radius-pill: 999px;
      --border-w: 1px;
      --border-w-thick: 2px;
      --accent-bar-w: 4px;
      --shadow-xs: 0 1px 2px rgba(44, 44, 43, 0.06);
      --shadow-sm: 0 1px 3px rgba(44, 44, 43, 0.08), 0 1px 2px rgba(44, 44, 43, 0.06);
      --shadow-md: 0 4px 12px rgba(44, 44, 43, 0.10), 0 2px 4px rgba(44, 44, 43, 0.06);
      --shadow-lg: 0 12px 28px rgba(44, 44, 43, 0.14), 0 4px 8px rgba(44, 44, 43, 0.06);
      --shadow-brand: 0 8px 24px rgba(91, 43, 130, 0.22);
      --container-max: 1200px;
      --container-narrow: 760px;
      --header-h: 72px;
      --ease-standard: cubic-bezier(0.2, 0, 0.2, 1);
      --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
      --dur-fast: 120ms;
      --dur-base: 200ms;
      --dur-slow: 320ms;
    }

    /* ---- tokens/base.css ---- */
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--font-sans);
      font-size: var(--fs-body);
      line-height: var(--lh-normal);
      color: var(--text-body);
      background: var(--surface-page);
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }
    h1, h2, h3, h4 {
      font-family: var(--font-sans);
      color: var(--text-strong);
      line-height: var(--lh-tight);
      letter-spacing: var(--ls-tight);
      margin: 0 0 var(--space-4);
      font-weight: var(--fw-bold);
    }
    h1 { font-size: var(--fs-h1); }
    h2 { font-size: var(--fs-h2); }
    h3 { font-size: var(--fs-h3); }
    h4 { font-size: var(--fs-h4); font-weight: var(--fw-semibold); }
    p { margin: 0 0 var(--space-4); text-wrap: pretty; }
    a {
      color: var(--text-link);
      text-decoration: none;
      transition: color var(--dur-fast) var(--ease-standard);
    }
    a:hover { color: var(--text-link-hover); text-decoration: underline; }
    :focus-visible {
      outline: var(--border-w-thick) solid var(--focus-ring);
      outline-offset: 2px;
      border-radius: var(--radius-xs);
    }
    /* Regla de acento — la barra morada corta bajo el logo / los títulos */
    .cmf-rule {
      height: var(--accent-bar-w);
      width: 56px;
      background: var(--color-brand);
      border: 0;
      border-radius: var(--radius-pill);
      margin: var(--space-3) 0;
    }

    /* ═══ Aplicación al dashboard ══════════════════════════════════════
       De acá abajo es CSS propio. Los componentes del sistema (Button,
       Card, Badge, Alert, Input, Tabs) vienen del bundle React de upstream
       y no se pueden montar en esta página —no hay React ni bundler—, así
       que su especificación visual está portada a los selectores que ya
       emiten las funciones _render_*. Es fiel porque esos componentes
       estilan sólo con los tokens de arriba: no traen CSS propio.
       Ningún valor de acá debería ser un hex literal; si necesitas uno,
       falta un token.
       ═════════════════════════════════════════════════════════════════ */

    /* Tintas de las variantes «subtle» de Badge y Alert. Upstream las
       hardcodea dentro de los componentes y no las expone en colors.css,
       así que se declaran acá con exactamente el mismo valor. Importan:
       el color sólido sobre su propio fondo tintado no llega a AA
       (success 3,8:1 · warning 3,3:1), estas tintas sí. */
    :root {
      --ink-on-success-bg: #13643f;
      --ink-on-warning-bg: #8a5905;
      --ink-on-danger-bg:  #922a1f;
    }

    /* base.css asume una página de prosa: h1–h4 y p traen margen inferior y
       la escala de titulares parte en 39px. Este dashboard es una UI densa
       de tablas y tarjetas, así que se neutraliza el flujo del documento y
       cada componente declara su propio tamaño y espaciado. */
    h1, h2, h3, h4, p, ul, ol, figure { margin: 0; }
    ul, ol { padding: 0; }
    button { font-family: var(--font-sans); }

    /* Cabecera — tratamiento de portada navy del sistema. La banda oficial
       usa la textura de red (assets/backgrounds/cmf-network-texture.jpeg),
       que no está publicada en el repo del sistema, así que va navy plano. */
    /* Va como `body > header` y no como `header` a secas porque el aviso de
       revisión manual también abre un <header>: sin acotar, quedaba con fondo
       navy. */
    body > header { background: var(--surface-navy);
                    padding: var(--space-7) var(--space-6); }
    .hd-inner { max-width: var(--container-max); margin: 0 auto; }
    /* Logo oficial, en la forma «blanco total» que el manual permite para
       fondo oscuro. Va inline y no como <img> por dos razones: la página se
       publica como un único HTML —un archivo suelto que no llegue al commit
       deja la cabecera rota— y así el blanco lo hereda de `color`, sin
       tocar la geometría ni recolorear a mano trazado por trazado.
       El SVG viene de cmfchile.cl con los rellenos originales #52307E y
       #6D4C95 (isotipo) y #737373 (logotipo), sustituidos por currentColor;
       los stroke-width se conservan, que son los que dan el grosor de las
       letras. Restituir la versión a color = devolver esos tres valores.

       El manual pide un margen de protección ≈ la altura de la «F», que en
       este trazado mide 35,89 de las 37,59 unidades del viewBox, o sea casi
       el alto completo: con 34px de logo son ~32px libres, que es lo que dan
       el padding del header y el margen inferior. */
    .hd-logo { color: var(--cmf-white); margin-bottom: var(--space-6);
               line-height: 0; }
    .hd-logo svg { height: 34px; width: auto; max-width: 100%; display: block; }
    body > header h1 { font-size: var(--fs-h3); font-weight: var(--fw-bold);
                       color: var(--cmf-white); }
    /* La regla de acento es morada sobre fondo claro, como manda el manual,
       pero el morado 2587 C sobre navy da ~2:1 y desaparece; sobre oscuro va
       en el teal de la paleta de apoyo. */
    body > header .cmf-rule { background: var(--color-accent); }
    .hd-sub { color: var(--cmf-ink-200); font-size: var(--fs-sm);
              max-width: 78ch; }

    main { max-width: var(--container-max); margin: var(--space-5) auto;
           padding: 0 var(--space-4); }
    /* Card */
    section { background: var(--surface-card);
              border: var(--border-w) solid var(--border-subtle);
              border-radius: var(--radius-md); box-shadow: var(--shadow-sm);
              margin-bottom: var(--space-5); overflow: hidden; }
    section h2 { font-size: var(--fs-body); font-weight: var(--fw-semibold);
                 padding: var(--space-3) var(--space-5);
                 border-bottom: var(--border-w) solid var(--border-subtle);
                 background: var(--cmf-ink-50);
                 display: flex; align-items: center; justify-content: space-between;
                 letter-spacing: var(--ls-normal); }
    .h2-hint { font-size: var(--fs-xs); color: var(--text-muted);
               font-weight: var(--fw-regular); }

    /* Tabs */
    /* La barra de pestañas scrollea cuando no cabe, y es todo el mecanismo de
       navegación del dashboard: sin una señal de que sigue hacia el lado, quien
       no arrastra nunca se entera de que existe «Listado completo». Mismo
       recurso que el riel del calendario —difuminado en los bordes, encendido
       sólo cuando de verdad hay más— y encima un chevrón, porque acá lo que
       queda fuera de cuadro no es contenido sino secciones enteras.

       Las clases las pone `bordesTabs()` midiendo el overflow real, así que en
       escritorio, donde las cuatro caben, no aparece nada. El borde inferior y
       el margen se mudan al envoltorio: en el elemento que scrollea, el borde
       se corta al ancho visible en vez de acompañar a la barra. */
    #tabs-outer { position: relative; margin-bottom: var(--space-5);
                  border-bottom: var(--border-w) solid var(--border-subtle); }
    /* Los chevrones son <button> y no ::before/::after. Como pseudo-elementos
       llevaban `pointer-events: none` —obligatorio, si no tapan las pestañas de
       abajo— y el resultado era un control que se ve como control y no hace
       nada al tocarlo. El degradado viaja en el botón, así que un solo elemento
       señala y opera. */
    .tabs-nav { position: absolute; top: 0; bottom: 0; width: 40px; z-index: 2;
                display: flex; align-items: center; border: 0; padding: 0;
                font: inherit; font-size: 19px; line-height: 1;
                color: var(--color-brand); cursor: pointer;
                /* `visibility` y no sólo `opacity`: un botón transparente sigue
                   siendo enfocable y anunciado por un lector de pantalla, así
                   que en escritorio —donde las cuatro pestañas caben y estos
                   nunca aparecen— el tabulador caía en dos controles
                   invisibles. `visibility: hidden` los saca del orden de foco y
                   del árbol de accesibilidad. El delay difiere el ocultamiento
                   hasta que termina el fundido; al aparecer es inmediato. */
                opacity: 0; visibility: hidden; pointer-events: none;
                transition: opacity var(--dur-base) var(--ease-standard),
                            visibility 0s linear var(--dur-base); }
    #tabs-izq { left: 0; justify-content: flex-start;
                background: linear-gradient(90deg, var(--surface-page) 45%, transparent); }
    #tabs-der { right: 0; justify-content: flex-end;
                background: linear-gradient(270deg, var(--surface-page) 45%, transparent); }
    #tabs-outer.puede-izq #tabs-izq,
    #tabs-outer.puede-der #tabs-der { opacity: 1; visibility: visible;
                pointer-events: auto; transition-delay: 0s; }
    .tabs-nav:focus-visible { outline: 2px solid var(--color-brand);
                outline-offset: -2px; }
    /* `scroll-padding-inline` deja libre el ancho del chevrón cuando algo se
       trae a la vista con scrollIntoView. Sin esto la pestaña activa quedaba
       justo debajo del botón y se leía a medias («mbios relevantes»). */
    #tabs { display: flex; gap: var(--space-5); overflow-x: auto;
            flex-wrap: nowrap; scrollbar-width: none;
            scroll-padding-inline: 44px; }
    #tabs::-webkit-scrollbar { display: none; }
    .tab { background: transparent; border: 0; cursor: pointer;
           padding: var(--space-3) 0; font-size: var(--fs-body);
           font-weight: var(--fw-medium); color: var(--text-muted);
           border-bottom: 3px solid transparent;
           transition: color var(--dur-fast) var(--ease-standard),
                       border-color var(--dur-base) var(--ease-out); }
    .tab:hover { color: var(--text-strong); }
    .tab.activo { color: var(--color-brand); border-bottom-color: var(--color-brand);
                  font-weight: var(--fw-bold); }
    .tab-panel { animation: fadeIn var(--dur-fast) var(--ease-standard); }
    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    /* «Animación discreta y funcional… respeta prefers-reduced-motion» */
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important; transition-duration: 0.01ms !important; }
    }

    /* ── Agenda de tareas ──────────────────────────────────────────────
       Los pasos de dato son los de la rampa CMF, pero un escalón más claro
       que los de marca: el morado 700 no pasa la banda de luminosidad para
       rellenos sobre superficie clara. */
    #agenda { --ag-dato-1: var(--cmf-purple-500);
              --ag-dato-2: var(--cmf-teal);
              --ag-dato-3: var(--cmf-warning);
              --ag-dato-pasado: var(--cmf-ink-300);
              --ag-grid: rgba(44,44,43,.10); }
    .ag-num { font-variant-numeric: tabular-nums; }
    .ag-de { color: var(--text-faint); }
    .ag-vacio { font-size: var(--fs-xs); color: var(--text-muted); font-style: italic; }
    .ag-regla { display: block; width: 22px; height: 1px; background: var(--border-default); }

    /* Buscador */
    /* `.ag-buscador` es un <section>, así que la regla global le da fondo
       blanco, borde y esquinas redondeadas — pero el padding no viene con el
       lote, y sin él la caja de búsqueda y los ejemplos quedaban pegados al
       borde de su propia tarjeta. Los otros bloques de la agenda ya lo traen
       (`.ag-panel`, `.ag-stat`); este era el único sin él. */
    .ag-buscador { margin-bottom: var(--space-5); padding: var(--space-4); }
    .ag-caja { display: flex; align-items: center; gap: var(--space-2);
               background: var(--surface-card);
               border: var(--border-w) solid var(--border-default);
               border-radius: var(--radius-md); padding: 0 var(--space-3);
               transition: border-color var(--dur-fast) var(--ease-standard); }
    .ag-caja:focus-within { border-color: var(--color-brand);
                            box-shadow: 0 0 0 3px var(--color-brand-tint); }
    .ag-caja svg { flex: none; color: var(--text-faint); }
    .ag-caja input { flex: 1; border: 0; background: none; font: inherit;
                     font-size: var(--fs-body); color: var(--text-strong);
                     padding: 11px 0; outline: none; min-width: 0; }
    .ag-x { border: 0; background: none; color: var(--text-faint); cursor: pointer;
            font-size: var(--fs-lg); line-height: 1; padding: 4px; }
    .ag-x:hover { color: var(--color-brand); }
    .ag-ejemplos { display: flex; flex-wrap: wrap; gap: var(--space-1);
                   align-items: center; margin-top: var(--space-2);
                   font-size: var(--fs-xs); color: var(--text-faint); }
    .ag-ejemplos button { border: var(--border-w) solid var(--border-subtle);
                          background: var(--surface-card); color: var(--text-muted);
                          font: inherit; font-size: var(--fs-xs);
                          font-weight: var(--fw-semibold); padding: 2px 9px;
                          border-radius: var(--radius-pill); cursor: pointer;
                          font-variant-numeric: tabular-nums; }
    .ag-ejemplos button:hover { border-color: var(--color-brand); color: var(--color-brand); }
    .ag-respuesta { margin-top: var(--space-3); }
    .ag-frase { font-size: var(--fs-body); color: var(--text-strong);
                line-height: var(--lh-normal); padding: var(--space-3) var(--space-4);
                border-radius: var(--radius-md); background: var(--color-brand-tint-faint);
                border-left: var(--accent-bar-w) solid var(--color-brand); margin: 0; }
    .ag-frase.es-nada { background: var(--surface-sunken); color: var(--text-muted);
                        border-left-color: var(--border-default); }
    .ag-frase b { color: var(--color-brand-strong); }
    .ag-hits { margin-top: var(--space-2);
               border: var(--border-w) solid var(--border-subtle);
               border-radius: var(--radius-md); overflow: hidden;
               display: grid; gap: 1px; background: var(--border-subtle); }
    .ag-hit { background: var(--surface-card); padding: var(--space-3) var(--space-4);
              display: grid; grid-template-columns: 1fr auto; gap: 4px var(--space-4);
              align-items: baseline; }
    .ag-hit-norma { font-size: var(--fs-sm); font-weight: var(--fw-bold);
                    color: var(--text-strong); }
    .ag-hit-norma a { color: inherit; }
    .ag-hit-meta { font-size: var(--fs-xs); color: var(--text-muted);
                   font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
    .ag-hit-desc, .ag-hit-chips { grid-column: 1 / -1; }
    .ag-hit-desc { font-size: var(--fs-xs); color: var(--text-muted);
                   line-height: var(--lh-normal); }
    .ag-hit-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 3px; }
    .ag-hit mark { background: var(--cmf-warning-bg); color: inherit;
                   border-radius: 2px; padding: 0 2px; }
    .ag-hits-mas { background: var(--surface-sunken); padding: var(--space-2) var(--space-4);
                   font-size: var(--fs-xs); color: var(--text-muted); }
    .ag-estado { font-size: 10px; font-weight: var(--fw-bold);
                 letter-spacing: var(--ls-wide); padding: 1px 7px;
                 border-radius: var(--radius-pill); white-space: nowrap; }
    .ag-estado.es-futuro { background: var(--color-brand-tint); color: var(--color-brand-strong); }
    .ag-estado.es-pasado { background: var(--surface-sunken); color: var(--text-muted); }
    .ag-estado.es-sinfecha { background: var(--cmf-warning-bg); color: var(--ink-on-warning-bg); }
    .ag-estado.es-sinvigencia { background: var(--surface-sunken); color: var(--text-faint); }

    /* Cifras */
    .ag-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 1px; background: var(--border-subtle);
                border: var(--border-w) solid var(--border-subtle);
                border-radius: var(--radius-md); overflow: hidden;
                margin-bottom: var(--space-5); }
    .ag-stat { background: var(--surface-card); padding: var(--space-3) var(--space-4); }
    .ag-stat b { display: block; font-size: var(--fs-h2); font-weight: var(--fw-regular);
                 color: var(--text-strong); line-height: var(--lh-tight); }
    /* Una celda con ayuda tiene que verse como que la tiene: este total y el
       contador del tab «Revisión manual» cuentan conjuntos distintos, y sin
       una señal de que hay explicación el tooltip no lo aclara nunca porque
       nadie lo busca. */
    .ag-stat.es-conayuda { cursor: help; }
    .ag-stat.es-conayuda span { text-decoration: underline dotted;
                                text-underline-offset: 2px; }
    .ag-stat span { display: block; font-size: var(--fs-xs); color: var(--text-muted);
                    margin-top: 4px; }
    .ag-stat.es-clave b { color: var(--color-brand); }

    /* Paneles */
    .ag-paneles { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                  gap: var(--space-4); margin-bottom: var(--space-6); }
    .ag-panel { background: var(--surface-card);
                border: var(--border-w) solid var(--border-subtle);
                border-radius: var(--radius-lg); padding: var(--space-5);
                box-shadow: var(--shadow-xs); min-width: 0; }
    .ag-panel h3 { margin: 0; font-size: var(--fs-sm); font-weight: var(--fw-semibold); }
    .ag-panel .ag-sub { font-size: var(--fs-xs); color: var(--text-muted);
                        margin: 4px 0 var(--space-4); line-height: var(--lh-normal); }
    .ag-panel-head { display: flex; align-items: center; justify-content: space-between;
                     gap: var(--space-3); }
    .ag-switch { display: inline-flex; background: var(--surface-sunken);
                 border-radius: var(--radius-pill); padding: 2px; }
    .ag-switch button { border: 0; background: none; font: inherit; font-size: var(--fs-xs);
                        font-weight: var(--fw-semibold); color: var(--text-muted);
                        padding: 3px 10px; border-radius: var(--radius-pill); cursor: pointer; }
    .ag-switch button[aria-selected="true"] { background: var(--surface-card);
                        color: var(--color-brand); box-shadow: var(--shadow-xs); }

    .ag-hbar { display: grid; grid-template-columns: 86px 1fr 28px; align-items: center;
               gap: var(--space-2); margin-bottom: 7px; font-size: var(--fs-xs);
               width: 100%; border: 0; background: none; padding: 0; font-family: inherit;
               text-align: left; cursor: pointer; }
    .ag-hbar:disabled { cursor: default; }
    .ag-hbar-lbl { color: var(--text-body); text-align: right; overflow: hidden;
                   text-overflow: ellipsis; white-space: nowrap; }
    .ag-hbar-track { background: var(--surface-sunken); border-radius: var(--radius-pill);
                     height: 14px; }
    .ag-hbar-fill { display: block; height: 100%; border-radius: var(--radius-pill);
                    background: var(--ag-fill, var(--ag-dato-1)); }
    .ag-hbar:hover:not(:disabled) .ag-hbar-lbl { color: var(--color-brand); }
    .ag-hbar-val { font-variant-numeric: tabular-nums; color: var(--text-muted);
                   font-weight: var(--fw-semibold); }
    .ag-hbar.es-off { opacity: .35; }
    /* «Otros» es el residuo de la clasificación, no un cuerpo normativo: va
       rayado para que no se lea como la categoría más activa. */
    .ag-hbar.es-residual .ag-hbar-fill {
        background: repeating-linear-gradient(135deg, var(--ag-dato-pasado) 0 5px,
                                              transparent 5px 10px);
        border: var(--border-w) solid var(--ag-dato-pasado); }
    .ag-hbar.es-residual .ag-hbar-lbl { color: var(--text-faint); font-style: italic; }

    .ag-cols { display: flex; align-items: flex-end; gap: 2px; height: 132px;
               border-bottom: var(--border-w) solid var(--border-default);
               position: relative; margin-bottom: 6px; }
    .ag-cols::before, .ag-cols::after { content: ""; position: absolute;
               left: 0; right: 0; height: 1px; background: var(--ag-grid); }
    .ag-cols::before { top: 33%; } .ag-cols::after { top: 66%; }
    .ag-col { flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
              align-items: center; height: 100%; position: relative; min-width: 0; }
    .ag-col-bar { width: 100%; background: var(--ag-dato-pasado); border-radius: 4px 4px 0 0; }
    .ag-col.es-futuro .ag-col-bar { background: var(--ag-dato-1); }
    .ag-col.es-cero .ag-col-bar { height: 2px !important; background: var(--border-default);
                                  border-radius: 0; opacity: .7; }
    .ag-col-n { font-size: 10px; font-variant-numeric: tabular-nums;
                color: var(--text-muted); margin-bottom: 3px; font-weight: var(--fw-semibold); }
    .ag-col.es-cero .ag-col-n { color: var(--text-faint); font-weight: var(--fw-regular); }
    .ag-seam { position: absolute; top: -4px; bottom: -1px; width: 2px;
               background: var(--color-brand); opacity: .55; }
    .ag-cols-x { display: flex; gap: 2px; }
    .ag-col-x { flex: 1; text-align: center; font-size: 9px; color: var(--text-faint);
                text-transform: uppercase; letter-spacing: var(--ls-wide); }
    .ag-col-x.es-hoy { color: var(--color-brand); font-weight: var(--fw-bold); }
    .ag-legend { display: flex; flex-wrap: wrap; gap: var(--space-3);
                 margin-top: var(--space-3); font-size: var(--fs-xs); color: var(--text-muted); }
    .ag-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px;
                   margin-right: 5px; vertical-align: -1px; }

    /* Panel de obligaciones sin fecha: el punto ciego del eje temporal. */
    .ag-panel.es-alerta { border-color: var(--cmf-warning);
                          background: linear-gradient(180deg, var(--cmf-warning-bg) 0 60px,
                                                       var(--surface-card) 60px); }
    .ag-sf-total { display: flex; align-items: baseline; gap: var(--space-2);
                   margin-bottom: var(--space-4); }
    .ag-sf-total b { font-size: var(--fs-h1); line-height: 1;
                     color: var(--ink-on-warning-bg); font-weight: var(--fw-regular); }
    .ag-sf-total span { font-size: var(--fs-xs); color: var(--text-body); }
    .ag-sf-fila { margin-bottom: var(--space-3); }
    .ag-sf-top { display: flex; justify-content: space-between; align-items: baseline;
                 font-size: var(--fs-xs); margin-bottom: 5px; color: var(--text-muted); }
    .ag-sf-top b { color: var(--text-strong); font-variant-numeric: tabular-nums; }
    .ag-sf-track { height: 8px; background: var(--surface-sunken);
                   border-radius: var(--radius-pill); }
    .ag-sf-fill { height: 100%; border-radius: var(--radius-pill); background: var(--ag-dato-3); }
    .ag-sf-fill.es-alt { background: repeating-linear-gradient(135deg,
                         var(--ag-dato-3) 0 6px, var(--cmf-warning-bg) 6px 12px); }
    .ag-sf-pie { font-size: var(--fs-xs); color: var(--text-muted);
                 line-height: var(--lh-normal);
                 border-top: var(--border-w) solid var(--border-subtle);
                 padding-top: var(--space-3); margin: var(--space-4) 0 0; }
    .ag-datos summary { font-size: var(--fs-xs); color: var(--text-muted); cursor: pointer; }
    .ag-datos summary:hover { color: var(--color-brand); }
    .ag-sf-lista { margin-top: var(--space-2); max-height: 210px; overflow-y: auto;
                   display: grid; gap: 1px; background: var(--border-subtle);
                   border: var(--border-w) solid var(--border-subtle);
                   border-radius: var(--radius-sm); }
    .ag-sf-item { background: var(--surface-card); padding: 7px 10px; font-size: var(--fs-xs); }
    .ag-sf-item b { display: block; color: var(--text-strong); }
    .ag-sf-item span { color: var(--text-muted); display: block; overflow: hidden;
                       text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
    .ag-sf-item em { font-style: normal; color: var(--text-faint); font-size: 10px; }

    /* Riel de calendario */
    .ag-sec-head { display: flex; align-items: baseline; justify-content: space-between;
                   gap: var(--space-4); flex-wrap: wrap; margin-bottom: var(--space-3); }
    .ag-sec-head h2 { margin: 0; font-size: var(--fs-h3); font-weight: var(--fw-semibold); }
    .ag-hint { font-size: var(--fs-xs); color: var(--text-muted); }
    .ag-riel-ctrl { display: inline-flex; align-items: center; gap: 4px; }
    .ag-riel-ctrl .ag-hint { margin-right: 6px; }
    .ag-riel-ctrl button { border: var(--border-w) solid var(--border-default);
                           background: var(--surface-card); color: var(--text-muted);
                           font: inherit; font-size: var(--fs-sm);
                           font-weight: var(--fw-semibold); cursor: pointer; height: 28px;
                           min-width: 28px; padding: 0 9px; border-radius: var(--radius-sm); }
    .ag-riel-ctrl button:hover:not(:disabled) { border-color: var(--color-brand);
                           color: var(--color-brand); }
    .ag-riel-ctrl button:disabled { opacity: .4; cursor: default; }
    .ag-riel-ctrl .es-hoy { color: var(--color-brand); border-color: var(--color-brand-soft); }
    .ag-nota-riel { font-size: var(--fs-xs); color: var(--text-muted);
                    margin-bottom: var(--space-3); }
    .ag-nota-riel b { color: var(--ink-on-warning-bg); font-variant-numeric: tabular-nums; }

    /* Último cambio publicado. Va entre los paneles y el riel porque contesta
       una pregunta distinta —qué salió recién— y el riel arranca centrado en
       hoy, o sea con la vista puesta en lo que viene. */
    .ag-ultimo { margin-bottom: var(--space-5); }
    .ag-ult-card { background: var(--surface-card);
                   border: var(--border-w) solid var(--border-subtle);
                   border-left: var(--accent-bar-w) solid var(--color-brand);
                   border-radius: var(--radius-md); box-shadow: var(--shadow-xs);
                   padding: var(--space-4) var(--space-5); }
    .ag-ult-top { display: flex; align-items: center; gap: var(--space-3);
                  flex-wrap: wrap; margin-bottom: var(--space-2); }
    .ag-ult-fecha { font-size: var(--fs-xs); color: var(--text-muted);
                    font-variant-numeric: tabular-nums; }
    .ag-ult-doc { font-size: var(--fs-body); font-weight: var(--fw-bold);
                  color: var(--text-strong); }
    .ag-ult-tags { display: inline-flex; flex-wrap: wrap; gap: 2px; }
    .ag-ult-tema { font-size: var(--fs-sm); line-height: var(--lh-normal);
                   color: var(--text-strong); margin: 0 0 var(--space-3); }
    .ag-ult-meta { display: flex; flex-wrap: wrap; gap: var(--space-5);
                   margin-bottom: var(--space-3); }
    .ag-ult-dato span { display: block; font-size: var(--fs-xs);
                        text-transform: uppercase; letter-spacing: var(--ls-caps);
                        color: var(--text-muted); margin-bottom: 2px; }
    .ag-ult-dato b { font-size: var(--fs-sm); font-weight: var(--fw-semibold);
                     color: var(--color-brand); }
    .ag-ult-acciones { display: flex; align-items: center; gap: var(--space-2);
                       flex-wrap: wrap; }
    .ag-ult-ir { border: 0; background: none; color: var(--text-link);
                 font: inherit; font-size: var(--fs-xs);
                 font-weight: var(--fw-semibold); cursor: pointer; padding: 6px 2px; }
    .ag-ult-ir:hover { text-decoration: underline; }
    .ag-ult-pdf { font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                  margin-left: auto; }
    /* `_render_detalle` trae su propia grilla de dos columnas; acá sólo hay que
       darle el respiro que en el listado le da `tr.detail-row > td`. */
    .ag-ult-detalle { margin-top: var(--space-4);
                      padding-top: var(--space-4);
                      border-top: var(--border-w) solid var(--border-subtle); }
    .ag-ult-detalle[hidden] { display: none; }
    .ag-filtro { display: flex; align-items: center; gap: var(--space-2);
                 font-size: var(--fs-xs); margin-bottom: var(--space-3); min-height: 26px;
                 color: var(--text-muted); }
    .ag-filtro-pill { display: inline-flex; align-items: center; gap: 7px;
                      background: var(--color-brand-tint); color: var(--color-brand-strong);
                      border-radius: var(--radius-pill); padding: 3px 6px 3px 12px;
                      font-weight: var(--fw-semibold); }
    .ag-filtro-pill button { border: 0; background: var(--color-brand);
                      color: var(--text-on-brand); width: 16px; height: 16px;
                      border-radius: 50%; cursor: pointer; font-size: var(--fs-xs);
                      line-height: 1; padding: 0; font-family: inherit; }

    .ag-riel-outer { background: var(--surface-sunken);
                     border: var(--border-w) solid var(--border-subtle);
                     border-radius: var(--radius-lg); padding: var(--space-4) 0 6px;
                     margin-bottom: var(--space-6); position: relative; }
    /* Difuminado en los bordes: dice "hay más hacia allá" sin ocupar espacio.
       Se apaga al llegar al extremo, para no prometer contenido que no hay. */
    .ag-riel-outer::before, .ag-riel-outer::after { content: ""; position: absolute;
                     top: 1px; bottom: 14px; width: 44px; pointer-events: none; z-index: 2;
                     opacity: 0; transition: opacity var(--dur-base) var(--ease-standard); }
    .ag-riel-outer::before { left: 1px;
                     background: linear-gradient(90deg, var(--surface-sunken) 25%, transparent); }
    .ag-riel-outer::after { right: 1px;
                     background: linear-gradient(270deg, var(--surface-sunken) 25%, transparent); }
    .ag-riel-outer.puede-izq::before, .ag-riel-outer.puede-der::after { opacity: 1; }
    .ag-riel { display: flex; gap: var(--space-3); overflow-x: auto;
               padding: 0 var(--space-4) var(--space-4); align-items: stretch;
               scroll-snap-type: x proximity; }
    .ag-mes { flex: 0 0 232px; scroll-snap-align: center; background: var(--surface-card);
              border: var(--border-w) solid var(--border-subtle);
              border-radius: var(--radius-md); display: flex; flex-direction: column;
              overflow: hidden; }
    .ag-mes-head { padding: 11px var(--space-3) 9px;
                   border-bottom: var(--border-w) solid var(--border-subtle);
                   display: flex; align-items: baseline; justify-content: space-between;
                   gap: var(--space-2); }
    .ag-mes-nom { font-size: var(--fs-sm); color: var(--text-strong);
                  text-transform: capitalize; }
    .ag-mes-nom small { font-size: var(--fs-xs); color: var(--text-faint); margin-left: 5px; }
    .ag-mes-n { font-size: var(--fs-xs); font-weight: var(--fw-bold); color: var(--text-muted);
                font-variant-numeric: tabular-nums; background: var(--surface-sunken);
                border-radius: var(--radius-pill); padding: 2px 8px; }
    .ag-mes.es-pasado .ag-mes-head { background: var(--surface-sunken); }
    .ag-mes.es-hoy { border-color: var(--color-brand);
                     box-shadow: 0 0 0 2px var(--color-brand-tint); }
    .ag-mes.es-hoy .ag-mes-head { background: var(--color-brand-tint-faint);
                     border-bottom-color: var(--color-brand-soft); }
    .ag-mes.es-hoy .ag-mes-nom { color: var(--color-brand-strong); }
    .ag-hoy-chip { font-size: 9px; font-weight: var(--fw-bold); letter-spacing: var(--ls-caps);
                   text-transform: uppercase; color: var(--text-on-brand);
                   background: var(--color-brand); padding: 2px 7px;
                   border-radius: var(--radius-pill); }
    .ag-mes-body { padding: var(--space-2); display: flex; flex-direction: column;
                   gap: var(--space-2); flex: 1; }
    .ag-mes-vacio, .ag-mes-filtrado { display: flex; flex-direction: column;
                   align-items: center; justify-content: center; gap: 9px; flex: 1;
                   padding: var(--space-3) var(--space-2); text-align: center;
                   font-size: var(--fs-xs); color: var(--text-faint); }

    .ag-tarea { border-left: 3px solid var(--ag-dato-1); background: var(--surface-sunken);
                border-radius: 0 var(--radius-sm) var(--radius-sm) 0; padding: var(--space-2) 10px; }
    .ag-tarea.es-vencida { border-left-color: var(--ag-dato-3); }
    .ag-tarea-top { display: flex; align-items: baseline; justify-content: space-between;
                    gap: var(--space-2); }
    .ag-tarea-norma { font-size: var(--fs-xs); font-weight: var(--fw-bold);
                      color: var(--text-strong); }
    .ag-tarea-fecha { font-size: 10px; color: var(--text-muted);
                      font-variant-numeric: tabular-nums; white-space: nowrap; }
    .ag-tarea-desc { font-size: 10.5px; color: var(--text-muted); line-height: var(--lh-normal);
                     margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 2;
                     -webkit-box-orient: vertical; overflow: hidden; }
    .ag-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; align-items: center; }
    .ag-chip { font-size: 9px; padding: 1px 6px; border-radius: var(--radius-pill);
               background: var(--color-brand-tint); color: var(--color-brand-strong);
               font-weight: var(--fw-semibold); border: 0; font-family: inherit; }
    .ag-chip.es-inm { background: var(--cmf-warning-bg); color: var(--ink-on-warning-bg); }
    .ag-chip.es-archivo { background: var(--cmf-teal-50); color: var(--color-accent-deep);
                          font-variant-numeric: tabular-nums; }
    .ag-chip.es-norma { background: none; border: var(--border-w) solid var(--border-default);
                        color: var(--text-muted); cursor: pointer; }
    .ag-chip.es-norma:hover { border-color: var(--color-brand); color: var(--color-brand); }
    .ag-tarea-links { display: flex; gap: 10px; margin-top: 7px; font-size: 9.5px;
                      align-items: center; }
    .ag-tarea-links a, .ag-tarea-links button { color: var(--color-brand);
                      text-decoration: none; border: 0; background: none; padding: 0;
                      font: inherit; font-weight: var(--fw-semibold); cursor: pointer; }
    .ag-tarea-links a:hover, .ag-tarea-links button:hover { text-decoration: underline; }
    /* La procedencia de la fecha no es decorativa: `sección` la declara el
       PDF, `calculada` la derivó el parser de un plazo que el PDF declara
       ("un mes desde su publicación"), `cláusula` es un respaldo más frágil y
       `confirmada` la puso una persona. Mostrarlas iguales sería repetir el
       bug de las fechas inventadas. */
    .ag-fuente { display: inline-flex; align-items: center; gap: 4px;
                 font-size: 9px; color: var(--text-faint); margin-left: auto; }
    .ag-fuente::before { content: ""; width: 5px; height: 5px; border-radius: 50%;
                         background: var(--ag-dato-2); }
    .ag-fuente.es-clausula::before { background: var(--ag-dato-3); }
    .ag-fuente.es-manual::before { background: var(--color-brand); }
    /* Teal y no el ámbar de `cláusula`: una fecha calculada es firme —el PDF
       declara la regla completa—, mientras que la cláusula de aplicación es un
       respaldo frágil. Compartir color las igualaría. */
    .ag-fuente.es-calculada::before { background: var(--ag-dato-2); }

    /* El mazo: meses sin actividad, uno sobre otro. */
    .ag-mazo { flex: 0 0 108px; scroll-snap-align: center; position: relative;
               display: flex; align-items: stretch; padding: 5px 6px 5px 0; }
    .ag-mazo-stack { position: relative; flex: 1; }
    .ag-mazo-capa { position: absolute; inset: 0;
                    border: var(--border-w) dashed var(--border-default);
                    border-radius: var(--radius-md); background: var(--surface-card); }
    .ag-mazo-capa:nth-child(1) { transform: translate(6px, -5px); opacity: .4; }
    .ag-mazo-capa:nth-child(2) { transform: translate(3px, -2.5px); opacity: .65; }
    .ag-mazo-front { position: relative; height: 100%;
                     border: var(--border-w) dashed var(--border-default);
                     border-radius: var(--radius-md); background: var(--surface-card);
                     display: flex; flex-direction: column; align-items: center;
                     justify-content: center; gap: 9px; padding: var(--space-3) var(--space-2);
                     text-align: center; }
    .ag-mazo-meses { display: flex; flex-direction: column; gap: 2px; }
    .ag-mazo-mes { font-size: var(--fs-xs); color: var(--text-faint);
                   text-transform: capitalize; }
    .ag-mazo-msg { font-size: 9.5px; color: var(--text-faint); line-height: 1.35; }

    /* Más allá del riel */
    .ag-lejos { background: var(--surface-card);
                border: var(--border-w) solid var(--border-subtle);
                border-radius: var(--radius-lg); overflow: hidden; }
    .ag-lejos-grupo { border-top: var(--border-w) solid var(--border-subtle);
                      display: grid; grid-template-columns: 156px 1fr; }
    .ag-lejos-grupo:first-child { border-top: 0; }
    .ag-lejos-fecha { padding: var(--space-3) var(--space-4); background: var(--surface-sunken);
                      border-right: var(--border-w) solid var(--border-subtle); }
    .ag-lejos-fecha b { display: block; font-size: var(--fs-lg); color: var(--text-strong);
                        font-weight: var(--fw-regular); font-variant-numeric: tabular-nums; }
    .ag-lejos-fecha span { font-size: var(--fs-xs); color: var(--text-muted); }
    /* `min-width: 0` no es cosmético: sin él la elipsis de .ag-lejos-desc nunca
       aparece. Un grid item nace con `min-width: auto`, o sea no baja de su
       min-content, y el min-content de un texto en `nowrap` es el texto entero.
       La columna 1fr se desbordaba y el `overflow: hidden` de .ag-lejos cortaba
       la descripción a la mitad de una palabra, sin puntos suspensivos. */
    .ag-lejos-items { padding: var(--space-2) var(--space-4); display: flex;
                      flex-direction: column; min-width: 0; }
    .ag-lejos-item { display: flex; align-items: baseline; gap: var(--space-3);
                     padding: 7px 0; border-bottom: 1px dotted var(--border-subtle); }
    .ag-lejos-item:last-child { border-bottom: 0; }
    .ag-lejos-norma { font-size: var(--fs-sm); font-weight: var(--fw-bold);
                      color: var(--text-strong); flex: 0 0 122px; }
    .ag-lejos-desc { font-size: var(--fs-xs); color: var(--text-muted); flex: 1;
                     min-width: 0; overflow: hidden; text-overflow: ellipsis;
                     white-space: nowrap; }

    /* Revisión manual: cambios de archivo sin fecha de vigencia.
       Es el componente Alert en tono warning —fondo tintado y barra de
       acento de 4px a la izquierda—, y pasa a tono success cuando no queda
       nada pendiente (.rv-vacio). */
    /* Badge en variante subtle y no solid: el solid de tono warning es blanco
       sobre #b97708 y da 3,68:1, bajo AA. Es de las pocas combinaciones del
       sistema que no llega; el resto de los tonos sólidos sí. */
    .tab-badge { background: var(--cmf-warning-bg); color: var(--ink-on-warning-bg);
                 border-radius: var(--radius-pill); padding: 3px 8px;
                 font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                 letter-spacing: var(--ls-wide); line-height: 1;
                 margin-left: var(--space-2); vertical-align: 1px; }
    #revision-manual { background: var(--cmf-warning-bg);
                       border-left: var(--accent-bar-w) solid var(--cmf-warning);
                       border-radius: var(--radius-sm);
                       padding: var(--space-4) var(--space-5); }
    #revision-manual header { display: flex; align-items: center; gap: var(--space-2);
                              margin-bottom: 2px; background: none; padding: 0; }
    #revision-manual h2 { font-size: var(--fs-body); color: var(--ink-on-warning-bg);
                          margin: 0; border: none; padding: 0; background: none;
                          font-weight: var(--fw-bold); }
    /* Sobre el fondo tintado del Alert un badge subtle se perdería, así que
       éste va sobre superficie blanca con filete warning. Misma razón que
       .tab-badge para no usar el solid. */
    .rv-count { background: var(--surface-card); color: var(--ink-on-warning-bg);
                border: var(--border-w) solid var(--cmf-warning);
                border-radius: var(--radius-pill); padding: 3px 9px;
                font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                letter-spacing: var(--ls-wide); line-height: 1; }
    .rv-nota { font-size: var(--fs-sm); color: var(--text-body);
               margin: var(--space-2) 0 var(--space-3); max-width: 78ch;
               line-height: var(--lh-normal); }
    .rv-vacio { background: var(--cmf-success-bg);
                border-left-color: var(--cmf-success); }
    .rv-vacio h2 { color: var(--ink-on-success-bg); }
    .rv-vacio .rv-nota { margin-bottom: 0; }
    .rv-lista { list-style: none; margin: 0; padding: 0; }
    .rv-lista li { display: flex; align-items: baseline; gap: var(--space-3);
                   padding: var(--space-2) 0;
                   border-top: var(--border-w) solid var(--border-subtle);
                   font-size: var(--fs-sm); }
    .rv-fecha { color: var(--ink-on-warning-bg); font-variant-numeric: tabular-nums;
                flex: 0 0 92px; }
    .rv-doc { flex: 0 0 152px; font-weight: var(--fw-semibold);
              color: var(--text-strong); font-size: var(--fs-xs); }
    .rv-arch { flex: 0 0 auto; display: flex; gap: var(--space-1); flex-wrap: wrap; }
    .rv-tema { color: var(--text-body); flex: 1 1 auto; }
    .rv-pdf { flex: 0 0 auto; }
    .rv-nota code { font-family: var(--font-mono); background: var(--cmf-white);
                    border: var(--border-w) solid var(--border-subtle);
                    border-radius: var(--radius-xs); padding: 1px 5px;
                    font-size: var(--fs-xs); }
    .rv-como { margin: var(--space-3) 0; padding: var(--space-3) var(--space-4);
               background: var(--surface-sunken, var(--surface-card));
               border-left: 3px solid var(--color-brand); }
    .rv-como summary { cursor: pointer; font-weight: var(--fw-semibold);
                       font-size: var(--fs-sm); }
    .rv-como ol { margin: var(--space-3) 0 0; padding-left: var(--space-4);
                  font-size: var(--fs-sm); color: var(--text-muted); }
    .rv-como li { margin-bottom: var(--space-2); line-height: 1.55; }
    .rv-como p { font-size: var(--fs-sm); color: var(--text-muted); margin-top: var(--space-2); }
    .rv-como code { font-size: 0.92em; }
    .rv-revisados { font-size: var(--fs-xs); color: var(--ink-on-success-bg);
                    background: var(--cmf-success-bg);
                    border-left: var(--accent-bar-w) solid var(--cmf-success);
                    border-radius: var(--radius-sm);
                    padding: var(--space-2) var(--space-3);
                    margin: var(--space-3) 0 0; }
    .rv-cand { margin-top: var(--space-1); }
    .rv-cand-lbl { font-size: var(--fs-xs); color: var(--text-muted);
                   text-transform: uppercase; letter-spacing: var(--ls-caps);
                   font-weight: var(--fw-bold); }
    .rv-cand ul { list-style: none; margin: 2px 0 0; padding: 0; }
    .rv-cand li { font-size: var(--fs-xs); color: var(--text-muted);
                  padding: 1px 0; border: 0; }
    .rv-cand b { color: var(--ink-on-warning-bg); font-variant-numeric: tabular-nums; }

    /* Cambios relevantes */
    #cambios-relevantes { display: flex; flex-direction: column; gap: var(--space-4); }
    .cr-intro { padding: 0 var(--space-1) var(--space-1); font-size: var(--fs-sm);
                color: var(--text-body); }
    /* Card interactiva: se eleva 2px al hover y oscurece el borde. */
    .cr-grupo { background: var(--surface-card);
                border: var(--border-w) solid var(--border-subtle);
                border-radius: var(--radius-md); box-shadow: var(--shadow-sm);
                overflow: hidden;
                transition: box-shadow var(--dur-base) var(--ease-standard),
                            transform var(--dur-base) var(--ease-standard),
                            border-color var(--dur-base) var(--ease-standard); }
    .cr-grupo:hover { box-shadow: var(--shadow-md); transform: translateY(-2px);
                      border-color: var(--border-default); }
    .cr-grupo.abierto { border-color: var(--border-brand); transform: none; }
    .cr-cab { padding: var(--space-3) var(--space-5); background: var(--cmf-ink-50);
              transition: background var(--dur-fast) var(--ease-standard); }
    .cr-grupo.abierto .cr-cab {
        border-bottom: var(--border-w) solid var(--border-subtle);
        background: var(--color-brand-tint-faint); }
    .cr-cab-tit { display: flex; align-items: center; gap: var(--space-3);
                  flex-wrap: wrap; }
    .cr-cab h2 { font-size: var(--fs-lg); font-weight: var(--fw-bold);
                 color: var(--text-strong); padding: 0; border: 0;
                 background: none; }
    .cr-count { background: var(--color-brand); color: var(--text-on-brand);
                border-radius: var(--radius-pill); padding: 5px 10px;
                font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                letter-spacing: var(--ls-wide); line-height: 1; }
    .cr-total { font-size: var(--fs-xs); color: var(--text-muted); }
    /* Button, variante secondary → primary cuando el grupo queda abierto */
    .cr-revisar { margin-left: auto; display: inline-flex; align-items: center;
                  justify-content: center; gap: var(--space-2);
                  height: 34px; padding: 0 var(--space-3);
                  background: transparent; color: var(--color-brand);
                  border: var(--border-w-thick) solid var(--color-brand);
                  border-radius: var(--radius-sm);
                  font-weight: var(--fw-semibold); font-size: var(--fs-sm);
                  line-height: 1; white-space: nowrap; cursor: pointer;
                  transition: background var(--dur-fast) var(--ease-standard),
                              color var(--dur-fast) var(--ease-standard),
                              transform var(--dur-fast) var(--ease-standard); }
    .cr-revisar:hover { background: var(--color-brand-tint-faint); }
    .cr-revisar:active { transform: translateY(1px); }
    .cr-grupo.abierto .cr-revisar { background: var(--color-brand);
                                     color: var(--text-on-brand);
                                     border-color: var(--color-brand); }
    .cr-grupo.abierto .cr-revisar:hover { background: var(--color-brand-strong);
                                           border-color: var(--color-brand-strong); }
    .cr-desc { font-size: var(--fs-sm); color: var(--text-muted);
               margin-top: var(--space-1); }
    .cr-tabla { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
    .cr-tabla th { background: var(--cmf-ink-50); text-align: left;
                   padding: var(--space-2) var(--space-3);
                   font-weight: var(--fw-bold);
                   border-bottom: var(--border-w) solid var(--border-subtle);
                   font-size: var(--fs-xs); text-transform: uppercase;
                   letter-spacing: var(--ls-caps); color: var(--text-body); }
    .cr-tabla td { padding: var(--space-2) var(--space-3);
                   border-bottom: var(--border-w) solid var(--cmf-ink-100);
                   vertical-align: top; }
    .cr-th-fecha { width: 110px; }
    .cr-th-cambios { width: 170px; }
    .cr-th-pdf { width: 80px; text-align: right; }
    .cr-td-fecha { color: var(--text-body); white-space: nowrap;
                   font-variant-numeric: tabular-nums; }
    .td-doc { white-space: nowrap; }
    .cr-th-doc { width: 150px; }
    .cr-td-doc { color: var(--text-strong); font-weight: var(--fw-semibold);
                 font-size: var(--fs-xs); white-space: nowrap; }
    .cr-td-tema { color: var(--text-strong); line-height: var(--lh-normal); }
    .cr-td-cambios { font-size: var(--fs-xs); }
    .cr-td-cambios b { color: var(--color-brand); font-size: var(--fs-sm);
                       margin-right: 2px; }
    .cr-td-pdf { text-align: right; }
    .cr-detalle-toggle { cursor: pointer; color: var(--text-link); }
    .cr-sin-detalle { color: var(--text-muted); }
    /* `_render_detalle_tarea` sólo se usa dentro de la fila de detalle de
       Cambios relevantes, y ahí el que se despliega es el <tr>. El display:none
       con su override venía de unas tarjetas que la Agenda de tareas tuvo
       antes de ser un calendario, y que ya no existen. */
    .cr-detalle-row .cm-detalle { margin: 0; padding-top: 0; border-top: none; }
    .cr-detalle-row { display: none; }
    .cr-detalle-row.abierto { display: table-row; }
    .cr-detalle-row > td { background: var(--cmf-ink-50) !important;
                           padding: var(--space-3) var(--space-5);
                           border-bottom: var(--border-w-thick) solid var(--border-subtle); }
    .cr-vacio { padding: var(--space-6); color: var(--text-muted);
                text-align: center; font-style: italic; }
    /* Antetítulo del sistema: mayúsculas con tracking amplio */
    /* Retrospectiva: lo que ya debió implementarse, por mes. Iba en violeta
       para no mezclarla con la escala de urgencia futura de las columnas, y
       ese sigue siendo el criterio — pero ahora el morado es el color de
       marca y está en todas partes, así que el que distingue es el índigo
       #3F3A7E de la paleta de apoyo: misma familia, distinto rol. */
    .rt-inm { margin-left: var(--space-1); background: var(--cmf-info-bg);
              color: var(--cmf-indigo); border-radius: var(--radius-pill);
              padding: 3px 8px; font-size: var(--fs-xs);
              font-weight: var(--fw-semibold); letter-spacing: var(--ls-wide);
              line-height: 1; }
    /* Escala de urgencia sobre los tokens funcionales: danger → warning →
       navy. El navy es el "info" del sistema, así que reemplaza al azul. */
    .cm-detalle { margin-top: var(--space-2); padding-top: var(--space-2);
                  border-top: var(--border-w) dashed var(--border-subtle); }
    .cm-det-bloque { margin-bottom: var(--space-2); }
    .cm-det-bloque:last-child { margin-bottom: 0; }
    .cm-det-label { display: block; font-size: var(--fs-xs);
                    font-weight: var(--fw-bold); text-transform: uppercase;
                    letter-spacing: var(--ls-caps); color: var(--text-muted);
                    margin-bottom: var(--space-1); }
    .cm-archivos { font-size: var(--fs-xs); padding-left: 0; list-style: none;
                   display: flex; flex-direction: column; gap: 3px; }
    .cm-archivos .chip { margin-right: var(--space-1); }

    /* Badge, variante neutral subtle */
    #stats { display: flex; gap: var(--space-2); flex-wrap: wrap;
             padding: var(--space-3) var(--space-5);
             border-bottom: var(--border-w) solid var(--border-subtle);
             background: var(--cmf-ink-50); }
    .stat { padding: 5px 10px; border-radius: var(--radius-pill);
            font-size: var(--fs-xs); font-weight: var(--fw-semibold);
            letter-spacing: var(--ls-wide); line-height: 1;
            background: var(--surface-sunken); color: var(--text-body); }
    .stat b { color: var(--text-strong); margin-right: var(--space-1); }

    #filtros { padding: var(--space-3) var(--space-5); display: flex;
               gap: var(--space-2); flex-wrap: wrap;
               border-bottom: var(--border-w) solid var(--border-subtle);
               background: var(--cmf-ink-50); align-items: center; }
    /* Button sm, variante secondary → primary cuando está activo */
    .filtro-btn { display: inline-flex; align-items: center; justify-content: center;
                  height: 34px; padding: 0 var(--space-3);
                  background: transparent; color: var(--color-brand);
                  border: var(--border-w-thick) solid var(--color-brand);
                  border-radius: var(--radius-sm); cursor: pointer;
                  font-size: var(--fs-sm); font-weight: var(--fw-semibold);
                  line-height: 1; white-space: nowrap;
                  transition: background var(--dur-fast) var(--ease-standard),
                              color var(--dur-fast) var(--ease-standard),
                              transform var(--dur-fast) var(--ease-standard); }
    .filtro-btn:hover { background: var(--color-brand-tint-faint); }
    .filtro-btn:active { transform: translateY(1px); }
    .filtro-btn.activo { background: var(--color-brand); color: var(--text-on-brand);
                         border-color: var(--color-brand); }
    .filtro-btn.activo:hover { background: var(--color-brand-strong);
                               border-color: var(--color-brand-strong); }
    /* Input */
    #search { flex: 1; min-width: 220px; height: 34px; padding: 0 var(--space-3);
              background: var(--surface-card); color: var(--text-strong);
              border: var(--border-w) solid var(--border-default);
              border-radius: var(--radius-sm); outline: none;
              font-family: var(--font-sans); font-size: var(--fs-sm);
              transition: border-color var(--dur-fast) var(--ease-standard),
                          box-shadow var(--dur-fast) var(--ease-standard); }
    #search::placeholder { color: var(--text-muted); }
    #search:focus { border-color: var(--color-brand-soft);
                    box-shadow: 0 0 0 3px var(--color-brand-tint); }

    table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
    /* --text-muted sobre el gris hundido da 3,98:1 y estos encabezados van en
       12px: la tinta de cuerpo es la que llega a AA. */
    th { background: var(--surface-sunken); text-align: left;
         padding: var(--space-2) var(--space-3); font-weight: var(--fw-bold);
         border-bottom: var(--border-w) solid var(--border-subtle);
         font-size: var(--fs-xs); text-transform: uppercase;
         letter-spacing: var(--ls-caps); color: var(--text-body); }
    td { padding: var(--space-2) var(--space-3);
         border-bottom: var(--border-w) solid var(--cmf-ink-100);
         vertical-align: top; }
    tr.fila-principal { cursor: pointer; }
    tr.fila-principal:hover td { background: var(--cmf-ink-50); }
    tr.fila-principal.nueva td { background: var(--color-brand-tint-faint); }
    .td-normas { color: var(--color-brand); font-weight: var(--fw-medium); }
    .td-vig { color: var(--text-body); font-size: var(--fs-xs); }
    .td-vacio { padding: var(--space-5); text-align: center;
                color: var(--text-muted); font-style: italic; }
    .tl-vacio, .tl-sin-resultados { padding: var(--space-5);
                color: var(--text-muted); font-style: italic; }

    /* Badge, variantes subtle. El tono lo fija el significado, no el gusto:
       nueva→success, modifica→navy(info), deroga→danger, consulta→warning,
       circular→brand, prórroga→accent, otro→neutral. */
    .tag { display: inline-flex; align-items: center; padding: 5px 10px;
           border-radius: var(--radius-pill); font-size: var(--fs-xs);
           font-weight: var(--fw-semibold); letter-spacing: var(--ls-wide);
           line-height: 1; margin-right: var(--space-1); white-space: nowrap; }
    .tag-consulta { background: var(--cmf-warning-bg); color: var(--ink-on-warning-bg); }
    .tag-nueva    { background: var(--cmf-success-bg); color: var(--ink-on-success-bg); }
    .tag-mod      { background: var(--cmf-info-bg);    color: var(--cmf-navy); }
    .tag-circular { background: var(--color-brand-tint); color: var(--cmf-purple-800); }
    .tag-postergacion { background: var(--cmf-teal-50); color: var(--cmf-teal-deep); }
    .tag-deroga   { background: var(--cmf-danger-bg);  color: var(--ink-on-danger-bg); }
    .tag-otro     { background: var(--surface-sunken); color: var(--text-body); }

    /* Control de despliegue compartido por el listado, Cambios relevantes y el
       Último cambio publicado. Es discreto a propósito —borde y no relleno—
       porque se repite en cada fila de una tabla de 607: un botón sólido por
       fila convertiría la tabla en una pared de botones y taparía el dato, que
       es lo que la persona vino a leer. Lo que tiene que lograr es sólo
       anunciar que ahí hay algo que abrir. */
    .btn-revisar { border: var(--border-w) solid var(--border-default);
                   background: var(--surface-card); color: var(--text-muted);
                   font: inherit; font-size: var(--fs-xs);
                   font-weight: var(--fw-semibold); letter-spacing: var(--ls-wide);
                   line-height: 1; white-space: nowrap; cursor: pointer;
                   padding: 6px 10px; border-radius: var(--radius-sm);
                   transition: color var(--dur-fast) var(--ease-standard),
                               border-color var(--dur-fast) var(--ease-standard); }
    .btn-revisar:hover, tr.fila-principal:hover .btn-revisar {
                   border-color: var(--color-brand); color: var(--color-brand); }
    .btn-revisar[aria-expanded="true"] { border-color: var(--border-brand);
                   color: var(--color-brand); background: var(--color-brand-tint-faint); }
    .btn-revisar:focus-visible { outline: 2px solid var(--color-brand);
                   outline-offset: 2px; }
    .td-revisar { text-align: right; white-space: nowrap; }

    tr.detail-row { display: none; }
    tr.detail-row.abierto { display: table-row; }
    tr.detail-row > td { background: var(--cmf-ink-50) !important;
                         padding: var(--space-4) var(--space-5);
                         border-bottom: var(--border-w-thick) solid var(--border-subtle); }
    .detalle { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
    .d-bloque { background: var(--surface-card);
                border: var(--border-w) solid var(--border-subtle);
                border-radius: var(--radius-md); box-shadow: var(--shadow-xs);
                padding: var(--space-2) var(--space-3); }
    /* Alert warning: barra de acento a la izquierda, no borde completo */
    .d-bloque.d-warn { background: var(--cmf-warning-bg); border-color: transparent;
                       border-left: var(--accent-bar-w) solid var(--cmf-warning);
                       border-radius: var(--radius-sm); box-shadow: none; }
    .d-label { display: block; font-size: var(--fs-xs); font-weight: var(--fw-bold);
               text-transform: uppercase; letter-spacing: var(--ls-caps);
               color: var(--text-muted); margin-bottom: var(--space-2); }
    .d-warn .d-label { color: var(--ink-on-warning-bg); }
    .d-bloque p { font-size: var(--fs-sm); line-height: var(--lh-normal);
                  color: var(--text-body); }
    .d-bloque ul { font-size: var(--fs-sm); line-height: var(--lh-relaxed);
                   padding-left: var(--space-4); color: var(--text-body); }
    .d-msi li { color: var(--text-muted); font-style: italic; font-size: var(--fs-xs); }
    .d-extra { font-size: var(--fs-xs); color: var(--text-muted);
               margin-top: var(--space-1); }
    .d-vacio { padding: var(--space-2); color: var(--text-muted);
               font-style: italic; font-size: var(--fs-sm); }

    /* Badge subtle en su forma más chica */
    .chips { display: flex; flex-wrap: wrap; gap: var(--space-1); }
    .chip { display: inline-flex; align-items: center;
            background: var(--color-brand-tint); color: var(--cmf-purple-800);
            border-radius: var(--radius-pill); padding: 4px 9px;
            font-size: var(--fs-xs); font-weight: var(--fw-semibold);
            letter-spacing: var(--ls-wide); line-height: 1; white-space: nowrap; }
    .chip-crear     { background: var(--cmf-success-bg); color: var(--ink-on-success-bg); }
    .chip-modificar { background: var(--cmf-info-bg);    color: var(--cmf-navy); }
    .chip-eliminar  { background: var(--cmf-danger-bg);  color: var(--ink-on-danger-bg); }

    .tl-norma { padding: var(--space-3) var(--space-5);
                border-bottom: var(--border-w) solid var(--cmf-ink-100); }
    .tl-norma h3 { font-size: var(--fs-sm); font-weight: var(--fw-semibold);
                   color: var(--color-brand); margin-bottom: var(--space-2);
                   display: flex; align-items: center; gap: var(--space-2); }
    .tl-count { font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                letter-spacing: var(--ls-wide); line-height: 1;
                color: var(--text-body); background: var(--surface-sunken);
                padding: 4px 9px; border-radius: var(--radius-pill); }
    .tl-items { display: flex; flex-wrap: wrap; gap: var(--space-2); }
    /* Barra de acento a la izquierda, el borde expresivo del sistema */
    .tl-item { background: var(--cmf-ink-50); border-radius: var(--radius-sm);
               padding: var(--space-1) var(--space-3); font-size: var(--fs-sm);
               border-left: var(--accent-bar-w) solid var(--color-brand);
               color: var(--text-body);
               transition: background var(--dur-fast) var(--ease-standard); }
    .tl-item:hover { background: var(--surface-sunken); color: var(--text-strong);
                     text-decoration: none; }
    .tl-actor { font-weight: var(--fw-semibold); color: var(--color-brand); }
    /* La barra izquierda codifica la acción: morado modifica, rojo deroga,
       gris apenas menciona. Nunca va sola — el texto del evento dice lo mismo
       en palabras, porque el color por sí solo no es un canal accesible. */
    .tl-der { border-left-color: var(--cmf-danger); }
    .tl-ref { border-left-color: var(--border-default); }
    .tl-ref .tl-actor { color: var(--text-muted); font-weight: var(--fw-medium); }
    .tl-desglose { font-size: var(--fs-xs); font-weight: var(--fw-regular);
                   color: var(--text-muted); letter-spacing: var(--ls-normal); }

    /* Pie navy, como la franja de cierre de las piezas oficiales */
    footer { text-align: center; color: var(--cmf-ink-200); font-size: var(--fs-sm);
             background: var(--surface-navy); padding: var(--space-5);
             margin-top: var(--space-6); }
    footer a { color: var(--cmf-teal-200); }
    footer a:hover { color: var(--cmf-white); }

    @media (max-width: 800px) {
      .detalle { grid-template-columns: 1fr; }
      /* Los <th> se ocultan junto con sus <td>. Antes la regla nombraba sólo
         las celdas y los encabezados quedaban en pie: bajo 800px la cabecera
         tenía dos columnas más que el cuerpo y toda la tabla se leía corrida.
         Si agregas una columna ocultable, nómbrala en los dos lados. */
      .td-vig, .th-vig, .td-link, .th-link { display: none; }
    }

    /* En celular «Más allá de N meses» dejaba de ser una tabla y no llegaba a
       ser una tarjeta: la fecha y los documentos son dos columnas fijas —156px
       y 122px— que en 570px de ancho no dejan lugar para la descripción, así
       que el texto quedaba cortado contra el borde y el bloque se leía como una
       tabla a la que le falta la mitad derecha.

       Acá el grupo se convierte en lo que ya era conceptualmente: una tarjeta
       por fecha, con la fecha de encabezado y los documentos debajo. El
       contenedor pierde su marco —lo toma cada grupo— para que se lean como
       piezas separadas y no como filas de algo. */
    @media (max-width: 640px) {
      .ag-lejos { background: none; border: 0; border-radius: 0;
                  overflow: visible; display: flex; flex-direction: column;
                  gap: var(--space-3); }
      .ag-lejos-grupo { display: block; border-top: 0;
                        background: var(--surface-card);
                        border: var(--border-w) solid var(--border-subtle);
                        border-radius: var(--radius-md); overflow: hidden; }
      .ag-lejos-fecha { display: flex; align-items: baseline; flex-wrap: wrap;
                        gap: var(--space-2); border-right: 0;
                        border-bottom: var(--border-w) solid var(--border-subtle); }
      .ag-lejos-fecha b { display: inline; font-size: var(--fs-body); }
      /* La descripción pasa a envolver en vez de recortarse: `_tema_corto` ya
         la deja en 120 caracteres, así que son dos o tres líneas, no un muro. */
      .ag-lejos-item { display: block; padding: var(--space-2) 0; }
      .ag-lejos-norma { display: block; flex: none; margin-bottom: 2px; }
      .ag-lejos-desc { white-space: normal; overflow: visible;
                       text-overflow: clip; line-height: var(--lh-normal); }

      /* Cambios relevantes, mismo problema: cinco columnas con cuatro anchos
         fijos (110+150+170+80) dejan a «Tema» sin ancho, y ahí el texto baja a
         una palabra por línea mientras las dos últimas columnas se salen del
         borde. Cada fila pasa a ser una tarjeta.

         La grilla se aplica sobre el propio <tr>, sin envoltorios nuevos: así
         `fecha` y `PDF` comparten la primera línea —son las dos referencias
         cortas— y norma, tema y el botón ocupan el ancho completo debajo. El
         <thead> se oculta porque los rótulos de columna no describen nada
         cuando ya no hay columnas. */
      .cr-tabla, .cr-tabla tbody { display: block; }
      .cr-tabla thead { display: none; }
      .cr-fila { display: grid; grid-template-columns: 1fr auto;
                 grid-template-areas: "fecha pdf" "doc doc" "tema tema"
                                      "cambios cambios";
                 gap: 2px var(--space-3);
                 background: var(--surface-card);
                 border: var(--border-w) solid var(--border-subtle);
                 border-radius: var(--radius-md);
                 padding: var(--space-3); margin-bottom: var(--space-3); }
      .cr-fila > td { display: block; border-bottom: 0; padding: 0; }
      .cr-td-fecha { grid-area: fecha; }
      .cr-td-pdf { grid-area: pdf; }
      .cr-td-doc { grid-area: doc; white-space: normal; font-size: var(--fs-sm); }
      .cr-td-tema { grid-area: tema; margin-bottom: var(--space-2); }
      .cr-td-cambios { grid-area: cambios; }
      /* El detalle se pega a su tarjeta en vez de flotar como bloque suelto:
         la de arriba pierde el redondeo inferior mientras está abierto. */
      .cr-fila:has(+ .cr-detalle-row.abierto) {
                 border-radius: var(--radius-md) var(--radius-md) 0 0;
                 margin-bottom: 0; }
      .cr-detalle-row.abierto { display: block;
                 border: var(--border-w) solid var(--border-subtle);
                 border-top: 0; border-radius: 0 0 var(--radius-md) var(--radius-md);
                 margin-bottom: var(--space-3); }
      .cr-detalle-row > td { display: block; padding: var(--space-3);
                 border-bottom: 0; }

      /* Las cuatro pestañas no caben en una línea. El scroll y su señal ya
         están definidos arriba; acá sólo se aprieta el espaciado y se reserva
         el ancho del chevrón para que la última no quede debajo de él. */
      /* Sin padding lateral a propósito: los chevrones se apagan justo en los
         extremos, así que la primera y la última pestaña nunca quedan debajo de
         uno. Reservarles espacio fijo sólo dejaría la barra indentada 40px en
         reposo, desalineada del contenido. De los casos intermedios se encarga
         `scroll-padding-inline`. */
      #tabs { gap: var(--space-4); }
      .tab { white-space: nowrap; font-size: var(--fs-sm); }

      /* Listado completo: siete columnas es la tabla más ancha del dashboard.
         Misma conversión a tarjeta que Cambios relevantes.

         Acá sí vuelven Vigencia y PDF, que el breakpoint de 800px escondía por
         falta de ancho: apiladas ya no compiten por él, y esconder la vigencia
         —el dato que contesta para cuándo hay que tener esto hecho— era la
         peor de las pérdidas posibles. Como el <thead> desaparece, las dos que
         no se explican solas llevan su rótulo en un ::before. */
      #tabla-resoluciones, #tabla-resoluciones tbody { display: block; }
      #tabla-resoluciones thead { display: none; }
      tr.fila-principal { display: grid; grid-template-columns: 1fr auto;
                 grid-template-areas: "fecha pdf" "doc doc" "tipos tipos"
                                      "normas normas" "vig vig"
                                      "revisar revisar";
                 gap: 2px var(--space-3);
                 background: var(--surface-card);
                 border: var(--border-w) solid var(--border-subtle);
                 border-radius: var(--radius-md);
                 padding: var(--space-3); margin-bottom: var(--space-3); }
      tr.fila-principal > td { display: block; border-bottom: 0; padding: 0; }
      /* El resaltado de «nueva» pintaba las celdas. Como tarjeta hay `gap`
         entre ellas, así que el color salía a manchones: lo toma la tarjeta. */
      tr.fila-principal.nueva { background: var(--color-brand-tint-faint); }
      tr.fila-principal.nueva > td,
      tr.fila-principal:hover > td { background: none; }
      .td-fecha { grid-area: fecha; font-size: var(--fs-xs);
                  color: var(--text-muted); }
      .td-link { display: block; grid-area: pdf; text-align: right; }
      .td-doc { grid-area: doc; white-space: normal; font-size: var(--fs-sm); }
      .td-tipos { grid-area: tipos; margin: 2px 0; }
      .td-normas { grid-area: normas; }
      .td-vig { display: block; grid-area: vig; }
      .td-revisar { grid-area: revisar; text-align: left;
                    margin-top: var(--space-2); }
      .td-normas::before, .td-vig::before {
                    color: var(--text-muted); font-weight: var(--fw-regular);
                    text-transform: uppercase; letter-spacing: var(--ls-caps);
                    font-size: var(--fs-xs); }
      .td-normas::before { content: "Afecta a "; }
      .td-vig::before { content: "Vigencia "; }
      tr.fila-principal:has(+ .detail-row.abierto) {
                 border-radius: var(--radius-md) var(--radius-md) 0 0;
                 margin-bottom: 0; }
      tr.detail-row.abierto { display: block;
                 border: var(--border-w) solid var(--border-subtle);
                 border-top: 0; border-radius: 0 0 var(--radius-md) var(--radius-md);
                 margin-bottom: var(--space-3); }
      tr.detail-row > td { display: block; padding: var(--space-3);
                 border-bottom: 0; }

      /* Revisión manual: la lista es un flex con fecha y norma en anchos fijos
         (92px + 152px) que dejan al tema sin espacio. Se apila. */
      .rv-lista li { display: block; }
      .rv-fecha, .rv-doc { flex: none; }
      .rv-fecha { font-size: var(--fs-xs); color: var(--text-muted); }
      .rv-doc { display: block; margin: 2px 0; }
      .rv-pdf { display: inline-block; margin-top: var(--space-2); }

      /* El encabezado azul se come media pantalla con el padding de escritorio. */
      body > header { padding: var(--space-5) var(--space-4); }
      .hd-logo { margin-bottom: var(--space-4); }
      main { padding: 0 var(--space-3); }
    }
  </style>
</head>
<body>

<header>
  <div class="hd-inner">
    <div class="hd-logo"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200.15 37.59" role="img" aria-label="Comisión para el Mercado Financiero" focusable="false"><g><rect x="83.71" y="1.7" width="0.56" height="35.89" style="fill: currentColor"/><polygon points="66.98 37.59 66.95 21.83 76.63 21.83 76.63 15.68 66.94 15.68 66.93 7.85 78.49 7.85 78.49 1.7 59.11 1.7 59.17 34.36 66.98 37.59" style="fill: currentColor"/><polygon points="46.48 12.88 46.48 29.11 53.08 31.85 53.08 1.7 35.49 10.65 17.9 1.7 17.9 10.09 35.49 17.36 46.48 12.88" style="fill: currentColor"/><path d="M97.79,3.25v1l0,0h0a3.34,3.34,0,0,0-4.5.26,3.36,3.36,0,0,0-1,2.44,3.34,3.34,0,0,0,1,2.43,3.24,3.24,0,0,0,2.39,1,3.31,3.31,0,0,0,2.11-.74s0,0,0,0a.05.05,0,0,1,0,0v.65a.52.52,0,0,1-.31.5A4.21,4.21,0,0,1,92.62,10a4.24,4.24,0,0,1-1.24-3,4.24,4.24,0,0,1,1.24-3.06,4.06,4.06,0,0,1,3-1.26,4.14,4.14,0,0,1,2.15.59.05.05,0,0,1,0,0" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M101.74,4.5a3.36,3.36,0,0,0-1,2.44,3.34,3.34,0,0,0,1,2.43,3.33,3.33,0,0,0,4.78,0,3.34,3.34,0,0,0,1-2.43,3.37,3.37,0,0,0-1-2.44,3.33,3.33,0,0,0-4.78,0m6.63,2.44A4.2,4.2,0,0,1,107.13,10a4.2,4.2,0,0,1-6,0,4.2,4.2,0,0,1-1.24-3,4.2,4.2,0,0,1,1.24-3.06,4.2,4.2,0,0,1,6,0,4.2,4.2,0,0,1,1.24,3.06" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M111.14,2.81l3.71,4.84,3.7-4.84a.16.16,0,0,1,.18-.05.15.15,0,0,1,.1.15V11s0,0,0,0h-.51a.29.29,0,0,1-.22-.09.33.33,0,0,1-.09-.23V5l-3.07,4a.09.09,0,0,1-.06,0,.09.09,0,0,1-.07,0l-3.07-4v6a0,0,0,0,1,0,0h-.78s0,0,0,0V2.91a.15.15,0,0,1,.1-.15.16.16,0,0,1,.18.05" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M122.82,11.07H122s0,0,0,0V2.84s0,0,0,0h.78a0,0,0,0,1,0,0V11a0,0,0,0,1,0,0" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M128,10.39a3,3,0,0,0,1.09-.2,1.28,1.28,0,0,0,.67-.7,1.32,1.32,0,0,0,0-1.05,1.47,1.47,0,0,0-.77-.8l-2.44-1.15-.08,0a1.94,1.94,0,0,1-1-1.1,1.88,1.88,0,0,1,0-1.15,1.86,1.86,0,0,1,.33-.66,2.14,2.14,0,0,1,1.06-.73,3.45,3.45,0,0,1,.67-.15,4.59,4.59,0,0,1,.75,0,5,5,0,0,1,2.08.49s0,0,0,0V4s0,0,0,0h0L130.26,4a4.11,4.11,0,0,0-1.92-.51,3.25,3.25,0,0,0-.63,0,2.64,2.64,0,0,0-.52.12,1.48,1.48,0,0,0-.52.3,1,1,0,0,0-.31.52,1.06,1.06,0,0,0,0,.61,1.26,1.26,0,0,0,.69.67l2.35,1.11a2.25,2.25,0,0,1,1.2,1.26,2.19,2.19,0,0,1,0,1.72A2.1,2.1,0,0,1,129.47,11a3.6,3.6,0,0,1-1.41.28,4.33,4.33,0,0,1-2.13-.47.52.52,0,0,1-.3-.5V9.64s0,0,0,0,0,0,0,0a4.44,4.44,0,0,0,.38.27,3.49,3.49,0,0,0,2,.51" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M134.1,11.07h-.78a0,0,0,0,1,0,0V2.84a0,0,0,0,1,0,0h.78a0,0,0,0,1,0,0V11a0,0,0,0,1,0,0" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M140.49,2.18l-.31-.3a.05.05,0,0,1,0-.06L141.41.57h.8l0,0s0,0,0,.05L140.7,2.18a.12.12,0,0,1-.21,0m-2,2.32a3.36,3.36,0,0,0-1,2.44,3.34,3.34,0,0,0,1,2.43,3.34,3.34,0,0,0,4.79,0,3.34,3.34,0,0,0,1-2.43,3.36,3.36,0,0,0-1-2.44,3.34,3.34,0,0,0-4.79,0m6.64,2.44A4.21,4.21,0,0,1,143.86,10a4.19,4.19,0,0,1-6,0,4.21,4.21,0,0,1-1.25-3,4.21,4.21,0,0,1,1.25-3.06,4.19,4.19,0,0,1,6,0,4.21,4.21,0,0,1,1.25,3.06" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M153.2,11.11a.14.14,0,0,1-.18-.06L148.45,5v6a0,0,0,0,1,0,0h-.78a0,0,0,0,1,0,0V2.92a.16.16,0,0,1,.11-.16.15.15,0,0,1,.19.06l4.57,6.06V3.12a.29.29,0,0,1,.09-.22.28.28,0,0,1,.22-.1h.51a0,0,0,0,1,0,0V11a.16.16,0,0,1-.12.16" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M92.24,19.79H94.1a1.29,1.29,0,0,0,1.31-.87,1.92,1.92,0,0,0,0-1.28,1.29,1.29,0,0,0-1.31-.87H92.24Zm0,.87v3.46a0,0,0,0,1,0,0h-.78a0,0,0,0,1,0,0V15.93s0,0,0,0H94.1a2.15,2.15,0,0,1,2.1,1.43,2.64,2.64,0,0,1,.18,1,2.55,2.55,0,0,1-.18.95,2.17,2.17,0,0,1-.75,1,2.21,2.21,0,0,1-1.35.41Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M101,17.7,99.4,21.44h3.22Zm-2.78,6.43a0,0,0,0,1,0,0h-.85l0,0a.06.06,0,0,1,0,0L100.83,16a.18.18,0,0,1,.18-.11.2.2,0,0,1,.18.11l3.51,8.15a0,0,0,0,1,0,0l0,0h-.62a.37.37,0,0,1-.36-.24l-.7-1.61H99Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M107.37,19.79h1.87a1.27,1.27,0,0,0,1.3-.87,1.92,1.92,0,0,0,0-1.28,1.27,1.27,0,0,0-1.3-.87h-1.87Zm0,.87v3.46a0,0,0,0,1,0,0h-.78s0,0,0,0V15.93s0,0,0,0h2.69a2.16,2.16,0,0,1,1.34.42,2.28,2.28,0,0,1,.76,1,2.63,2.63,0,0,1,.17,1,2.54,2.54,0,0,1-.17.95,2.06,2.06,0,0,1-1.82,1.42L112,24.09s0,0,0,0a0,0,0,0,1,0,0h-.75a.46.46,0,0,1-.38-.2l-2.32-3.2a.23.23,0,0,0-.19-.1Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M116.54,17.7l-1.61,3.74h3.22Zm-2.78,6.43a0,0,0,0,1,0,0h-.85l0,0a.06.06,0,0,1,0,0L116.36,16a.18.18,0,0,1,.18-.11.2.2,0,0,1,.18.11l3.51,8.15a0,0,0,0,1,0,0l0,0h-.61a.37.37,0,0,1-.36-.24l-.7-1.61h-4Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M127.26,19.38h2.31s0,0,0,.05v.51a.29.29,0,0,1-.09.22.27.27,0,0,1-.22.1h-2v3h2a.3.3,0,0,1,.22.1.29.29,0,0,1,.09.22v.52a0,0,0,0,1,0,0h-2.86a.27.27,0,0,1-.22-.1.29.29,0,0,1-.09-.22V15.93s0,0,0,0h3.14s0,0,0,0v.79s0,.05,0,.05h-2.31Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M135.34,23.29a.31.31,0,0,1,.22.09.29.29,0,0,1,.09.22v.52a0,0,0,0,1,0,0h-2.86a.3.3,0,0,1-.23-.1.29.29,0,0,1-.09-.22V15.93a0,0,0,0,1,0,0h.78s0,0,0,0v7.36Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M142.69,15.9l3.71,4.84,3.71-4.84a.15.15,0,0,1,.18-.05.14.14,0,0,1,.1.15v8.12a0,0,0,0,1,0,0h-.51a.28.28,0,0,1-.22-.1.29.29,0,0,1-.09-.22V18.07l-3.07,4a.08.08,0,0,1-.12,0l-3.07-4v6.05a0,0,0,0,1,0,0h-.78a0,0,0,0,1,0,0V16a.16.16,0,0,1,.28-.1" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M154.42,19.38h2.32a0,0,0,0,1,0,.05v.51a.29.29,0,0,1-.09.22.29.29,0,0,1-.23.1h-2v3h2a.33.33,0,0,1,.23.1.29.29,0,0,1,.09.22v.52a0,0,0,0,1,0,0h-2.87a.28.28,0,0,1-.22-.1.29.29,0,0,1-.09-.22V15.93s0,0,0,0h3.14a0,0,0,0,1,0,0v.79a0,0,0,0,1,0,.05h-2.32Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M160.46,19.79h1.86a1.27,1.27,0,0,0,1.3-.87,1.77,1.77,0,0,0,0-1.28,1.27,1.27,0,0,0-1.3-.87h-1.86Zm0,.87v3.46a0,0,0,0,1-.05,0h-.77a0,0,0,0,1,0,0V15.93s0,0,0,0h2.68a2.16,2.16,0,0,1,1.34.42,2.21,2.21,0,0,1,.76,1,2.63,2.63,0,0,1,.17,1,2.54,2.54,0,0,1-.17.95,2.06,2.06,0,0,1-1.82,1.42l2.49,3.44s0,0,0,0a0,0,0,0,1,0,0h-.75a.46.46,0,0,1-.38-.2l-2.32-3.2a.22.22,0,0,0-.18-.1Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M173,16.33v1s0,0,0,0a0,0,0,0,1,0,0,3.34,3.34,0,0,0-4.5.26,3.49,3.49,0,0,0,0,4.87,3.34,3.34,0,0,0,4.5.26h0s0,0,0,0v.66a.53.53,0,0,1-.3.5,4.16,4.16,0,0,1-1.86.43,4.06,4.06,0,0,1-3-1.26,4.36,4.36,0,0,1,0-6.11,4.06,4.06,0,0,1,3-1.26,4.13,4.13,0,0,1,2.14.59s0,0,0,0" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M178.13,17.7l-1.62,3.74h3.23Zm-2.78,6.43a0,0,0,0,1,0,0h-.85a.05.05,0,0,1,0,0v0L178,16a.18.18,0,0,1,.18-.11.18.18,0,0,1,.17.11l3.52,8.15a0,0,0,0,1,0,0,.05.05,0,0,1,0,0h-.62a.35.35,0,0,1-.35-.24l-.7-1.61h-4Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M184.49,23.29h1.59a2.55,2.55,0,0,0,1.63-.55,2.93,2.93,0,0,0,.93-1.35,4.34,4.34,0,0,0,0-2.73,2.88,2.88,0,0,0-.93-1.35,2.54,2.54,0,0,0-1.63-.54h-1.59Zm3.74-6.68a3.74,3.74,0,0,1,1.21,1.76,5,5,0,0,1,0,3.31,3.74,3.74,0,0,1-1.21,1.76,3.34,3.34,0,0,1-2.15.72H184a.34.34,0,0,1-.23-.1.29.29,0,0,1-.09-.22V15.93a0,0,0,0,1,0,0h2.41a3.34,3.34,0,0,1,2.15.72" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M193.36,17.59a3.49,3.49,0,0,0,0,4.87,3.34,3.34,0,0,0,4.79,0,3.49,3.49,0,0,0,0-4.87,3.34,3.34,0,0,0-4.79,0M200,20a4.2,4.2,0,0,1-1.24,3,4.2,4.2,0,0,1-6,0,4.36,4.36,0,0,1,0-6.11,4.2,4.2,0,0,1,6,0A4.19,4.19,0,0,1,200,20" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M92.24,37.17s0,0,0,0h-.78s0,0,0,0V29s0,0,0,0h3.14a0,0,0,0,1,0,0v.79a0,0,0,0,1,0,0H92.24v2.62h2.32a0,0,0,0,1,0,0V33a.29.29,0,0,1-.09.22.33.33,0,0,1-.23.1h-2Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M98.09,37.2h-.78a0,0,0,0,1,0,0V29s0,0,0,0h.78s0,0,0,0v8.19s0,0,0,0" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M106.92,37.25a.17.17,0,0,1-.19-.06l-4.57-6.07v6.05s0,0,0,0h-.78a0,0,0,0,1,0,0V29.06a.14.14,0,0,1,.11-.16.15.15,0,0,1,.19.06L106.17,35V29.26a.33.33,0,0,1,.1-.23.29.29,0,0,1,.22-.09h.5s0,0,0,0v8.11a.16.16,0,0,1-.11.16" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M112.54,30.75l-1.61,3.74h3.22Zm-2.77,6.43s0,0,0,0h-.88s0,0,0,0L112.36,29a.18.18,0,0,1,.18-.12.19.19,0,0,1,.18.12l3.51,8.15s0,0,0,0h-.65a.36.36,0,0,1-.36-.23l-.69-1.61h-4Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M123.66,37.25a.18.18,0,0,1-.19-.06l-4.56-6.07v6.05s0,0,0,0h-.78s0,0,0,0V29.06a.15.15,0,0,1,.11-.16.15.15,0,0,1,.19.06L122.92,35V29.26A.32.32,0,0,1,123,29a.29.29,0,0,1,.22-.09h.51s0,0,0,0v8.11a.15.15,0,0,1-.11.16" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M132.67,29.38v1s0,0,0,0,0,0,0,0a3.32,3.32,0,0,0-4.49.26,3.49,3.49,0,0,0,0,4.87,3.32,3.32,0,0,0,4.49.26s0,0,0,0a0,0,0,0,1,0,0v.66a.51.51,0,0,1-.31.49,4.17,4.17,0,0,1-1.86.44,4.06,4.06,0,0,1-3-1.26,4.38,4.38,0,0,1,0-6.11,4.21,4.21,0,0,1,5.15-.67l0,0" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M136.27,37.2h-.78s0,0,0,0V29a0,0,0,0,1,0,0h.78s0,0,0,0v8.19s0,0,0,0" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M140.34,32.43h2.32a0,0,0,0,1,0,0V33a.29.29,0,0,1-.09.22.32.32,0,0,1-.22.1h-2v3h2a.31.31,0,0,1,.22.09.33.33,0,0,1,.09.23v.52s0,0,0,0h-2.87a.33.33,0,0,1-.22-.09.29.29,0,0,1-.09-.22V29a0,0,0,0,1,0,0h3.14s0,0,0,0v.79s0,0,0,0h-2.32Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M146.38,32.84h1.86a1.27,1.27,0,0,0,1.3-.87,1.79,1.79,0,0,0,0-1.29,1.27,1.27,0,0,0-1.3-.87h-1.86Zm0,.87v3.46s0,0,0,0h-.78s0,0,0,0V29s0,0,0,0h2.68a2.22,2.22,0,0,1,1.34.41,2.19,2.19,0,0,1,.76,1,2.6,2.6,0,0,1,.18,1,2.55,2.55,0,0,1-.18,1,2.06,2.06,0,0,1-1.82,1.42L151,37.14s0,0,0,0,0,0,0,0h-.75a.42.42,0,0,1-.37-.19l-2.32-3.2a.22.22,0,0,0-.19-.1Z" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M154.36,30.64a3.49,3.49,0,0,0,0,4.87,3.34,3.34,0,0,0,4.79,0,3.49,3.49,0,0,0,0-4.87,3.34,3.34,0,0,0-4.79,0M161,33.07a4.22,4.22,0,0,1-1.24,3.06,4.2,4.2,0,0,1-6,0,4.38,4.38,0,0,1,0-6.11,4.2,4.2,0,0,1,6,0A4.18,4.18,0,0,1,161,33.07" transform="translate(0 -0.41)" style="fill: currentColor;stroke: currentColor;stroke-miterlimit: 10;stroke-width: 0.30000001192092896px"/><path d="M27.35,21.62a9.16,9.16,0,0,1-.44,1.75,10.09,10.09,0,0,1-9.34,6.7A10,10,0,0,1,7.69,20a10.15,10.15,0,0,1,1.8-5.78l-7.23-3A18,18,0,0,0,0,20,17.75,17.75,0,0,0,17.57,37.9a17.74,17.74,0,0,0,17-13.3Z" transform="translate(0 -0.41)" style="fill: currentColor"/></g></svg></div>
    <h1>Monitoreo normativo CMF</h1>
    <hr class="cmf-rule">
    <p class="hd-sub">Seguimiento automático diario de resoluciones normativas de la Comisión para el Mercado Financiero de Chile.</p>
  </div>
</header>

<main>

  <div id="tabs-outer">
    <button type="button" class="tabs-nav" id="tabs-izq"
            aria-label="Ver las secciones anteriores">‹</button>
    <button type="button" class="tabs-nav" id="tabs-der"
            aria-label="Ver las secciones siguientes">›</button>
    <nav id="tabs">
      <button class="tab activo" data-tab="cuadro" onclick="setTab(this)">Agenda de tareas</button>
      <button class="tab" data-tab="relevantes" onclick="setTab(this)">Cambios relevantes</button>
      <button class="tab" data-tab="revision" onclick="setTab(this)">Revisión manual __REVISION_BADGE__</button>
      <button class="tab" data-tab="listado" onclick="setTab(this)">Listado completo</button>
    </nav>
  </div>

  <div class="tab-panel" data-panel="cuadro">
    __CUADRO__
  </div>

  <div class="tab-panel" data-panel="relevantes" style="display:none">
    __RELEVANTES__
  </div>

  <div class="tab-panel" data-panel="revision" style="display:none">
    __REVISION__
  </div>

  <div class="tab-panel" data-panel="listado" style="display:none">

    <section>
      <h2>Resumen</h2>
      __STATS__
    </section>

    <section id="tabla">
      <h2>Resoluciones normativas <span class="h2-hint">Haz clic en una fila para ver el detalle</span></h2>
      __FILTROS__
      <table id="tabla-resoluciones">
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Norma</th>
            <th>Tipo de acuerdo</th>
            <th>Norma(s) afectada(s)</th>
            <th class="th-vig">Vigencia</th>
            <th class="th-link">PDF</th>
            <th class="th-revisar">Detalle</th>
          </tr>
        </thead>
        <tbody>
          __TABLA__
        </tbody>
      </table>
    </section>

    <section id="timeline">
      <h2>Línea de tiempo por NCG <span class="h2-hint">Normas afectadas más de una vez, primero las más modificadas</span></h2>
      <p class="tl-sin-resultados" style="display:none">Ninguna norma afectada calza con el filtro.</p>
      __TIMELINE__
    </section>

  </div>

</main>

<footer>
  Última actualización: __ACTUALIZADO__ ·
  Fuente: <a href="https://www.cmfchile.cl" target="_blank" rel="noopener">cmfchile.cl</a> ·
  <a href="https://www.cmfchile.cl/institucional/legislacion_normativa/normativa2.php?tiponorma=ALL&numero=&dd=&mm=&aa=&dd2=&mm2=&aa2=&buscar=&entidad_web=ALL&materia=ALL&enviado=1&hidden_mercado=%25" target="_blank" rel="noopener">Listado oficial</a>
</footer>

<script>
  function setTab(btn) {
    document.querySelectorAll('#tabs .tab').forEach(b => b.classList.remove('activo'));
    btn.classList.add('activo');
    const target = btn.dataset.tab;
    document.querySelectorAll('.tab-panel').forEach(p => {
      p.style.display = p.dataset.panel === target ? '' : 'none';
    });
    // La pestaña elegida se trae a la vista. Sin esto, al saltar al Listado
    // desde la agenda (irAListado) la pestaña activa quedaba fuera de cuadro y
    // la barra seguía mostrando "Agenda de tareas" subrayada… en otro scroll.
    // `nearest` en los dos ejes: mueve lo mínimo y no arrastra la página.
    if (btn.scrollIntoView) {
      btn.scrollIntoView({ inline: 'nearest', block: 'nearest' });
    }
    bordesTabs();
  }

  /* Enciende el difuminado del lado que tenga más contenido. Mide el overflow
     real en vez de asumir un breakpoint, así que si algún día se agrega una
     quinta pestaña la señal aparece sola, también en escritorio. */
  function bordesTabs() {
    const tabs = document.getElementById('tabs');
    const caja = document.getElementById('tabs-outer');
    if (!tabs || !caja) return;
    const max = tabs.scrollWidth - tabs.clientWidth;
    caja.classList.toggle('puede-izq', tabs.scrollLeft > 4);
    caja.classList.toggle('puede-der', tabs.scrollLeft < max - 4);
  }

  /* Corre la barra un poco menos de un ancho visible: así siempre queda a la
     vista la pestaña del borde, que es la referencia de dónde quedaste. Mismo
     criterio que `correr()` en el riel del calendario. */
  function correrTabs(dir) {
    const tabs = document.getElementById('tabs');
    if (!tabs) return;
    const quieto = matchMedia('(prefers-reduced-motion: reduce)').matches;
    tabs.scrollBy({
      left: dir * Math.max(tabs.clientWidth * 0.6, 140),
      behavior: quieto ? 'auto' : 'smooth',
    });
  }

  /* Un solo lugar decide cómo se ve un control de despliegue. Cada toggle
     escribía su propio rótulo y ya habían divergido en tres formas ('→', '↑' y
     ningún estado en la tabla del listado), que es como el mismo gesto termina
     pareciendo tres cosas distintas. El <span class="rv-txt"> existe para poder
     conservar lo que va antes, como el conteo "3 cambios ·". */
  function marcarRevisar(btn, abierto) {
    if (!btn) return;
    btn.setAttribute('aria-expanded', String(abierto));
    (btn.querySelector('.rv-txt') || btn).textContent =
      abierto ? 'Cerrar ▴' : 'Revisar ▾';
  }

  function toggleDetalleCR(btn) {
    const fila = btn.closest('tr');
    const detalle = fila.nextElementSibling;
    if (!detalle || !detalle.classList.contains('cr-detalle-row')) return;
    const abierto = detalle.dataset.open === '1';
    detalle.dataset.open = abierto ? '0' : '1';
    detalle.classList.toggle('abierto', !abierto);
    marcarRevisar(btn, !abierto);
  }

  function toggleGrupoCR(btn) {
    const grupo = btn.closest('.cr-grupo');
    const cuerpo = grupo.querySelector('.cr-cuerpo');
    if (!cuerpo) return;
    const abierto = grupo.classList.toggle('abierto');
    cuerpo.style.display = abierto ? '' : 'none';
    marcarRevisar(btn, abierto);
  }

  function toggleDetail(row) {
    const next = row.nextElementSibling;
    if (!next || !next.classList.contains('detail-row')) return;
    const open = next.dataset.open === '1';
    next.dataset.open = open ? '0' : '1';
    next.classList.toggle('abierto', !open);
    marcarRevisar(row.querySelector('.btn-revisar'), !open);
  }

  function setTipo(btn) {
    document.querySelectorAll('.filtro-btn').forEach(b => b.classList.remove('activo'));
    btn.classList.add('activo');
    aplicarFiltros();
  }

  function aplicarFiltros() {
    const activo = document.querySelector('.filtro-btn.activo');
    const tipoActivo = activo ? activo.dataset.tipo : 'todos';
    const q = (document.getElementById('search').value || '').toLowerCase().trim();
    const visibles = new Set();
    document.querySelectorAll('#tabla-resoluciones tbody tr.fila-principal').forEach(tr => {
      const tipos = (tr.dataset.tipos || '').split('|');
      const matchTipo = tipoActivo === 'todos' || tipos.includes(tipoActivo);
      const matchQ = !q || (tr.dataset.search || '').includes(q);
      const visible = matchTipo && matchQ;
      tr.style.display = visible ? '' : 'none';
      if (visible) visibles.add(tr.dataset.clave);
      const detail = tr.nextElementSibling;
      if (detail && detail.classList.contains('detail-row')) {
        // Igual que en Cambios relevantes: el estado va en la clase, nunca en
        // `style.display`, porque el display en línea le gana a la media query
        // y en celular la fila abierta tiene que ser `block`, no `table-row`.
        // `dataset.open` se conserva, así que al volver a entrar en el filtro
        // la fila reaparece abierta si lo estaba.
        detail.classList.toggle('abierto', visible && detail.dataset.open === '1');
      }
    });
    filtrarTimeline(visibles);
  }

  // La línea de tiempo vive en su propia <section> y hasta ahora el filtro no la
  // tocaba: al elegir "Circular" la tabla se reducía y abajo seguían apareciendo
  // todas las normas, sin relación con lo seleccionado. Sigue a la tabla por
  // clave en vez de repetir la lógica de filtrado.
  function filtrarTimeline(visibles) {
    let gruposVisibles = 0;
    document.querySelectorAll('#timeline .tl-norma').forEach(grupo => {
      let vistos = 0;
      grupo.querySelectorAll('.tl-item').forEach(item => {
        const on = visibles.has(item.dataset.clave);
        item.style.display = on ? '' : 'none';
        if (on) vistos++;
      });
      // Una norma sin ningún evento visible no es una línea de tiempo vacía:
      // es una norma que no aplica al filtro, y no tiene por qué figurar.
      grupo.style.display = vistos ? '' : 'none';
      if (vistos) gruposVisibles++;
      const badge = grupo.querySelector('.tl-count');
      if (badge) {
        const total = Number(badge.dataset.total || vistos);
        // "4 de 8" a secas no dice de qué son los 8 y se lee como si faltaran
        // cuatro por dibujar. La palabra "eventos" va siempre.
        badge.textContent = vistos === total
          ? vistos + ' evento' + (vistos === 1 ? '' : 's')
          : vistos + ' de ' + total + ' eventos';
      }
    });
    const vacio = document.querySelector('#timeline .tl-sin-resultados');
    if (vacio) vacio.style.display = gruposVisibles ? 'none' : '';
  }

  /* ── Agenda de tareas ─────────────────────────────────────────────── */
  (function () {
    const raiz = document.getElementById('agenda');
    if (!raiz) return;
    const AG = JSON.parse(document.getElementById('ag-datos').textContent);
    const riel = document.getElementById('ag-riel');
    const cajaFiltro = document.getElementById('ag-filtro');
    let filtro = null;

    const esc = s => String(s ?? '').replace(/[&<>"]/g,
      c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const corta = (s, n) => { s = String(s || '').trim();
      return s.length > n ? s.slice(0, n - 1) + '…' : s; };

    /* ── Centrado del riel ──────────────────────────────────────────
       Se mide con getBoundingClientRect y no con offsetLeft: offsetLeft
       cuenta desde el ancestro posicionado —.ag-riel-outer— y no desde el
       contenedor que hace scroll, así que el riel abría en el primer mes,
       que es justo el que no dice nada. El rAF espera a que el navegador
       termine de maquetar; medir antes devuelve ceros. */
    function centrarEnHoy(suave) {
      requestAnimationFrame(function () {
        const hoyEl = document.getElementById('ag-mes-hoy');
        if (!hoyEl) return;
        const r = riel.getBoundingClientRect(), h = hoyEl.getBoundingClientRect();
        const quieto = matchMedia('(prefers-reduced-motion: reduce)').matches;
        riel.scrollTo({ left: riel.scrollLeft + (h.left - r.left) - (r.width - h.width) / 2,
                        behavior: suave && !quieto ? 'smooth' : 'auto' });
        bordes();
      });
    }

    function bordes() {
      const caja = riel.parentElement;
      const max = riel.scrollWidth - riel.clientWidth;
      caja.classList.toggle('puede-izq', riel.scrollLeft > 4);
      caja.classList.toggle('puede-der', riel.scrollLeft < max - 4);
      document.getElementById('ag-izq').disabled = riel.scrollLeft <= 4;
      document.getElementById('ag-der').disabled = riel.scrollLeft >= max - 4;
    }

    function correr(dir) {
      const quieto = matchMedia('(prefers-reduced-motion: reduce)').matches;
      riel.scrollBy({ left: dir * Math.max(riel.clientWidth * 0.7, 244),
                      behavior: quieto ? 'auto' : 'smooth' });
    }

    /* ── Filtro por cuerpo normativo ────────────────────────────────
       Un solo estado manda sobre las barras y sobre el riel. Con estados
       separados divergen, que es exactamente cómo la línea de tiempo y la
       tabla del Listado llegaron a mostrar cosas distintas.

       Los meses ya apilados en un mazo no se recalculan al filtrar: el mazo
       describe los meses estructuralmente vacíos, y un mes que el filtro
       deja sin tarjetas muestra el aviso en su lugar. Recolapsar haría
       saltar el riel entero con cada clic. */
    function aplicarFiltro() {
      let visibles = 0;
      riel.querySelectorAll('.ag-mes').forEach(function (mes) {
        let n = 0;
        mes.querySelectorAll('.ag-tarea').forEach(function (t) {
          const calza = !filtro || (t.dataset.cuerpos || '').split('|').includes(filtro);
          t.hidden = !calza;
          if (calza) n++;
        });
        const propio = mes.querySelector('.ag-mes-vacio');
        const filtrado = mes.querySelector('.ag-mes-filtrado');
        const tieneTareas = mes.querySelector('.ag-tarea') !== null;
        if (filtrado) filtrado.hidden = !(filtro && tieneTareas && n === 0);
        if (propio) propio.hidden = filtro !== null && tieneTareas;
        const badge = mes.querySelector('.ag-mes-n');
        if (badge) badge.textContent = n;
        visibles += n;
      });
      raiz.querySelectorAll('.ag-medida[data-medida="tareas"] .ag-hbar').forEach(function (b) {
        b.classList.toggle('es-off', filtro !== null && b.dataset.cuerpo !== filtro);
      });
      const total = cajaFiltro.dataset.total;
      cajaFiltro.innerHTML = filtro
        ? '<span class="ag-filtro-pill">' + esc(filtro) + ' · ' + visibles +
          (visibles === 1 ? ' hito' : ' hitos') +
          ' <button type="button" aria-label="Quitar el filtro">×</button></span>' +
          '<span>El calendario y el conteo siguen al filtro.</span>'
        : '<span>Sin filtro · ' + total + ' hitos en la ventana. ' +
          'Haz clic en una barra de «Por cuerpo normativo» para acotar.</span>';
      centrarEnHoy(false);
    }

    /* ── Buscador ───────────────────────────────────────────────────
       Busca sobre TODO el corpus y no sobre la ventana del calendario: si
       mirara sólo los meses del riel, «no hay cambios normativos en
       relación con el archivo consultado» sería falso para casi todo. */
    const IX = AG.indice;
    /* Sólo se quita el símbolo de grado, no la letra que lo precede: con
       n[°ºo] la «no» de «norma» también calzaba y quedaba en «rma». */
    const norm = s => String(s || '').toLowerCase().normalize('NFD')
      .replace(/[̀-ͯ]/g, '').replace(/[°º]/g, '')
      .replace(/[.\\s]+/g, ' ').trim();
    const ES_ARCHIVO = /^[a-z]{1,3}\\s*\\d{2,3}$/i;
    const ESTADOS = { futuro: 'por aplicarse', pasado: 'ya aplicado',
                      sinfecha: 'sin fecha', sinvigencia: 'sin vigencia declarada' };
    IX.forEach(function (e) {
      e._h = norm([e.n, e.d, e.a.join(' '), e.m.join(' '), e.g.join(' '), e.c].join(' '));
    });

    function buscar(q) {
      const n = norm(q);
      if (!n) return [];
      const codigo = ES_ARCHIVO.test(q.trim()) ? n.replace(/\\s/g, '') : null;
      return IX.map(function (e) {
        let peso = 0;
        if (codigo && e.a.some(a => norm(a).replace(/\\s/g, '') === codigo)) peso = 4;
        else if (norm(e.n).includes(n)) peso = 3;
        else if (e.m.some(m => norm(m).includes(n))) peso = 2;
        else if (e._h.includes(n)) peso = 1;
        return peso ? { e: e, peso: peso } : null;
      }).filter(Boolean)
        .sort((a, b) => b.peso - a.peso || (b.e.f || '').localeCompare(a.e.f || ''))
        .map(x => x.e);
    }

    /* El resaltado busca sobre el texto crudo, sólo insensible a mayúsculas:
       plegar acentos cambia el largo de la cadena y el <mark> queda corrido. */
    function resaltar(txt, q) {
      const t = String(txt || ''), term = q.trim();
      if (term.length < 2) return esc(t);
      const i = t.toLowerCase().indexOf(term.toLowerCase());
      if (i < 0) return esc(t);
      return esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + term.length)) +
             '</mark>' + esc(t.slice(i + term.length));
    }

    const TOPE = 12;
    function responder(q) {
      const caja = document.getElementById('ag-respuesta');
      document.getElementById('ag-q-x').hidden = !q.trim();
      if (!q.trim()) { caja.innerHTML = ''; return; }
      const hits = buscar(q), term = q.trim(), esArchivo = ES_ARCHIVO.test(term);

      let frase;
      if (!hits.length) {
        frase = esArchivo
          ? 'No hay cambios normativos en relación con el archivo consultado.'
          : 'No hay cambios normativos en relación con «' + esc(term) + '».';
      } else if (esArchivo) {
        const cabeza = 'El archivo <b>' + esc(term.toUpperCase()) + '</b> está incluido en ';
        if (hits.length === 1) {
          const h = hits[0];
          frase = cabeza + 'el cambio normativo <b>' + esc(h.n) + '</b>' +
            (h.v && h.v.slice(0, 2) === '20' ? ', con vigencia desde el ' + esc(h.v) : '') + '.';
        } else {
          frase = cabeza + '<b>' + hits.length + '</b> cambios normativos.';
        }
      } else {
        frase = hits.length === 1
          ? '«' + esc(term) + '» aparece en un cambio normativo: <b>' + esc(hits[0].n) + '</b>.'
          : '«' + esc(term) + '» aparece en <b>' + hits.length + '</b> cambios normativos.';
      }

      const filas = hits.slice(0, TOPE).map(function (e) {
        const vig = e.v && e.v.slice(0, 2) === '20' ? 'rige ' + e.v : (e.v || '—');
        const norma = e.u
          ? '<a href="' + esc(e.u) + '" target="_blank" rel="noopener">' + resaltar(e.n, term) + '</a>'
          : resaltar(e.n, term);
        return '<div class="ag-hit"><span class="ag-hit-norma">' + norma + '</span>' +
          '<span class="ag-hit-meta">' + esc(e.f) + ' · ' + esc(vig) + '</span>' +
          '<span class="ag-hit-desc">' + resaltar(corta(e.d, 130), term) + '</span>' +
          '<span class="ag-hit-chips"><span class="ag-estado es-' + e.s + '">' +
          ESTADOS[e.s] + '</span>' +
          e.a.map(a => '<span class="ag-chip es-archivo">' + resaltar(a, term) + '</span>').join('') +
          e.g.map(g => '<span class="ag-chip">' + esc(g) + '</span>').join('') +
          '</span></div>';
      }).join('');

      caja.innerHTML = '<p class="ag-frase' + (hits.length ? '' : ' es-nada') + '">' + frase + '</p>' +
        (hits.length ? '<div class="ag-hits">' + filas +
          (hits.length > TOPE
            ? '<div class="ag-hits-mas">y ' + (hits.length - TOPE) +
              ' más — afina la búsqueda para verlos.</div>'
            : '') + '</div>' : '');
    }

    const q = document.getElementById('ag-q');
    q.addEventListener('input', () => responder(q.value));
    document.getElementById('ag-q-x').addEventListener('click', function () {
      q.value = ''; responder(''); q.focus();
    });

    /* Un solo manejador para todo lo pinchable de la agenda. */
    raiz.addEventListener('click', function (ev) {
      const ejemplo = ev.target.closest('.ag-ejemplos button[data-q]');
      if (ejemplo) { q.value = ejemplo.dataset.q; return responder(ejemplo.dataset.q); }

      const medida = ev.target.closest('.ag-switch button[data-medida]');
      if (medida) {
        const cual = medida.dataset.medida;
        raiz.querySelectorAll('.ag-switch button').forEach(b =>
          b.setAttribute('aria-selected', String(b.dataset.medida === cual)));
        raiz.querySelectorAll('.ag-medida, .ag-sub[data-medida]').forEach(el => {
          el.hidden = el.dataset.medida !== cual;
        });
        return;
      }

      const barra = ev.target.closest('.ag-medida[data-medida="tareas"] .ag-hbar');
      if (barra && !barra.disabled) {
        filtro = filtro === barra.dataset.cuerpo ? null : barra.dataset.cuerpo;
        return aplicarFiltro();
      }
      if (ev.target.closest('#ag-filtro button')) { filtro = null; return aplicarFiltro(); }

      const ult = ev.target.closest('[data-ult-toggle]');
      if (ult) {
        const det = raiz.querySelector('.ag-ult-detalle');
        if (!det) return;
        det.hidden = !det.hidden;
        marcarRevisar(ult, !det.hidden);
        return;
      }

      const tl = ev.target.closest('[data-timeline]');
      if (tl) return irATimeline(tl.dataset.timeline);
      const fila = ev.target.closest('[data-fila]');
      if (fila) return irAFila(fila.dataset.fila);
    });

    /* Los saltos no duplican el detalle: llevan a donde ya está. */
    function irAListado() {
      const btn = document.querySelector('#tabs .tab[data-tab="listado"]');
      if (btn) setTab(btn);
    }
    function irAFila(clave) {
      irAListado();
      const fila = document.querySelector('#tabla tr[data-clave="' + CSS.escape(clave) + '"]');
      if (!fila) return;
      const detalle = fila.nextElementSibling;
      if (detalle && detalle.classList.contains('detail-row') && detalle.dataset.open !== '1') {
        toggleDetail(fila);
      }
      fila.scrollIntoView({ block: 'center', behavior: 'smooth' });
      fila.classList.add('nueva');
    }
    function irATimeline(norma) {
      irAListado();
      const buscada = norma.trim();
      for (const g of document.querySelectorAll('#timeline .tl-norma')) {
        const h = g.querySelector('h3');
        // El h3 lleva el nombre y además los badges de conteo y desglose, así
        // que se compara sólo el primer nodo de texto. El `?.` es por si algún
        // día el h3 queda vacío: sin él, un solo grupo así corta el bucle con
        // un TypeError y el salto deja de funcionar para todas las normas.
        if (h && (h.firstChild?.textContent || '').trim() === buscada) {
          g.scrollIntoView({ block: 'center', behavior: 'smooth' });
          return;
        }
      }
    }

    document.getElementById('ag-izq').addEventListener('click', () => correr(-1));
    document.getElementById('ag-der').addEventListener('click', () => correr(1));
    document.getElementById('ag-hoy').addEventListener('click', () => centrarEnHoy(true));
    riel.addEventListener('scroll', bordes, { passive: true });
    addEventListener('resize', bordes);
    aplicarFiltro();
  })();

  /* Las pestañas se cablean fuera del IIFE de la agenda: la barra pertenece a
     toda la página, no a una pestaña. Va al final para que el <nav> ya exista. */
  (function () {
    const tabs = document.getElementById('tabs');
    if (!tabs) return;
    tabs.addEventListener('scroll', bordesTabs, { passive: true });
    addEventListener('resize', bordesTabs);
    document.getElementById('tabs-izq').addEventListener('click', () => correrTabs(-1));
    document.getElementById('tabs-der').addEventListener('click', () => correrTabs(1));
    bordesTabs();
  })();
</script>

</body>
</html>"""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generar_html()
