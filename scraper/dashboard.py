"""Genera docs/index.html implementando los requisitos del brief
'Propuesta - Cambios Normativos.txt' para journalists que monitorean la CMF.

Estructura en dos pestañas:
- **Cuadro de mando**: tres columnas (30 / 60 / 90+ días desde la fecha
  actual) con las resoluciones cuya vigencia entra a regir en cada
  horizonte, presentadas como tareas para la agenda del periodista.
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
    return [f"NCG N°{n}" for n in sorted(nums)]


_LABEL_INICIO = {
    "inmediata": "Inmediata",
    "cierre_mes_siguiente": "Cierre mes siguiente",
    "no especificado": "—",
    "ver texto": "Ver documento",
}


def _vigencia_fmt(v: dict | None) -> str:
    if not v:
        return "—"
    inicio = v.get("inicio") or "—"
    plazo = v.get("plazo_transicion")
    label = _LABEL_INICIO.get(inicio, inicio)
    return f"{label} · transición hasta {plazo}" if plazo else label


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
    for v in fuentes:
        for k in ("inicio", "plazo_transicion"):
            d = _parse_iso(v.get(k))
            if d and d >= hoy:
                fechas.append(d)
    return fechas


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

    ultima_actualizacion = (
        diferenciales[0].get("generated_at", "")[:10] if diferenciales else _hoy_iso()
    )

    grupos = _agrupar_por_norma(entradas)
    html_doc = _render(entradas, (b30, b60, b90), grupos, hoy, ultima_actualizacion)
    OUTPUT.write_text(html_doc, encoding="utf-8")
    logger.info(
        "Dashboard generado: %s (%d entradas · cuadro de mando: %d/%d/%d)",
        OUTPUT, len(entradas), len(b30), len(b60), len(b90),
    )


def _hoy_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Render ───────────────────────────────────────────────────────────────

def _render(
    entradas: list[dict],
    buckets: tuple[list[dict], list[dict], list[dict]],
    grupos: dict[str, list[dict]],
    hoy: datetime,
    ultima_actualizacion: str,
) -> str:
    cuadro_html = _render_cuadro_mando(buckets, hoy)
    stats_html = _render_stats(_stats(entradas), len(entradas))
    filtros_html = _render_filtros()
    tabla_html = _render_tabla(entradas, [])
    timeline_html = _render_timeline(grupos)

    return (
        _TEMPLATE
        .replace("__CUADRO__", cuadro_html)
        .replace("__STATS__", stats_html)
        .replace("__FILTROS__", filtros_html)
        .replace("__TABLA__", tabla_html)
        .replace("__TIMELINE__", timeline_html)
        .replace("__ACTUALIZADO__", html.escape(ultima_actualizacion))
    )


def _render_cuadro_mando(
    buckets: tuple[list[dict], list[dict], list[dict]], hoy: datetime
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
    columnas = (
        _render_columna_tareas("Próximos 30 días", "col-30", "Acción inmediata", b30)
        + _render_columna_tareas("Entre 31 y 60 días", "col-60", "Por planificar", b60)
        + _render_columna_tareas("60 días o más", "col-90", "Mediano plazo", b90)
    )
    return f"{encabezado}<div id=\"cuadro-mando\">{columnas}</div>"


def _render_columna_tareas(
    titulo: str, cls: str, subtitulo: str, tareas: list[dict]
) -> str:
    if not tareas:
        cards = '<p class="cm-vacio">Sin tareas en este horizonte.</p>'
    else:
        cards = "".join(_render_tarjeta_tarea(t) for t in tareas)
    return (
        f'<div class="cm-columna {html.escape(cls)}">'
        f'<header class="cm-cab">'
        f'<div class="cm-cab-tit"><h3>{html.escape(titulo)}</h3>'
        f'<span class="cm-count">{len(tareas)}</span></div>'
        f'<span class="cm-sub">{html.escape(subtitulo)}</span>'
        f'</header>'
        f'<div class="cm-tareas">{cards}</div>'
        f'</div>'
    )


def _render_tarjeta_tarea(t: dict) -> str:
    fecha_apl = t.get("_fecha_aplicacion", "—")
    dias = t.get("_dias_restantes", 0)
    dias_txt = "hoy" if dias == 0 else f'en {dias} día{"s" if dias != 1 else ""}'
    normas = ", ".join(_normas_afectadas(t)) or "—"
    desc_full = t.get("descripcion_cmf") or ""
    desc = desc_full[:160] + ("…" if len(desc_full) > 160 else "")
    archivos = t.get("archivos_afectados") or []
    archivos_html = ""
    if archivos:
        items = "".join(
            f'<li><span class="chip chip-{html.escape(a.get("accion",""))}">'
            f'{html.escape(a.get("accion","").upper())}</span> '
            f'{html.escape(a.get("nombre",""))}</li>'
            for a in archivos
        )
        archivos_html = (
            f'<div class="cm-archivos-titulo">Archivos afectados</div>'
            f'<ul class="cm-archivos">{items}</ul>'
        )
    url = t.get("url_documento") or ""
    link = (
        f'<a class="cm-link" href="{html.escape(url)}" target="_blank" rel="noopener">PDF ↗</a>'
        if url else ""
    )
    tipo = _tipo_tag(t.get("tipo_acuerdo", "Otro"))
    return (
        f'<article class="cm-tarea">'
        f'<header class="cm-fecha"><b>{html.escape(fecha_apl)}</b> '
        f'<span class="cm-dias">· {dias_txt}</span></header>'
        f'<div class="cm-meta">{tipo}'
        f'<span class="cm-norma">{html.escape(normas)}</span></div>'
        f'<p class="cm-desc">{html.escape(desc)}</p>'
        f'{archivos_html}'
        f'{link}'
        f'</article>'
    )


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
        clave, str(num_res), descripcion,
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
        f'<td><b>{html.escape(str(num_res))}</b></td>'
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

    sesion = e.get("sesion") or {}
    if sesion:
        bloques.append(
            f'<div class="d-bloque"><span class="d-label">Sesión del Consejo</span>'
            f'<p>{html.escape(sesion.get("tipo",""))} N°{html.escape(str(sesion.get("numero","")))} '
            f'· {html.escape(sesion.get("fecha","") or "—")}</p></div>'
        )

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
    #cuadro-mando { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .cm-columna { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
                  display: flex; flex-direction: column; overflow: hidden; }
    .cm-cab { padding: 12px 16px; border-bottom: 1px solid #e5e7eb; }
    .cm-cab-tit { display: flex; justify-content: space-between; align-items: center; }
    .cm-cab h3 { font-size: 14px; font-weight: 700; color: #111; }
    .cm-sub { font-size: 11px; color: #6b7280; text-transform: uppercase;
              letter-spacing: 0.04em; }
    .cm-count { background: #fff; border: 1px solid #e5e7eb; border-radius: 999px;
                padding: 1px 10px; font-size: 12px; font-weight: 700; color: #374151; }
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
    .cm-desc { font-size: 12px; color: #374151; line-height: 1.45; }
    .cm-archivos-titulo { font-size: 11px; font-weight: 700; text-transform: uppercase;
                          letter-spacing: 0.04em; color: #6b7280; margin: 8px 0 4px; }
    .cm-archivos { font-size: 11px; padding-left: 0; list-style: none;
                   display: flex; flex-direction: column; gap: 3px; }
    .cm-archivos .chip { margin-right: 4px; }
    .cm-link { display: inline-block; margin-top: 8px; font-size: 12px; }
    .cm-vacio { padding: 24px 12px; color: #9ca3af; text-align: center;
                font-style: italic; font-size: 12px; }

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
    <button class="tab activo" data-tab="cuadro" onclick="setTab(this)">Cuadro de mando</button>
    <button class="tab" data-tab="listado" onclick="setTab(this)">Listado completo</button>
  </nav>

  <div class="tab-panel" data-panel="cuadro">
    __CUADRO__
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
            <th>N° Resolución</th>
            <th>Tipo de Acuerdo</th>
            <th>Norma(s) afectada(s)</th>
            <th>Vigencia</th>
            <th>Documento</th>
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
