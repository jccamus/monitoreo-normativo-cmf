"""Genera docs/index.html implementando los requisitos del brief
'Propuesta - Cambios Normativos.txt' para periodistas que monitorean la CMF.

Estructura en cuatro pestañas:
- **Agenda de tareas**: tres columnas (≤30 / 31–60 / 61+ días desde la fecha
  actual) con las resoluciones cuya vigencia entra a regir en cada
  horizonte, más una retrospectiva por mes de lo que ya debió aplicarse.
  Cada tarjeta muestra el tema oficial del documento (bloque REF del PDF) y
  bullets accionables con los cambios concretos extraídos por el parser.
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
from datetime import datetime, timezone
from pathlib import Path

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
    # tiene que aparecer en el cuadro de mando por esa fecha futura, aunque su
    # `inicio` global diga "inmediata".
    fuentes.extend(p for v in list(fuentes) for p in (v.get("plazos") or []))
    for v in fuentes:
        for k in ("inicio", "plazo_transicion"):
            d = _parse_iso(v.get(k))
            if d and d >= hoy:
                fechas.append(d)
    return fechas


# Meses hacia atrás que cubre la retrospectiva de la agenda.
MESES_RETROSPECTIVA = 6


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


def _clasificar_retrospectiva(
    entradas: list[dict], hoy: datetime, meses: int = MESES_RETROSPECTIVA
) -> list[tuple[str, list[dict]]]:
    """Lo que debió implementarse, agrupado por mes, hacia el pasado reciente.

    Devuelve [(clave_mes, items)] del mes más reciente al más antiguo, cubriendo
    una ventana móvil de `meses` meses. Un documento con dos plazos vencidos en
    meses distintos aparece en cada uno: son obligaciones separadas. Dentro de
    un mismo mes se muestra una sola vez.
    """
    ancla = (hoy.year * 12 + hoy.month - 1) - (meses - 1)
    por_mes: dict[str, dict[str, dict]] = {}

    for e in entradas:
        for fecha, inmediata in _fechas_vigencia(e):
            if fecha > hoy:
                continue
            indice = fecha.year * 12 + fecha.month - 1
            if indice < ancla:
                continue
            mes = f"{fecha.year:04d}-{fecha.month:02d}"
            slot = por_mes.setdefault(mes, {})
            clave = e.get("clave") or e.get("url_documento") or ""
            previo = slot.get(clave)
            # Ante dos fechas del mismo documento en el mismo mes, la primera.
            if previo and previo["_fecha_aplicacion"] <= fecha.strftime("%Y-%m-%d"):
                continue
            item = dict(e)
            item["_fecha_aplicacion"] = fecha.strftime("%Y-%m-%d")
            item["_inmediata"] = inmediata
            slot[clave] = item

    salida: list[tuple[str, list[dict]]] = []
    for mes in sorted(por_mes, reverse=True):
        items = sorted(
            por_mes[mes].values(), key=lambda x: x["_fecha_aplicacion"], reverse=True
        )
        salida.append((mes, items))
    return salida


def _clasificar_tareas(
    entradas: list[dict], hoy: datetime
) -> tuple[list[dict], list[dict], list[dict]]:
    """Reparte las entradas con vigencia futura en buckets ≤30, 31–60, 61+ días."""
    b30: list[dict] = []
    b60: list[dict] = []
    b90: list[dict] = []
    for e in entradas:
        fechas = _fechas_futuras(e, hoy)
        if not fechas:
            continue
        prox = min(fechas)
        dias = (prox - hoy).days
        item = dict(e)
        item["_fecha_aplicacion"] = prox.strftime("%Y-%m-%d")
        item["_dias_restantes"] = dias
        if dias <= 30:
            b30.append(item)
        elif dias <= 60:
            b60.append(item)
        else:
            b90.append(item)
    for b in (b30, b60, b90):
        b.sort(key=lambda x: x["_fecha_aplicacion"])
    return b30, b60, b90


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

    hoy = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    b30, b60, b90 = _clasificar_tareas(entradas, hoy)
    retrospectiva = _clasificar_retrospectiva(entradas, hoy)

    ultima_actualizacion = (
        diferenciales[0].get("generated_at", "")[:10] if diferenciales else _hoy_iso()
    )

    # Las novedades son las del archivo diario más reciente (`_cargar_dife-
    # renciales` ordena descendente). Alimentan el resaltado de filas nuevas
    # de la tabla, que hasta ahora recibía una lista vacía fija: el cálculo
    # estaba escrito y funcionando, pero nunca se le pasaba nada.
    novedades = diferenciales[0].get("new_entries", []) if diferenciales else []

    grupos = _agrupar_por_norma(entradas)
    grupos_cuerpo = _agrupar_por_cuerpo(entradas)
    html_doc = _render(
        entradas, (b30, b60, b90), retrospectiva, grupos, grupos_cuerpo, hoy,
        ultima_actualizacion, novedades,
    )
    OUTPUT.write_text(html_doc, encoding="utf-8")
    logger.info(
        "Dashboard generado: %s (%d entradas | agenda 30/60/90: %d/%d/%d | retrospectiva: %d en %d meses)",
        OUTPUT, len(entradas), len(b30), len(b60), len(b90),
        sum(len(i) for _, i in retrospectiva), len(retrospectiva),
    )


def _hoy_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Render ───────────────────────────────────────────────────────────────

def _render(
    entradas: list[dict],
    buckets: tuple[list[dict], list[dict], list[dict]],
    retrospectiva: list[tuple[str, list[dict]]],
    grupos: dict[str, list[dict]],
    grupos_cuerpo: dict[str, list[dict]],
    hoy: datetime,
    ultima_actualizacion: str,
    novedades: list[dict],
) -> str:
    cuadro_html = _render_cuadro_mando(buckets, hoy, retrospectiva)
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
        .replace("__ACTUALIZADO__", html.escape(ultima_actualizacion))
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
        f'tomar leyendo el PDF. Para anotar el resultado: '
        f'<code>data/revisiones.csv</code>.</p>'
        f'<ul class="rv-lista">{filas}</ul>{_render_revisados(entradas)}</section>'
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


def _render_retrospectiva(meses: list[tuple[str, list[dict]]], hoy: datetime) -> str:
    """Lo que debió implementarse, un bloque por mes, hacia el pasado reciente."""
    total = sum(len(items) for _, items in meses)
    intro = (
        f'<p class="rt-intro">Cambios cuya vigencia ya empezó, agrupados por el mes '
        f'en que entraron a regir. Ventana móvil de los últimos '
        f'{MESES_RETROSPECTIVA} meses.</p>'
    )
    if not meses:
        return (
            f'<section id="retrospectiva"><h2>Debió implementarse</h2>{intro}'
            f'<p class="rt-vacio">Ningún cambio entró en vigencia en los últimos '
            f'{MESES_RETROSPECTIVA} meses.</p></section>'
        )

    bloques = []
    for mes, items in meses:
        filas = "".join(
            _render_fila_cuerpo(e, fecha=e["_fecha_aplicacion"], inmediata=e.get("_inmediata"))
            for e in items
        )
        actual = " rt-mes-actual" if mes == hoy.strftime("%Y-%m") else ""
        bloques.append(
            f'<section class="rt-mes{actual}">'
            f'<header class="rt-cab"><h3>{html.escape(_mes_legible(mes))}</h3>'
            f'<span class="rt-count">{len(items)}</span></header>'
            f'<table class="cr-tabla">'
            f'<thead><tr>'
            f'<th class="cr-th-fecha">Fecha</th>'
            f'<th class="cr-th-doc">Norma</th>'
            f'<th>Tema</th>'
            f'<th class="cr-th-cambios">Cambios</th>'
            f'<th class="cr-th-pdf">PDF</th>'
            f'</tr></thead><tbody>{filas}</tbody></table>'
            f'</section>'
        )
    return (
        f'<section id="retrospectiva">'
        f'<header class="rt-head"><h2>Debió implementarse</h2>'
        f'<span class="rt-total">{total}</span></header>'
        f'{intro}{"".join(bloques)}</section>'
    )


def _render_cuadro_mando(
    buckets: tuple[list[dict], list[dict], list[dict]],
    hoy: datetime,
    retrospectiva: list[tuple[str, list[dict]]],
) -> str:
    b30, b60, b90 = buckets
    fecha_txt = html.escape(hoy.strftime("%Y-%m-%d"))
    total = len(b30) + len(b60) + len(b90)
    encabezado = (
        f'<div id="cm-encabezado">'
        f'<span><b>{total}</b> tarea{"s" if total != 1 else ""} con vigencia futura</span>'
        f'<span class="cm-hoy">Calculado al {fecha_txt}</span>'
        f'</div>'
    )
    defs = [
        ("Próximos 30 días", "col-30", "Acción inmediata", b30),
        ("Entre 31 y 60 días", "col-60", "Por planificar", b60),
        ("60 días o más", "col-90", "Mediano plazo", b90),
    ]
    vacias = "".join(_render_columna_tareas(*d) for d in defs if not d[3])
    llenas = "".join(_render_columna_tareas(*d) for d in defs if d[3])
    pila_vacias = f'<div class="cm-pila-vacias">{vacias}</div>' if vacias else ""
    return (
        f'{encabezado}<div id="cuadro-mando">{pila_vacias}{llenas}</div>'
        f'{_render_retrospectiva(retrospectiva, hoy)}'
    )


def _render_columna_tareas(
    titulo: str, cls: str, subtitulo: str, tareas: list[dict]
) -> str:
    vacia_cls = " vacia" if not tareas else ""
    if tareas:
        cards = "".join(_render_tarjeta_tarea(t) for t in tareas)
    else:
        cards = '<p class="cm-sin-tareas">Sin tareas en este plazo</p>'
    return (
        f'<div class="cm-columna {html.escape(cls)}{vacia_cls}">'
        f'<header class="cm-cab">'
        f'<div class="cm-cab-tit"><h3>{html.escape(titulo)}</h3>'
        f'<span class="cm-count">{len(tareas)}</span></div>'
        f'<span class="cm-sub">{html.escape(subtitulo)}</span>'
        f'</header>'
        f'<div class="cm-tareas">{cards}</div>'
        f'</div>'
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
        f'<button class="cr-revisar" onclick="toggleGrupoCR(this)">Revisar →</button>'
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
    plazos = (e.get("vigencia") or {}).get("plazos") or []
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
    cambios_cell = (
        f'<a class="cr-detalle-toggle" href="javascript:void(0)" '
        f'onclick="toggleDetalleCR(this)">{etiqueta_detalle} →</a>'
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
        f'<tr class="cr-detalle-row" data-open="0" style="display:none">'
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


def _render_tarjeta_tarea(t: dict) -> str:
    fecha_apl = t.get("_fecha_aplicacion", "—")
    dias = t.get("_dias_restantes", 0)
    # Negativo = ya rige, y se lee "hace N días"; positivo = falta.
    if dias == 0:
        dias_txt = "hoy"
    elif dias < 0:
        n = -dias
        dias_txt = f'hace {n} día{"s" if n != 1 else ""}'
    else:
        dias_txt = f'en {dias} día{"s" if dias != 1 else ""}'
    normas = ", ".join(_normas_afectadas(t)) or "—"
    resumen = _resumen_minimo(t)
    bullets = t.get("resumen_acciones") or []
    archivos = t.get("archivos_afectados") or []

    n = len(bullets)
    conteo_html = ""
    if n:
        conteo_html = (
            f'<p class="cm-conteo"><b>{n}</b> cambio{"s" if n != 1 else ""} '
            f'especificado{"s" if n != 1 else ""} en el documento</p>'
        )

    detalle_html = _render_detalle_tarea(bullets, archivos, t.get("vigencia"))
    tiene_detalle = bool(detalle_html)

    url = t.get("url_documento") or ""
    pdf_link = (
        f'<a class="cm-link" href="{html.escape(url)}" target="_blank" rel="noopener">PDF ↗</a>'
        if url else ""
    )
    detalle_link = (
        '<a class="cm-link cm-detalle-toggle" href="javascript:void(0)" '
        'onclick="toggleDetalleTarea(this)">Detalle de cambios →</a>'
        if tiene_detalle else ""
    )
    tipo = _tipo_tag(t.get("tipo_acuerdo", "Otro"))
    return (
        f'<article class="cm-tarea">'
        f'<header class="cm-fecha"><b>{html.escape(fecha_apl)}</b> '
        f'<span class="cm-dias">· {dias_txt}</span></header>'
        f'<div class="cm-meta">{tipo}'
        f'<span class="cm-norma">{html.escape(normas)}</span></div>'
        f'<p class="cm-resumen">{html.escape(resumen)}</p>'
        f'{conteo_html}'
        f'<div class="cm-acciones">{pdf_link}{detalle_link}</div>'
        f'{detalle_html}'
        f'</article>'
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
        return '<tr><td colspan="6" class="td-vacio">Sin datos aún.</td></tr>'

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
    tipo_principal = e.get("tipo_acuerdo", "Otro")
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
        f'<td>{html.escape(fecha)}</td>'
        f'<td class="td-doc"><b>{html.escape(documento)}</b></td>'
        f'<td>{badges}</td>'
        f'<td class="td-normas">{normas_html}</td>'
        f'<td class="td-vig">{html.escape(vigencia)}</td>'
        f'<td class="td-link">{link}</td>'
        f'</tr>'
        f'<tr class="detail-row" data-open="0"><td colspan="6">{detalle}</td></tr>'
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
    /* Antetítulo — etiqueta en mayúsculas con tracking, motivo recurrente CMF */
    .cmf-eyebrow {
      font-size: var(--fs-xs);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-caps);
      text-transform: uppercase;
      color: var(--color-brand-soft);
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
    /* Va como `body > header` y no como `header` a secas porque las columnas
       del Cuadro de mando y el aviso de revisión manual también abren un
       <header>: sin acotar, quedaban con fondo navy. */
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
    #tabs { display: flex; gap: var(--space-5);
            border-bottom: var(--border-w) solid var(--border-subtle);
            margin-bottom: var(--space-5); }
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

    /* Cuadro de mando */
    #cm-encabezado { display: flex; justify-content: space-between; align-items: center;
                     padding: var(--space-3) var(--space-1) var(--space-4);
                     font-size: var(--fs-sm); color: var(--text-body); }
    #cm-encabezado b { color: var(--text-strong); font-size: var(--fs-lg); }
    .cm-hoy { color: var(--text-muted); font-size: var(--fs-xs); }
    #cuadro-mando { display: flex; gap: var(--space-4); align-items: flex-start; }
    .cm-pila-vacias { display: flex; flex-direction: column; gap: var(--space-3);
                      flex: 0 0 auto; }
    /* Card + barra de acento superior (el borde expresivo del sistema): el
       color lo pone cada .col-* según urgencia. */
    .cm-columna { background: var(--surface-card);
                  border: var(--border-w) solid var(--border-subtle);
                  border-radius: var(--radius-md); box-shadow: var(--shadow-sm);
                  border-top: var(--accent-bar-w) solid var(--border-default);
                  display: flex; flex-direction: column; overflow: hidden;
                  flex: 1 1 0; min-width: 280px; }
    .cm-columna.vacia { flex: 0 0 auto; min-width: auto; opacity: 0.7; }
    .cm-columna.vacia .cm-tareas { padding: 0; }
    .cm-sin-tareas { padding: var(--space-2) var(--space-4); color: var(--text-muted);
                     font-size: var(--fs-xs); font-style: italic;
                     text-align: center; white-space: nowrap; }

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
    .rv-extra { font-size: var(--fs-xs); color: var(--text-muted);
                margin: var(--space-2) 0 0; }
    .rv-nota code { font-family: var(--font-mono); background: var(--cmf-white);
                    border: var(--border-w) solid var(--border-subtle);
                    border-radius: var(--radius-xs); padding: 1px 5px;
                    font-size: var(--fs-xs); }
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
    .cr-th-cambios { width: 130px; }
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
    /* `_render_detalle_tarea` envuelve su contenido en .cm-detalle, que nace
       oculto porque en las tarjetas del Cuadro de mando lo despliega la clase
       .abierto. En esta tabla el que se despliega es el <tr>, así que el div
       tiene que estar visible siempre: si no, la fila se abre vacía. */
    .cr-detalle-row .cm-detalle { display: block; margin: 0; padding-top: 0;
                                  border-top: none; }
    .cr-detalle-row > td { background: var(--cmf-ink-50) !important;
                           padding: var(--space-3) var(--space-5);
                           border-bottom: var(--border-w-thick) solid var(--border-subtle); }
    .cr-vacio { padding: var(--space-6); color: var(--text-muted);
                text-align: center; font-style: italic; }
    .cm-cab { padding: var(--space-3) var(--space-4); background: var(--cmf-ink-50);
              border-bottom: var(--border-w) solid var(--border-subtle); }
    .cm-cab-tit { display: flex; justify-content: space-between; align-items: center; }
    .cm-cab h3 { font-size: var(--fs-sm); font-weight: var(--fw-bold);
                 color: var(--text-strong); }
    /* Antetítulo del sistema: mayúsculas con tracking amplio */
    .cm-sub { font-size: var(--fs-xs); color: var(--text-muted);
              text-transform: uppercase; letter-spacing: var(--ls-caps);
              font-weight: var(--fw-semibold); }
    .cm-count { background: var(--surface-card);
                border: var(--border-w) solid var(--border-subtle);
                border-radius: var(--radius-pill); padding: 4px 10px;
                font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                letter-spacing: var(--ls-wide); line-height: 1;
                color: var(--text-body); }
    /* Retrospectiva: lo que ya debió implementarse, por mes. Iba en violeta
       para no mezclarla con la escala de urgencia futura de las columnas, y
       ese sigue siendo el criterio — pero ahora el morado es el color de
       marca y está en todas partes, así que el que distingue es el índigo
       #3F3A7E de la paleta de apoyo: misma familia, distinto rol. */
    #retrospectiva { margin-top: var(--space-6);
                     border-top: var(--border-w) solid var(--border-subtle);
                     padding-top: var(--space-5); }
    .rt-head { display: flex; align-items: center; gap: var(--space-2); }
    #retrospectiva h2 { font-size: var(--fs-body); margin: 0; border: none;
                        padding: 0; background: none; color: var(--cmf-indigo); }
    .rt-total { background: var(--cmf-indigo); color: var(--cmf-white);
                border-radius: var(--radius-pill); padding: 4px 9px;
                font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                letter-spacing: var(--ls-wide); line-height: 1; }
    .rt-intro { font-size: var(--fs-sm); color: var(--text-muted);
                margin: var(--space-2) 0 var(--space-3); max-width: 78ch;
                line-height: var(--lh-normal); }
    .rt-vacio { font-size: var(--fs-sm); color: var(--text-muted);
                font-style: italic; }
    .rt-mes { margin-bottom: var(--space-4);
              border: var(--border-w) solid var(--border-subtle);
              border-radius: var(--radius-md); box-shadow: var(--shadow-sm);
              overflow: hidden; background: var(--surface-card); }
    .rt-mes-actual { border-color: var(--cmf-indigo);
                     border-left: var(--accent-bar-w) solid var(--cmf-indigo); }
    .rt-cab { display: flex; align-items: center; gap: var(--space-2);
              padding: var(--space-2) var(--space-3); background: var(--cmf-ink-50);
              border-bottom: var(--border-w) solid var(--border-subtle); }
    .rt-cab h3 { margin: 0; font-size: var(--fs-sm); color: var(--cmf-indigo);
                 text-transform: capitalize; }
    .rt-count { background: var(--cmf-info-bg); color: var(--cmf-indigo);
                border-radius: var(--radius-pill); padding: 4px 9px;
                font-size: var(--fs-xs); font-weight: var(--fw-semibold);
                letter-spacing: var(--ls-wide); line-height: 1; }
    .rt-inm { margin-left: var(--space-1); background: var(--cmf-info-bg);
              color: var(--cmf-indigo); border-radius: var(--radius-pill);
              padding: 3px 8px; font-size: var(--fs-xs);
              font-weight: var(--fw-semibold); letter-spacing: var(--ls-wide);
              line-height: 1; }
    /* Escala de urgencia sobre los tokens funcionales: danger → warning →
       navy. El navy es el "info" del sistema, así que reemplaza al azul. */
    .col-30 { border-top-color: var(--cmf-danger); }
    .col-30 .cm-cab { background: var(--cmf-danger-bg); }
    .col-30 .cm-cab h3 { color: var(--ink-on-danger-bg); }
    .col-60 { border-top-color: var(--cmf-warning); }
    .col-60 .cm-cab { background: var(--cmf-warning-bg); }
    .col-60 .cm-cab h3 { color: var(--ink-on-warning-bg); }
    .col-90 { border-top-color: var(--cmf-navy); }
    .col-90 .cm-cab { background: var(--cmf-info-bg); }
    .col-90 .cm-cab h3 { color: var(--cmf-navy); }
    .cm-tareas { padding: var(--space-3); display: flex; flex-direction: column;
                 gap: var(--space-2); max-height: 70vh; overflow-y: auto; }
    /* Card interactiva */
    .cm-tarea { border: var(--border-w) solid var(--border-subtle);
                border-radius: var(--radius-md); padding: var(--space-2) var(--space-3);
                background: var(--surface-card); box-shadow: var(--shadow-xs);
                transition: box-shadow var(--dur-base) var(--ease-standard),
                            transform var(--dur-base) var(--ease-standard),
                            border-color var(--dur-base) var(--ease-standard); }
    .cm-tarea:hover { box-shadow: var(--shadow-md); transform: translateY(-2px);
                      border-color: var(--border-default); }
    .cm-fecha { font-size: var(--fs-sm); color: var(--color-brand);
                margin-bottom: var(--space-2); }
    .cm-fecha b { color: var(--text-strong); font-weight: var(--fw-bold); }
    .cm-dias { color: var(--text-muted); }
    .cm-meta { display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap;
               margin-bottom: var(--space-2); }
    .cm-norma { color: var(--color-brand); font-weight: var(--fw-medium);
                font-size: var(--fs-xs); }
    .cm-resumen { font-size: var(--fs-sm); color: var(--text-strong);
                  line-height: var(--lh-normal); font-weight: var(--fw-medium);
                  margin-bottom: var(--space-2); }
    .cm-conteo { font-size: var(--fs-xs); color: var(--text-muted);
                 margin: 0 0 var(--space-2); }
    .cm-conteo b { color: var(--color-brand); font-size: var(--fs-sm); }
    .cm-acciones { display: flex; gap: var(--space-3); margin-top: var(--space-2);
                   flex-wrap: wrap; }
    .cm-link { font-size: var(--fs-sm); }
    .cm-detalle-toggle { cursor: pointer; }
    .cm-detalle { display: none; margin-top: var(--space-2);
                  padding-top: var(--space-2);
                  border-top: var(--border-w) dashed var(--border-subtle); }
    .cm-detalle.abierto { display: block; }
    .cm-det-bloque { margin-bottom: var(--space-2); }
    .cm-det-bloque:last-child { margin-bottom: 0; }
    .cm-det-label { display: block; font-size: var(--fs-xs);
                    font-weight: var(--fw-bold); text-transform: uppercase;
                    letter-spacing: var(--ls-caps); color: var(--text-muted);
                    margin-bottom: var(--space-1); }
    .cm-bullets { font-size: var(--fs-xs); color: var(--text-body);
                  line-height: var(--lh-normal); padding-left: var(--space-5);
                  display: flex; flex-direction: column; gap: 3px; }
    .cm-bullets li::marker { color: var(--text-faint); }
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

    tr.detail-row { display: none; }
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

    @media (max-width: 900px) {
      /* #cuadro-mando es flex, no grid: la regla anterior fijaba
         grid-template-columns y por eso no hacía nada. */
      #cuadro-mando { flex-direction: column; }
      .cm-pila-vacias { flex-direction: row; flex-wrap: wrap; }
    }
    @media (max-width: 800px) {
      .detalle { grid-template-columns: 1fr; }
      .td-vig, .td-link { display: none; }
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

  <nav id="tabs">
    <button class="tab activo" data-tab="cuadro" onclick="setTab(this)">Agenda de tareas</button>
    <button class="tab" data-tab="relevantes" onclick="setTab(this)">Cambios relevantes</button>
    <button class="tab" data-tab="revision" onclick="setTab(this)">Revisión manual __REVISION_BADGE__</button>
    <button class="tab" data-tab="listado" onclick="setTab(this)">Listado completo</button>
  </nav>

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
            <th>Vigencia</th>
            <th>PDF</th>
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
  }

  function toggleDetalleTarea(link) {
    const tarjeta = link.closest('.cm-tarea');
    const detalle = tarjeta.querySelector('.cm-detalle');
    if (!detalle) return;
    const abierto = detalle.classList.toggle('abierto');
    link.textContent = abierto ? 'Ocultar detalle ↑' : 'Detalle de cambios →';
  }

  function toggleDetalleCR(link) {
    const fila = link.closest('tr');
    const detalle = fila.nextElementSibling;
    if (!detalle || !detalle.classList.contains('cr-detalle-row')) return;
    const abierto = detalle.dataset.open === '1';
    detalle.dataset.open = abierto ? '0' : '1';
    detalle.style.display = abierto ? 'none' : 'table-row';
  }

  function toggleGrupoCR(btn) {
    const grupo = btn.closest('.cr-grupo');
    const cuerpo = grupo.querySelector('.cr-cuerpo');
    if (!cuerpo) return;
    const abierto = grupo.classList.toggle('abierto');
    cuerpo.style.display = abierto ? '' : 'none';
    btn.textContent = abierto ? 'Cerrar ↑' : 'Revisar →';
  }

  function toggleDetail(row) {
    const next = row.nextElementSibling;
    if (!next || !next.classList.contains('detail-row')) return;
    const open = next.dataset.open === '1';
    next.dataset.open = open ? '0' : '1';
    next.style.display = open ? 'none' : 'table-row';
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
        detail.style.display = (visible && detail.dataset.open === '1') ? 'table-row' : 'none';
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
</script>

</body>
</html>"""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generar_html()
