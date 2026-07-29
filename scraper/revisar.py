"""Genera y refresca `data/revisiones.csv`, la hoja de trabajo de revisión manual.

La planilla lleva dos clases de columnas:

- **Contexto** (`norma`, `fecha_documento`, `archivos_detectados`,
  `fechas_candidatas`, `pdf`) — las escribe esta herramienta para que se pueda
  decidir sin abrir el PDF. El cargador las ignora.
- **Entrada** (`vigencia`, `sin_fecha`, `archivos`, `nota`, `revisado`) — las
  llena la persona.

    python scraper/revisar.py              # crea o refresca la planilla
    python scraper/revisar.py --estado     # sólo informa, no escribe

Refrescar **nunca pisa lo ya escrito**: las columnas de entrada se conservan
tal cual, se agregan las filas nuevas que quedaron pendientes y se recalcula el
contexto. Las filas cuyo documento dejó de estar pendiente —porque el parser
aprendió a leerlo— se conservan igual, con una marca, para no perder el
registro de que alguien lo revisó.
"""
import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from dashboard import _etiqueta_documento, _requiere_revision
from revisiones import COLUMNAS, COLUMNAS_ENTRADA, CSV_PATH, _leer_filas

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"
# Excel en español interpreta el punto y coma como separador de columnas; con
# coma abre todo en una sola celda.
SEPARADOR = ";"


def _cargar_entradas() -> list[dict]:
    entradas: list[dict] = []
    for path in sorted(DAILY_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                entradas += json.load(f).get("new_entries", []) or []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("No se pudo leer %s: %s", path.name, e)
    return entradas


def _contexto(e: dict) -> dict:
    archivos = e.get("archivos_afectados") or []
    candidatas = (e.get("vigencia") or {}).get("candidatas") or []
    return {
        "norma": _etiqueta_documento(e),
        "fecha_documento": e.get("fecha") or "",
        "archivos_detectados": " ".join(a.get("nombre", "") for a in archivos),
        # El separador de columnas es ";", así que dentro de una celda se usa
        # " | " para no romper el archivo.
        "fechas_candidatas": " | ".join(
            f"{c.get('fecha')} {c.get('contexto', '')}" for c in candidatas
        ),
        "pdf": e.get("url_documento") or "",
    }


def refrescar(path: Path, solo_estado: bool) -> None:
    entradas = _cargar_entradas()
    if not entradas:
        logger.error("No hay entradas en %s", DAILY_DIR)
        sys.exit(1)

    por_clave = {e.get("clave"): e for e in entradas if e.get("clave")}
    pendientes = [e for e in entradas if _requiere_revision(e)]

    previas: dict[str, dict] = {}
    if path.exists():
        for fila in _leer_filas(path):
            clave = (fila.get("clave") or "").strip()
            if clave:
                previas[clave] = fila

    filas: list[dict] = []
    claves_pendientes = set()
    for e in sorted(pendientes, key=lambda x: x.get("fecha") or "", reverse=True):
        clave = e.get("clave") or ""
        claves_pendientes.add(clave)
        fila = {"clave": clave, **_contexto(e)}
        # Lo ya escrito manda: el refresco sólo actualiza el contexto.
        anterior = previas.get(clave, {})
        for col in COLUMNAS_ENTRADA[1:]:
            fila[col] = (anterior.get(col) or "").strip()
        filas.append(fila)

    # Filas que ya no están pendientes pero fueron revisadas: se conservan para
    # no perder el registro, y se marca el motivo en la nota.
    resueltas = 0
    for clave, anterior in previas.items():
        if clave in claves_pendientes:
            continue
        if not any((anterior.get(c) or "").strip() for c in COLUMNAS_ENTRADA[1:]):
            continue  # fila vacía de un pendiente que se resolvió solo
        entrada = por_clave.get(clave)
        fila = {"clave": clave}
        fila.update(_contexto(entrada) if entrada else {})
        for col in COLUMNAS_ENTRADA[1:]:
            fila[col] = (anterior.get(col) or "").strip()
        filas.append(fila)
        resueltas += 1

    nuevas = sum(
        1 for f in filas
        if f["clave"] in claves_pendientes and f["clave"] not in previas
    )
    conservadas = sum(
        1 for f in filas
        if any((f.get(c) or "") for c in COLUMNAS_ENTRADA[1:])
    )

    logger.info(
        "Pendientes %d | filas nuevas %d | con datos ya escritos %d | "
        "revisadas fuera de pendientes %d",
        len(pendientes), nuevas, conservadas, resueltas,
    )

    if solo_estado:
        logger.info("--estado: no se escribio nada")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: sin BOM, Excel en Windows abre los acentos como basura.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(COLUMNAS), delimiter=SEPARADOR)
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in COLUMNAS})

    logger.info("Planilla escrita: %s (%d filas)", path, len(filas))
    logger.info("Al terminar de completarla: python scraper/dashboard.py")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Genera o refresca la planilla de revisión manual"
    )
    ap.add_argument("--estado", action="store_true", help="Sólo informar, sin escribir")
    ap.add_argument("--salida", type=Path, default=CSV_PATH, help="Ruta del CSV")
    args = ap.parse_args()
    refrescar(args.salida, args.estado)


if __name__ == "__main__":
    main()
