"""Genera docs/index.html implementando los requisitos del brief
'Propuesta - Cambios Normativos.txt' para journalists que monitorean la CMF.

Estructura en dos pestañas:
- **Agenda de tareas**: tres columnas (30 / 60 / 90+ días desde la fecha
  actual) con las resoluciones cuya vigencia entra a regir en cada
  horizonte. Cada tarjeta muestra el tema oficial del documento (bloque
  REF del PDF) y bullets accionables con los cambios concretos extraídos
  por el parser.
- **Listado completo**: stats, filtros por tipo de acuerdo, búsqueda libre,
  tabla con detalle expandible (descripción, RAN, MSI, archivos, modifica
  por sección) y línea de tiempo agrupada por NCG.
"""
import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"
DOCS_DIR = Path(__file__).parent.parent / "docs"
OUTPUT = DOCS_DIR / "index.html"

_NCG_NUM_DESC = re.compile(r"NORMA(?:S)?\s+DE\s+CARÁCTER\s+GENERAL\s+N[°o]\s*(\d+)", re.IGNORECASE)
_NCG_NUM_SHORT = re.compile(r"\bNCG\s+N[°o]\s*(\d+)", re.IGNORECASE)
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

TIPOS_FILTRO = [
    ("todos", "Todos"),
    ("Consulta Pública", "Consulta Pública"),
    ("Prórroga Consulta Pública", "Prórroga"),
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
    counts: dict[str, int] = {}
    for e in entradas:
        t = e.get("tipo_acuerdo", "Otro")
        counts[t] = counts.get(t, 0) + 1
        if _es_derogacion(e.get("descripcion_cmf", "")):
            counts["Derogación"] = counts.get("Derogación", 0) + 1
    return counts


def _tipos_de_entrada(entrada: dict) -> list[str]:
    tipos = [entrada.get("tipo_acuerdo", "Otro")]
    if _es_derogacion(entrada.get("descripcion_cmf", "")):
        tipos.append("Derogación")
    return tipos


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


def _agrupar_por_norma(entradas: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for e in entradas:
        for norma in _normas_afectadas(e):
            grupos.setdefault(norma, []).append(e)
    for norma in grupos:
        grupos[norma].sort(key=lambda x: x.get("fecha") or "")

    def _key(item):
        m = re.search(r"\d+", item[0])
        return int(m.group()) if m else 9999

    return dict(sorted(grupos.items(), key=_key))


# ── Punto de entrada ────────────────────────────────────────────────────

def generar_html() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    diferenciales = _cargar_diferenciales()
    entradas = _flatten_entradas(diferenciales)

    hoy = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    b30, b60, b90 = _clasificar_tareas(entradas, hoy)
    retrospectiva = _clasificar_retrospectiva(entradas, hoy)

    ultima_actualizacion = (
        diferenciales[0].get("generated_at", "")[:10] if diferenciales else _hoy_iso()
    )

    grupos = _agrupar_por_norma(entradas)
    grupos_cuerpo = _agrupar_por_cuerpo(entradas)
    html_doc = _render(
        entradas, (b30, b60, b90), retrospectiva, grupos, grupos_cuerpo, hoy,
        ultima_actualizacion,
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
) -> str:
    cuadro_html = _render_cuadro_mando(buckets, hoy, retrospectiva)
    relevantes_html = _render_cambios_relevantes(grupos_cuerpo, hoy)
    revision_html = _render_revision_manual(entradas)
    n_revision = sum(1 for e in entradas if _requiere_revision(e))
    stats_html = _render_stats(_stats(entradas), len(entradas))
    filtros_html = _render_filtros()
    tabla_html = _render_tabla(entradas, [])
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
            'de vigencia. No hay nada que revisar a mano.</p></section>'
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
        f'<ul class="rv-lista">{filas}</ul></section>'
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


def _render_filtros() -> str:
    botones = []
    for tipo, label in TIPOS_FILTRO:
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
        "Prórroga Consulta Pública": "tag-prorroga",
        "Derogación": "tag-deroga",
    }.get(tipo, "tag-otro")


def _tipo_tag(tipo: str) -> str:
    return f'<span class="tag {_tipo_class(tipo)}">{html.escape(tipo)}</span>'


def _render_tabla(entradas: list[dict], novedades: list[dict]) -> str:
    if not entradas:
        return '<tr><td colspan="6" style="padding:24px;text-align:center;color:#6b7280">Sin datos aún.</td></tr>'

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
            '<span class="d-label">⚠ PDF no procesado</span>'
            '<p>El parser no pudo extraer el texto del documento. '
            'Usar el enlace al PDF para revisión manual.</p></div>'
        )

    if not bloques:
        return '<p class="d-vacio">Sin detalles adicionales en el JSON.</p>'

    return '<div class="detalle">' + "".join(bloques) + "</div>"


def _render_timeline(grupos: dict[str, list[dict]]) -> str:
    if not grupos:
        return "<p style='padding:18px;color:#6b7280'>Sin datos de línea de tiempo aún.</p>"
    bloques = []
    for norma, items in grupos.items():
        if len(items) == 0:
            continue
        items_html = "".join(
            f'<a class="tl-item" href="{html.escape(i.get("url_documento") or "")}" target="_blank" rel="noopener" '
            f'title="{html.escape((i.get("descripcion_cmf") or "")[:200])}">'
            f'<b>{html.escape(i.get("fecha","?"))}</b> · '
            f'{html.escape(i.get("tipo_acuerdo","")) }'
            f'{" · DEROGA" if _es_derogacion(i.get("descripcion_cmf","")) else ""}'
            f'</a>'
            for i in items
        )
        count = len(items)
        bloques.append(
            f'<div class="tl-norma">'
            f'<h3>{html.escape(norma)} <span class="tl-count">{count} evento{"s" if count!=1 else ""}</span></h3>'
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
  <title>Monitoreo Normativo CMF</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           font-size: 14px; color: #222; background: #f7f8fa; }
    a { color: #1a56db; }

    header { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 20px 24px; }
    header h1 { font-size: 22px; font-weight: 700; color: #111; }
    header p { color: #6b7280; margin-top: 4px; font-size: 13px; }

    main { max-width: 1280px; margin: 24px auto; padding: 0 16px; }
    section { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
              margin-bottom: 24px; overflow: hidden; }
    section h2 { font-size: 15px; font-weight: 600; padding: 14px 18px;
                 border-bottom: 1px solid #e5e7eb; background: #f9fafb;
                 display: flex; align-items: center; justify-content: space-between; }

    /* Tabs */
    #tabs { display: flex; gap: 4px; border-bottom: 1px solid #e5e7eb;
            margin-bottom: 20px; }
    .tab { background: transparent; border: none; padding: 10px 18px;
           font-size: 14px; font-weight: 500; color: #6b7280; cursor: pointer;
           border-bottom: 3px solid transparent; }
    .tab:hover { color: #111; }
    .tab.activo { color: #1a56db; border-bottom-color: #1a56db; font-weight: 600; }
    .tab-panel { animation: fadeIn 0.15s ease-out; }
    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

    /* Cuadro de mando */
    #cm-encabezado { display: flex; justify-content: space-between; align-items: center;
                     padding: 12px 4px 16px; font-size: 13px; color: #4b5563; }
    #cm-encabezado b { color: #111; font-size: 16px; }
    .cm-hoy { color: #6b7280; font-size: 12px; }
    #cuadro-mando { display: flex; gap: 16px; align-items: flex-start; }
    .cm-pila-vacias { display: flex; flex-direction: column; gap: 12px;
                      flex: 0 0 auto; }
    .cm-columna { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
                  display: flex; flex-direction: column; overflow: hidden;
                  flex: 1 1 0; min-width: 280px; }
    .cm-columna.vacia { flex: 0 0 auto; min-width: auto; opacity: 0.7; }
    .cm-columna.vacia .cm-tareas { padding: 0; }
    .cm-sin-tareas { padding: 10px 16px; color: #6b7280; font-size: 11.5px;
                     font-style: italic; text-align: center; white-space: nowrap; }

    /* Revisión manual: cambios de archivo sin fecha de vigencia */
    .tab-badge { background: #f59e0b; color: #fff; border-radius: 10px;
                 padding: 1px 7px; font-size: 10.5px; font-weight: 600;
                 margin-left: 5px; vertical-align: 1px; }
    #revision-manual { background: #fffbeb; border: 1px solid #fde68a;
                       border-radius: 8px; padding: 16px 18px; }
    #revision-manual header { display: flex; align-items: center; gap: 10px;
                              margin-bottom: 2px; }
    #revision-manual h2 { font-size: 15px; color: #92400e; margin: 0;
                          border: none; padding: 0; }
    .rv-count { background: #f59e0b; color: #fff; border-radius: 10px;
                padding: 1px 8px; font-size: 11px; font-weight: 600; }
    .rv-nota { font-size: 12px; color: #78350f; margin: 6px 0 12px; max-width: 78ch;
               line-height: 1.5; }
    .rv-vacio { background: #f0fdf4; border-color: #bbf7d0; }
    .rv-vacio h2 { color: #166534; }
    .rv-vacio .rv-nota { color: #166534; margin-bottom: 0; }
    .rv-lista { list-style: none; margin: 0; padding: 0; }
    .rv-lista li { display: flex; align-items: baseline; gap: 10px; padding: 8px 0;
                   border-top: 1px solid #fde68a; font-size: 12px; }
    .rv-fecha { color: #92400e; font-variant-numeric: tabular-nums; flex: 0 0 82px; }
    .rv-doc { flex: 0 0 132px; font-weight: 600; color: #7c2d12; font-size: 11.5px; }
    .rv-arch { flex: 0 0 auto; display: flex; gap: 4px; flex-wrap: wrap; }
    .rv-tema { color: #444; flex: 1 1 auto; }
    .rv-pdf { flex: 0 0 auto; color: #92400e; text-decoration: none; }
    .rv-extra { font-size: 11.5px; color: #78350f; margin: 8px 0 0; }
    .rv-cand { margin-top: 4px; }
    .rv-cand-lbl { font-size: 10.5px; color: #a16207; text-transform: uppercase;
                   letter-spacing: 0.3px; }
    .rv-cand ul { list-style: none; margin: 2px 0 0; padding: 0; }
    .rv-cand li { font-size: 11px; color: #6b7280; padding: 1px 0; border: 0; }
    .rv-cand b { color: #92400e; font-variant-numeric: tabular-nums; }

    /* Cambios relevantes */
    #cambios-relevantes { display: flex; flex-direction: column; gap: 16px; }
    .cr-intro { padding: 0 4px 4px; font-size: 13px; color: #4b5563; }
    .cr-grupo { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
                overflow: hidden; }
    .cr-grupo.abierto { border-color: #c7d2fe; }
    .cr-cab { padding: 14px 18px; background: #f9fafb;
              transition: background 0.15s; }
    .cr-grupo.abierto .cr-cab { border-bottom: 1px solid #e5e7eb;
                                 background: #eef2ff; }
    .cr-cab-tit { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .cr-cab h2 { font-size: 16px; font-weight: 700; color: #111; }
    .cr-count { background: #1a56db; color: #fff; border-radius: 999px;
                padding: 1px 10px; font-size: 12px; font-weight: 700; }
    .cr-total { font-size: 11px; color: #6b7280; }
    .cr-revisar { margin-left: auto; background: #fff; border: 1px solid #d1d5db;
                  color: #1a56db; font-weight: 600; font-size: 12px;
                  padding: 5px 14px; border-radius: 6px; cursor: pointer; }
    .cr-revisar:hover { background: #1a56db; color: #fff; border-color: #1a56db; }
    .cr-grupo.abierto .cr-revisar { background: #1a56db; color: #fff;
                                     border-color: #1a56db; }
    .cr-desc { font-size: 12px; color: #6b7280; margin-top: 4px; }
    .cr-tabla { width: 100%; border-collapse: collapse; font-size: 13px; }
    .cr-tabla th { background: #fbfcfd; text-align: left; padding: 9px 14px;
                   font-weight: 600; border-bottom: 1px solid #e5e7eb;
                   font-size: 11px; text-transform: uppercase;
                   letter-spacing: 0.04em; color: #4b5563; }
    .cr-tabla td { padding: 9px 14px; border-bottom: 1px solid #f3f4f6;
                   vertical-align: top; }
    .cr-th-fecha { width: 110px; }
    .cr-th-cambios { width: 130px; }
    .cr-th-pdf { width: 80px; text-align: right; }
    .cr-td-fecha { color: #4b5563; white-space: nowrap;
                   font-variant-numeric: tabular-nums; }
    .td-doc { white-space: nowrap; }
    .cr-th-doc { width: 140px; }
    .cr-td-doc { color: #374151; font-weight: 600; font-size: 12px;
                 white-space: nowrap; }
    .cr-td-tema { color: #111; line-height: 1.45; }
    .cr-td-cambios { font-size: 12px; }
    .cr-td-cambios b { color: #1a56db; font-size: 13px; margin-right: 2px; }
    .cr-td-pdf { text-align: right; }
    .cr-td-pdf a { text-decoration: none; }
    .cr-td-pdf a:hover { text-decoration: underline; }
    .cr-detalle-toggle { cursor: pointer; color: #1a56db; }
    .cr-sin-detalle { color: #9ca3af; }
    /* `_render_detalle_tarea` envuelve su contenido en .cm-detalle, que nace
       oculto porque en las tarjetas del Cuadro de mando lo despliega la clase
       .abierto. En esta tabla el que se despliega es el <tr>, así que el div
       tiene que estar visible siempre: si no, la fila se abre vacía. */
    .cr-detalle-row .cm-detalle { display: block; margin: 0; padding-top: 0;
                                  border-top: none; }
    .cr-detalle-row > td { background: #fafbfc !important; padding: 14px 24px;
                           border-bottom: 2px solid #e5e7eb; }
    .cr-vacio { padding: 32px; color: #9ca3af; text-align: center;
                font-style: italic; }
    .cm-cab { padding: 12px 16px; border-bottom: 1px solid #e5e7eb; }
    .cm-cab-tit { display: flex; justify-content: space-between; align-items: center; }
    .cm-cab h3 { font-size: 14px; font-weight: 700; color: #111; }
    .cm-sub { font-size: 11px; color: #6b7280; text-transform: uppercase;
              letter-spacing: 0.04em; }
    .cm-count { background: #fff; border: 1px solid #e5e7eb; border-radius: 999px;
                padding: 1px 10px; font-size: 12px; font-weight: 700; color: #374151; }
    /* Retrospectiva: lo que ya debió implementarse, por mes. Violeta para no
       mezclarla con la escala rojo→azul de urgencia futura de las columnas. */
    #retrospectiva { margin-top: 28px; border-top: 1px solid #e5e7eb;
                     padding-top: 20px; }
    .rt-head { display: flex; align-items: center; gap: 10px; }
    #retrospectiva h2 { font-size: 15px; margin: 0; border: none; padding: 0;
                        color: #5b21b6; }
    .rt-total { background: #7c3aed; color: #fff; border-radius: 10px;
                padding: 1px 8px; font-size: 11px; font-weight: 600; }
    .rt-intro { font-size: 12px; color: #6b7280; margin: 6px 0 14px;
                max-width: 78ch; line-height: 1.5; }
    .rt-vacio { font-size: 12px; color: #6b7280; font-style: italic; }
    .rt-mes { margin-bottom: 16px; border: 1px solid #e5e7eb; border-radius: 8px;
              overflow: hidden; background: #fff; }
    .rt-mes-actual { border-color: #ddd6fe; box-shadow: 0 0 0 2px #f5f3ff; }
    .rt-cab { display: flex; align-items: center; gap: 8px; padding: 9px 14px;
              background: #faf9fc; border-bottom: 1px solid #e5e7eb; }
    .rt-cab h3 { margin: 0; font-size: 13px; color: #4c1d95;
                 text-transform: capitalize; }
    .rt-count { background: #ede9fe; color: #5b21b6; border-radius: 10px;
                padding: 0 7px; font-size: 11px; font-weight: 600; }
    .rt-inm { margin-left: 6px; background: #ede9fe; color: #5b21b6;
              border-radius: 3px; padding: 0 5px; font-size: 10px;
              font-weight: 600; }
    .col-30 .cm-cab { background: #fef2f2; border-color: #fecaca; }
    .col-30 .cm-cab h3 { color: #991b1b; }
    .col-60 .cm-cab { background: #fffbeb; border-color: #fde68a; }
    .col-60 .cm-cab h3 { color: #92400e; }
    .col-90 .cm-cab { background: #eff6ff; border-color: #bfdbfe; }
    .col-90 .cm-cab h3 { color: #1e40af; }
    .cm-tareas { padding: 12px; display: flex; flex-direction: column; gap: 10px;
                 max-height: 70vh; overflow-y: auto; }
    .cm-tarea { border: 1px solid #e5e7eb; border-radius: 6px; padding: 10px 12px;
                background: #fafbfc; }
    .cm-tarea:hover { background: #fff; border-color: #d1d5db; }
    .cm-fecha { font-size: 12px; color: #1a56db; margin-bottom: 6px; }
    .cm-fecha b { color: #111; font-weight: 700; }
    .cm-dias { color: #6b7280; }
    .cm-meta { display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
               margin-bottom: 6px; }
    .cm-norma { color: #1a56db; font-weight: 500; font-size: 12px; }
    .cm-resumen { font-size: 12.5px; color: #111; line-height: 1.45;
                  font-weight: 500; margin-bottom: 6px; }
    .cm-conteo { font-size: 11.5px; color: #6b7280; margin: 0 0 6px; }
    .cm-conteo b { color: #1a56db; font-size: 13px; }
    .cm-acciones { display: flex; gap: 12px; margin-top: 8px; flex-wrap: wrap; }
    .cm-link { font-size: 12px; }
    .cm-detalle-toggle { cursor: pointer; }
    .cm-detalle { display: none; margin-top: 10px; padding-top: 10px;
                  border-top: 1px dashed #e5e7eb; }
    .cm-detalle.abierto { display: block; }
    .cm-det-bloque { margin-bottom: 10px; }
    .cm-det-bloque:last-child { margin-bottom: 0; }
    .cm-det-label { display: block; font-size: 10.5px; font-weight: 700;
                    text-transform: uppercase; letter-spacing: 0.05em;
                    color: #6b7280; margin-bottom: 4px; }
    .cm-bullets { font-size: 11.5px; color: #374151; line-height: 1.45;
                  padding-left: 18px;
                  display: flex; flex-direction: column; gap: 3px; }
    .cm-bullets li::marker { color: #9ca3af; }
    .cm-archivos { font-size: 11px; padding-left: 0; list-style: none;
                   display: flex; flex-direction: column; gap: 3px; }
    .cm-archivos .chip { margin-right: 4px; }

    #stats { display: flex; gap: 8px; flex-wrap: wrap; padding: 14px 18px;
             border-bottom: 1px solid #e5e7eb; background: #fbfcfd; }
    .stat { padding: 4px 12px; border-radius: 999px; font-size: 12px;
            background: #f3f4f6; color: #374151; border: 1px solid #e5e7eb; }
    .stat b { color: #111; margin-right: 4px; }

    #filtros { padding: 12px 18px; display: flex; gap: 8px; flex-wrap: wrap;
               border-bottom: 1px solid #e5e7eb; background: #f9fafb;
               align-items: center; }
    .filtro-btn { border: 1px solid #d1d5db; background: #fff; padding: 5px 12px;
                  border-radius: 6px; cursor: pointer; font-size: 12px; }
    .filtro-btn.activo { background: #1a56db; color: #fff; border-color: #1a56db; }
    #search { flex: 1; min-width: 220px; padding: 6px 10px; border: 1px solid #d1d5db;
              border-radius: 6px; font-size: 12px; }

    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { background: #f3f4f6; text-align: left; padding: 9px 12px;
         font-weight: 600; border-bottom: 1px solid #e5e7eb; font-size: 12px;
         text-transform: uppercase; letter-spacing: 0.03em; color: #4b5563; }
    td { padding: 9px 12px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }
    tr.fila-principal { cursor: pointer; }
    tr.fila-principal:hover td { background: #f9fafb; }
    tr.fila-principal.nueva td { background: #eff6ff; }
    .td-normas { color: #1a56db; font-weight: 500; }
    .td-vig { color: #4b5563; font-size: 12px; }
    .td-link a { text-decoration: none; }
    .td-link a:hover { text-decoration: underline; }

    .tag { display: inline-block; padding: 2px 8px; border-radius: 4px;
           font-size: 11px; font-weight: 600; margin-right: 4px; }
    .tag-consulta { background: #fef3c7; color: #92400e; }
    .tag-nueva    { background: #d1fae5; color: #065f46; }
    .tag-mod      { background: #dbeafe; color: #1e40af; }
    .tag-circular { background: #ede9fe; color: #5b21b6; }
    .tag-prorroga { background: #fce7f3; color: #9d174d; }
    .tag-deroga   { background: #fee2e2; color: #991b1b; }
    .tag-otro     { background: #f3f4f6; color: #374151; }

    tr.detail-row { display: none; }
    tr.detail-row > td { background: #fafbfc !important; padding: 16px 24px;
                         border-bottom: 2px solid #e5e7eb; }
    .detalle { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .d-bloque { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
                padding: 10px 14px; }
    .d-bloque.d-warn { background: #fffbeb; border-color: #fcd34d; }
    .d-label { display: block; font-size: 11px; font-weight: 700;
               text-transform: uppercase; letter-spacing: 0.05em;
               color: #6b7280; margin-bottom: 6px; }
    .d-bloque p { font-size: 12px; line-height: 1.5; color: #374151; }
    .d-bloque ul { font-size: 12px; line-height: 1.6; padding-left: 16px; color: #374151; }
    .d-msi li { color: #6b7280; font-style: italic; font-size: 11px; }
    .d-extra { font-size: 11px; color: #6b7280; margin-top: 4px; }
    .d-vacio { padding: 8px; color: #9ca3af; font-style: italic; font-size: 12px; }

    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .chip { display: inline-block; background: #eef2ff; color: #3730a3;
            border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 600; }
    .chip-crear     { background: #d1fae5; color: #065f46; }
    .chip-modificar { background: #dbeafe; color: #1e40af; }
    .chip-eliminar  { background: #fee2e2; color: #991b1b; }

    .tl-norma { padding: 12px 18px; border-bottom: 1px solid #f3f4f6; }
    .tl-norma h3 { font-size: 13px; font-weight: 600; color: #1a56db;
                   margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
    .tl-count { font-size: 11px; font-weight: 500; color: #6b7280;
                background: #f3f4f6; padding: 1px 8px; border-radius: 999px; }
    .tl-items { display: flex; flex-wrap: wrap; gap: 6px; }
    .tl-item { background: #f3f4f6; border-radius: 6px; padding: 4px 10px;
               font-size: 12px; border-left: 3px solid #1a56db;
               text-decoration: none; color: #374151; }
    .tl-item:hover { background: #e5e7eb; }

    footer { text-align: center; color: #9ca3af; font-size: 12px;
             padding: 20px; margin-top: 8px; }

    @media (max-width: 900px) {
      #cuadro-mando { grid-template-columns: 1fr; }
    }
    @media (max-width: 800px) {
      .detalle { grid-template-columns: 1fr; }
      .td-vig, .td-link { display: none; }
    }
  </style>
</head>
<body>

<header>
  <h1>Monitoreo Normativo CMF</h1>
  <p>Seguimiento automático diario de resoluciones normativas de la Comisión para el Mercado Financiero de Chile.</p>
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
      <h2>Resoluciones normativas <span style="font-size:11px;color:#9ca3af;font-weight:400">click en una fila para ver detalle</span></h2>
      __FILTROS__
      <table id="tabla-resoluciones">
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Norma</th>
            <th>Tipo de Acuerdo</th>
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

    <section>
      <h2>Línea de tiempo por NCG</h2>
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
    document.querySelectorAll('#tabla-resoluciones tbody tr.fila-principal').forEach(tr => {
      const tipos = (tr.dataset.tipos || '').split('|');
      const matchTipo = tipoActivo === 'todos' || tipos.includes(tipoActivo);
      const matchQ = !q || (tr.dataset.search || '').includes(q);
      const visible = matchTipo && matchQ;
      tr.style.display = visible ? '' : 'none';
      const detail = tr.nextElementSibling;
      if (detail && detail.classList.contains('detail-row')) {
        detail.style.display = (visible && detail.dataset.open === '1') ? 'table-row' : 'none';
      }
    });
  }
</script>

</body>
</html>"""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generar_html()
