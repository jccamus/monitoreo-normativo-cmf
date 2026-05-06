"""Genera docs/index.html leyendo todos los JSONs en data/daily/."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"
DOCS_DIR = Path(__file__).parent.parent / "docs"
OUTPUT = DOCS_DIR / "index.html"


def _cargar_todas_entradas() -> tuple[list[dict], str | None]:
    """Carga y aplana todas las entradas de los JSONs diarios.

    Returns:
        (lista de entradas, fecha del último diferencial con novedades)
    """
    entradas = []
    ultima_fecha_con_novedades = None

    for path in sorted(DAILY_DIR.glob("*.json"), reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            nuevas = data.get("new_entries", [])
            if nuevas and ultima_fecha_con_novedades is None:
                ultima_fecha_con_novedades = data.get("date")
            entradas.extend(nuevas)
        except Exception as e:
            logger.warning("Error leyendo %s: %s", path, e)

    return entradas, ultima_fecha_con_novedades


def _hoy_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _novedades_hoy(entradas: list[dict]) -> list[dict]:
    hoy = _hoy_iso()
    return [e for e in entradas if e.get("fecha") == hoy or
            (e.get("resolucion") or {}).get("fecha") == hoy]


def _agrupar_por_ncg(entradas: list[dict]) -> dict[str, list[dict]]:
    """Agrupa entradas por NCG afectada para la línea de tiempo."""
    grupos: dict[str, list[dict]] = {}
    for e in entradas:
        normas_afectadas = [m["norma"] for m in e.get("modifica", [])] or [
            e.get("descripcion_cmf", "Sin norma específica")[:60]
        ]
        for norma in normas_afectadas:
            grupos.setdefault(norma, []).append(e)
    # Ordenar cada grupo cronológicamente
    for norma in grupos:
        grupos[norma].sort(key=lambda x: x.get("fecha") or "")
    return dict(sorted(grupos.items()))


def generar_html() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    entradas, ultima_fecha = _cargar_todas_entradas()
    novedades = _novedades_hoy(entradas)
    grupos_timeline = _agrupar_por_ncg(entradas)

    html = _render_html(entradas, novedades, grupos_timeline, ultima_fecha)
    OUTPUT.write_text(html, encoding="utf-8")
    logger.info("Dashboard generado: %s (%d entradas)", OUTPUT, len(entradas))


def _render_html(
    entradas: list[dict],
    novedades: list[dict],
    grupos: dict[str, list[dict]],
    ultima_fecha: str | None,
) -> str:
    hoy = _hoy_iso()
    banner_html = _render_banner(novedades, hoy) if novedades else ""
    tabla_html = _render_tabla(entradas)
    timeline_html = _render_timeline(grupos)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Monitoreo Normativo CMF</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            font-size: 14px; color: #222; background: #f7f8fa; }}
    /* Banner */
    #banner {{ background: #1a56db; color: #fff; padding: 14px 24px;
               display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
    #banner .badge {{ background: #fff; color: #1a56db; font-weight: 700;
                      border-radius: 999px; padding: 2px 10px; font-size: 13px; }}
    #banner a {{ color: #bfdbfe; text-decoration: underline; white-space: nowrap; }}
    /* Header */
    header {{ background: #fff; border-bottom: 1px solid #e5e7eb;
              padding: 20px 24px; }}
    header h1 {{ font-size: 22px; font-weight: 700; color: #111; }}
    header p  {{ color: #6b7280; margin-top: 4px; font-size: 13px; }}
    /* Contenedor */
    main {{ max-width: 1200px; margin: 24px auto; padding: 0 16px; }}
    section {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
               margin-bottom: 24px; overflow: hidden; }}
    section h2 {{ font-size: 15px; font-weight: 600; padding: 14px 18px;
                  border-bottom: 1px solid #e5e7eb; background: #f9fafb; }}
    /* Filtros */
    #filtros {{ padding: 12px 18px; display: flex; gap: 8px; flex-wrap: wrap;
                border-bottom: 1px solid #e5e7eb; background: #f9fafb; }}
    .filtro-btn {{ border: 1px solid #d1d5db; background: #fff; padding: 4px 12px;
                   border-radius: 6px; cursor: pointer; font-size: 12px; }}
    .filtro-btn.activo {{ background: #1a56db; color: #fff; border-color: #1a56db; }}
    /* Tabla */
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{ background: #f3f4f6; text-align: left; padding: 8px 12px;
          font-weight: 600; border-bottom: 1px solid #e5e7eb; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }}
    tr:hover td {{ background: #f9fafb; }}
    tr.nueva td {{ background: #eff6ff; }}
    td a {{ color: #1a56db; text-decoration: none; }}
    td a:hover {{ text-decoration: underline; }}
    .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 11px; font-weight: 600; }}
    .tag-consulta {{ background: #fef3c7; color: #92400e; }}
    .tag-nueva    {{ background: #d1fae5; color: #065f46; }}
    .tag-mod      {{ background: #dbeafe; color: #1e40af; }}
    .tag-circular {{ background: #ede9fe; color: #5b21b6; }}
    .tag-prorroga {{ background: #fce7f3; color: #9d174d; }}
    .tag-otro     {{ background: #f3f4f6; color: #374151; }}
    /* Timeline */
    .tl-norma {{ padding: 12px 18px; border-bottom: 1px solid #f3f4f6; }}
    .tl-norma h3 {{ font-size: 13px; font-weight: 600; color: #1a56db; margin-bottom: 6px; }}
    .tl-items {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .tl-item {{ background: #f3f4f6; border-radius: 6px; padding: 4px 10px;
                font-size: 12px; border-left: 3px solid #1a56db; }}
    /* Footer */
    footer {{ text-align: center; color: #9ca3af; font-size: 12px;
              padding: 20px; margin-top: 8px; }}
  </style>
</head>
<body>

{banner_html}

<header>
  <h1>Monitoreo Normativo CMF</h1>
  <p>Seguimiento automático diario de resoluciones normativas de la Comisión para el Mercado Financiero de Chile.</p>
</header>

<main>

  <!-- TABLA DE RESOLUCIONES -->
  <section>
    <h2>Resoluciones normativas</h2>
    <div id="filtros">
      <button class="filtro-btn activo" onclick="filtrar('todos',this)">Todos</button>
      <button class="filtro-btn" onclick="filtrar('Consulta Pública',this)">Consulta Pública</button>
      <button class="filtro-btn" onclick="filtrar('Modificación NCG',this)">Modificación NCG</button>
      <button class="filtro-btn" onclick="filtrar('Nueva Normativa',this)">Nueva Normativa</button>
      <button class="filtro-btn" onclick="filtrar('Circular',this)">Circular</button>
      <button class="filtro-btn" onclick="filtrar('Prórroga Consulta Pública',this)">Prórroga</button>
    </div>
    <table id="tabla-resoluciones">
      <thead>
        <tr>
          <th>Fecha</th>
          <th>N° Resolución</th>
          <th>Tipo de Acuerdo</th>
          <th>Norma(s) Afectada(s)</th>
          <th>Documento</th>
        </tr>
      </thead>
      <tbody>
        {tabla_html}
      </tbody>
    </table>
  </section>

  <!-- LÍNEA DE TIEMPO -->
  <section>
    <h2>Línea de tiempo por norma</h2>
    {timeline_html}
  </section>

</main>

<footer>
  Actualizado: {hoy} · Datos: <a href="https://www.cmfchile.cl" target="_blank">cmfchile.cl</a>
</footer>

<script>
  function filtrar(tipo, btn) {{
    document.querySelectorAll('.filtro-btn').forEach(b => b.classList.remove('activo'));
    btn.classList.add('activo');
    document.querySelectorAll('#tabla-resoluciones tbody tr').forEach(tr => {{
      tr.style.display = (tipo === 'todos' || tr.dataset.tipo === tipo) ? '' : 'none';
    }});
  }}
  // Scroll al banner si hay novedades
  document.querySelector('#banner a[href="#novedades"]')?.addEventListener('click', e => {{
    e.preventDefault();
    document.getElementById('novedades')?.scrollIntoView({{behavior:'smooth'}});
  }});
</script>

</body>
</html>"""


def _tipo_tag(tipo: str) -> str:
    clases = {
        "Consulta Pública": "tag-consulta",
        "Nueva Normativa": "tag-nueva",
        "Modificación NCG": "tag-mod",
        "Circular": "tag-circular",
        "Prórroga Consulta Pública": "tag-prorroga",
    }
    cls = clases.get(tipo, "tag-otro")
    return f'<span class="tag {cls}">{tipo}</span>'


def _render_banner(novedades: list[dict], hoy: str) -> str:
    n = len(novedades)
    return f"""<div id="banner">
  <span class="badge">{n} {'nueva' if n == 1 else 'nuevas'}</span>
  <span>Hoy {hoy} hay {n} resolución{'es' if n > 1 else ''} {'nuevas' if n > 1 else 'nueva'} en el monitoreo CMF.</span>
  <a href="#novedades">Ver en la tabla →</a>
</div>"""


def _render_tabla(entradas: list[dict]) -> str:
    hoy = _hoy_iso()
    filas = []
    for e in sorted(entradas, key=lambda x: x.get("fecha") or "", reverse=True):
        fecha = e.get("fecha") or (e.get("resolucion") or {}).get("fecha") or "—"
        res = e.get("resolucion") or {}
        num_res = res.get("numero") or e.get("clave", "—")
        tipo = e.get("tipo_acuerdo", "Otro")
        normas = ", ".join(m["norma"] for m in e.get("modifica", [])) or "—"
        url = e.get("url_documento") or ""
        link = f'<a href="{url}" target="_blank">Ver PDF</a>' if url else "—"
        es_hoy = "nueva" if fecha == hoy else ""
        id_attr = ' id="novedades"' if es_hoy and not filas else ""
        filas.append(
            f'<tr class="{es_hoy}" data-tipo="{tipo}"{id_attr}>'
            f"<td>{fecha}</td>"
            f"<td>{num_res}</td>"
            f"<td>{_tipo_tag(tipo)}</td>"
            f"<td>{normas}</td>"
            f"<td>{link}</td>"
            f"</tr>"
        )
    return "\n".join(filas) if filas else '<tr><td colspan="5">Sin datos aún.</td></tr>'


def _render_timeline(grupos: dict[str, list[dict]]) -> str:
    if not grupos:
        return "<p style='padding:18px;color:#6b7280'>Sin datos de línea de tiempo aún.</p>"
    bloques = []
    for norma, items in grupos.items():
        items_html = "".join(
            f'<span class="tl-item">{i.get("fecha","?")} · {i.get("tipo_acuerdo","")}</span>'
            for i in items
        )
        bloques.append(
            f'<div class="tl-norma">'
            f"<h3>{norma}</h3>"
            f'<div class="tl-items">{items_html}</div>'
            f"</div>"
        )
    return "\n".join(bloques)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generar_html()
