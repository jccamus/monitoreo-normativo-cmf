"""Punto de entrada del monitoreo normativo CMF."""
import argparse
import logging
import sys

from fetch import fetch_listado, fetch_pdf
from diff import get_nuevas, commit_nuevas
from parser import parse_pdf
from store import ensamblar_entrada, guardar_diferencial
from dashboard import generar_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    args = _parse_args()

    # 1. Obtener resoluciones relevantes del sitio CMF
    resoluciones = fetch_listado(from_date=args.from_date)

    # 2. Filtrar solo las nuevas (diferencial)
    nuevas_raw = get_nuevas(resoluciones)

    if not nuevas_raw:
        logger.info("Sin novedades. No se generan cambios.")
        generar_html()
        sys.exit(0)

    # 3. Para cada resolución nueva, descargar y parsear el PDF
    entradas_procesadas = []
    for raw in nuevas_raw:
        url = raw.get("url_documento")
        if url:
            pdf_bytes = fetch_pdf(url)
            parsed = parse_pdf(pdf_bytes, url) if pdf_bytes else {"parsed": False, "url": url}
        else:
            parsed = {"parsed": False, "url": None}

        entrada = ensamblar_entrada(raw, parsed)
        entradas_procesadas.append(entrada)
        logger.info("Procesada: %s · %s", entrada["fecha"], entrada["descripcion_cmf"][:60])

    # 4. Guardar diferencial diario
    guardar_diferencial(entradas_procesadas)

    # 5. Actualizar state.json
    commit_nuevas(nuevas_raw)

    # 6. Regenerar dashboard
    generar_html()

    logger.info("Monitoreo completado. %d resoluciones nuevas.", len(entradas_procesadas))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitoreo Normativo CMF")
    parser.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Fecha de inicio para bootstrap histórico (ej: 2024-01-01)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
