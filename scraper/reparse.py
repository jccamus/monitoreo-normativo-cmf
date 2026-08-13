"""Re-parsea entradas ya guardadas para corregir campos que quedaron vacíos.

Existe porque `data/state.json` impide que una resolución ya vista se vuelva a
procesar: si un arreglo del parser mejora la extracción, las entradas antiguas
se quedan con los datos malos para siempre. Esta herramienta las recorre, baja
el PDF de nuevo y reescribe la entrada dentro de su archivo `data/daily/`.

Caso de uso original: la fecha del encabezado se buscaba sólo en los primeros
500 caracteres, así que los documentos con bloque `REF:` largo perdían su fecha
y caían al placeholder `YYYY-01-01` derivado de la URL. En el dashboard
aparecían como 1 de enero y la actividad reciente quedaba invisible.

Segundo caso: `parsed` se marcaba False cuando el PDF no daba ni `ncg` ni
`modifica[]`, cosa que ninguna circular u oficio circular puede dar. Con
`--degradadas` esas entradas se reprocesan para que recuperen su identidad,
vigencia y referencias.

Tercer caso: la sección de vigencia sólo se reconocía escrita como `VIGENCIA`
sola en su línea, y la CMF la titula `II. VIGENCIA`, `IV. Vigencia` o
`m. Vigencia`. Sin sección reconocida, la fecha de entrada en vigor se buscaba
en el documento entero y terminaba siendo la del encabezado. Como esas entradas
no se ven rotas —tienen una fecha de aspecto normal— hay que forzarlas con
`--recalcular`.

    python scraper/reparse.py                    # entradas con fecha placeholder desde 2024
    python scraper/reparse.py --desde 2020-01-01 # ampliar el rango
    python scraper/reparse.py --todas            # todas las placeholder, sin filtro de año
    python scraper/reparse.py --degradadas       # además, las que quedaron con parsed=False
    python scraper/reparse.py --recalcular       # todas las del rango, estén rotas o no
    python scraper/reparse.py --dry-run          # sólo informar, sin escribir

Correr siempre `--dry-run` primero, y regenerar el dashboard después.
No toca `state.json`: las claves ya vistas siguen vistas.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from fetch import _fecha_y_numero_desde_url, fetch_pdf
from parser import parse_pdf
from store import ensamblar_entrada

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"


def _es_placeholder(entrada: dict) -> bool:
    """La fecha es el placeholder YYYY-01-01 derivado del nombre del PDF.

    Un 1 de enero real es indistinguible de un placeholder, pero la CMF no
    sesiona ese día, así que en la práctica no hay falsos positivos.
    """
    return (entrada.get("fecha") or "").endswith("-01-01")


def _candidatas(
    entradas: list[dict], desde: str | None, degradadas: bool, todo: bool = False
) -> list[dict]:
    def elegible(e: dict) -> bool:
        if todo:
            return True
        if _es_placeholder(e):
            return True
        return degradadas and not e.get("parsed")

    sel = [e for e in entradas if elegible(e)]
    if desde:
        sel = [e for e in sel if (e.get("fecha") or "") >= desde]
    return sel


def reparsear(
    desde: str | None, dry_run: bool, degradadas: bool, todo: bool = False
) -> None:
    archivos = sorted(DAILY_DIR.glob("*.json"))
    if not archivos:
        logger.error("No hay archivos en %s", DAILY_DIR)
        sys.exit(1)

    total = corregidas = sin_pdf = sin_cambio = fallidas = 0

    for path in archivos:
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("No se pudo leer %s: %s — se omite", path.name, e)
            continue

        entradas = payload.get("new_entries", []) or []
        objetivo = _candidatas(entradas, desde, degradadas, todo)
        if not objetivo:
            continue

        total += len(objetivo)
        logger.info("%s: %d entradas a revisar", path.name, len(objetivo))
        modificado = False

        for entrada in objetivo:
            url = entrada.get("url_documento")
            if not url:
                sin_pdf += 1
                continue

            pdf_bytes = fetch_pdf(url)
            if not pdf_bytes:
                sin_pdf += 1
                logger.warning("  sin PDF: %s", entrada.get("clave"))
                continue

            # Un PDF corrupto no puede abortar el lote entero: sin esto, una
            # excepción a mitad de camino deja sin escribir las correcciones ya
            # calculadas para ese archivo.
            try:
                parsed = parse_pdf(pdf_bytes, url)

                # Reconstruir la fila cruda del listado para reutilizar la misma
                # lógica de ensamblado que usa el pipeline diario.
                # `numero` se deriva de la URL, igual que en la corrida diaria,
                # y no de `entrada["resolucion"]`: ese campo ahora sólo existe
                # cuando el PDF declara una resolución exenta de verdad.
                _, numero = _fecha_y_numero_desde_url(url)
                raw = {
                    "_key": entrada.get("clave", ""),
                    "fecha": entrada.get("fecha"),
                    "numero": numero,
                    "descripcion": entrada.get("descripcion_cmf", ""),
                    "url_documento": url,
                }
                nueva = ensamblar_entrada(raw, parsed)
            except Exception:
                fallidas += 1
                logger.exception("  error parseando %s — se deja intacta", entrada.get("clave"))
                continue

            # Todos los campos que el parser puede cambiar. Si falta uno, un
            # arreglo que sólo toque ese campo se descarta como "sin cambios" y
            # no llega nunca al histórico.
            campos = (
                "fecha", "parsed", "documento", "resolucion", "sesion",
                "vigencia", "tema", "modifica", "archivos_afectados",
                "ran_referencias", "msi_referencias", "resumen_acciones", "ncg",
            )
            if all(nueva.get(c) == entrada.get(c) for c in campos):
                sin_cambio += 1
                logger.info("  %s sin cambios (%s)", entrada.get("clave"), entrada.get("fecha"))
                continue

            # Sin caracteres no-ASCII: la consola de Windows usa cp1252 por
            # defecto y logging revienta al emitirlos.
            logger.info(
                "  %s  %s -> %s  (parsed %s -> %s)",
                entrada.get("clave"), entrada.get("fecha"), nueva.get("fecha"),
                entrada.get("parsed"), nueva.get("parsed"),
            )
            # update, no clear+update: `entrada` es la referencia viva dentro de
            # payload["new_entries"]. Vaciarla borraría de los archivos
            # históricos cualquier campo que no produzca ensamblar_entrada
            # (anotaciones manuales, marcas de auditoría añadidas después).
            entrada.update(nueva)
            corregidas += 1
            modificado = True

        if modificado and not dry_run:
            # newline: ver el comentario de `OUTPUT.write_text` en dashboard.py.
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info("%s reescrito", path.name)

    logger.info(
        "Revisadas %d | corregidas %d | sin cambio %d | sin PDF %d | con error %d%s",
        total, corregidas, sin_cambio, sin_pdf, fallidas,
        " (dry-run: no se escribio nada)" if dry_run else "",
    )
    if corregidas and not dry_run:
        logger.info("Regenera el dashboard con: python scraper/dashboard.py")


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-parsea entradas con fecha placeholder")
    ap.add_argument(
        "--desde", metavar="YYYY-MM-DD", default="2024-01-01",
        help="Sólo entradas con fecha >= este valor (por defecto 2024-01-01)",
    )
    ap.add_argument(
        "--todas", action="store_true",
        help="Sin filtro de año: revisa todas las entradas con placeholder",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Informa qué cambiaría sin escribir los archivos",
    )
    ap.add_argument(
        "--degradadas", action="store_true",
        help="Incluir también las entradas con parsed=False, no sólo las de fecha placeholder",
    )
    ap.add_argument(
        "--recalcular", action="store_true",
        help="Reprocesar TODAS las entradas del rango, no sólo las que se ven rotas. "
             "Necesario cuando el arreglo del parser cambia un campo que las "
             "entradas ya tenían poblado (p. ej. la vigencia).",
    )
    args = ap.parse_args()
    reparsear(
        None if args.todas else args.desde,
        args.dry_run,
        args.degradadas,
        args.recalcular,
    )


if __name__ == "__main__":
    main()
