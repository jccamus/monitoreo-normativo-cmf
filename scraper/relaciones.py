"""Completa `relaciones_cmf` en las entradas ya guardadas, desde el listado de la CMF.

Existe por lo mismo que `reparse.py`: `data/state.json` impide que una
resolución ya vista se vuelva a procesar, así que un campo nuevo sólo llegaría a
lo que se capture de aquí en adelante. A diferencia de `reparse.py` no baja
ningún PDF: las relaciones («Modifica a», «Deroga a») están en el listado, que
se consulta una sola vez: la descarga del listado toma alrededor de un minuto
(~5 MB), contra los ~15 minutos que `reparse.py` necesita para 70 PDF.

Cada entrada se busca por su URL sin el parámetro `&t=`, que los enlaces
`ver_sgd.php?…` cambian en cada consulta. Reescribir el campo con el mismo
valor no toca el archivo, así que se puede correr cuantas veces haga falta —por
ejemplo, si la CMF corrige una relación en el listado—.

    python scraper/relaciones.py --dry-run         # qué entradas cambiarían
    python scraper/relaciones.py                   # escribir
    python scraper/relaciones.py --html listado.html  # usar un listado ya bajado

Regenerar el dashboard después. No toca `state.json` ni `modifica[]`.
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

from fetch import CMF_URL, _get_con_reintentos, _parse_listado

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"


def _sin_marca_de_tiempo(url: str) -> str:
    return re.sub(r"&t=\d+", "", url or "")


def _listado(html_local: str | None) -> list[dict]:
    """Todas las filas del listado, sin filtrar por frases clave.

    Sin filtrar a propósito: una entrada guardada calzó con las frases vigentes
    al capturarla, y si la lista cambió desde entonces igual tiene que
    encontrar su fila.
    """
    if html_local:
        return _parse_listado(Path(html_local).read_text(encoding="utf-8"))
    filas: list[dict] = []

    def _trae_filas(respuesta) -> bool:
        nonlocal filas
        filas = _parse_listado(respuesta.text)
        return bool(filas)

    if _get_con_reintentos(CMF_URL, validar=_trae_filas) is None:
        logger.error("El listado CMF no entregó filas — no se escribe nada")
        sys.exit(1)
    return filas


def completar(dry_run: bool, html_local: str | None = None) -> None:
    por_url = {_sin_marca_de_tiempo(f["url_documento"]): f for f in _listado(html_local)}
    logger.info("Listado: %d filas", len(por_url))

    total = cambiadas = no_encontradas = 0
    for path in sorted(DAILY_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("No se pudo leer %s: %s — se omite", path.name, e)
            continue

        modificado = False
        for entrada in payload.get("new_entries", []) or []:
            total += 1
            fila = por_url.get(_sin_marca_de_tiempo(entrada.get("url_documento")))
            if fila is None:
                no_encontradas += 1
                logger.warning("  %s no está en el listado (%s)",
                               entrada.get("clave"), entrada.get("url_documento"))
                continue
            if entrada.get("relaciones_cmf") == fila["relaciones_cmf"]:
                continue
            entrada["relaciones_cmf"] = fila["relaciones_cmf"]
            cambiadas += 1
            modificado = True

        if modificado and not dry_run:
            # newline: ver el comentario de `OUTPUT.write_text` en dashboard.py.
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info("%s reescrito", path.name)

    logger.info(
        "Entradas %d | con relaciones actualizadas %d | no encontradas en el listado %d%s",
        total, cambiadas, no_encontradas,
        " (dry-run: no se escribio nada)" if dry_run else "",
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="sólo informar, sin escribir")
    ap.add_argument("--html", help="usar un HTML del listado ya descargado en vez de consultar la CMF")
    args = ap.parse_args()
    completar(args.dry_run, args.html)
