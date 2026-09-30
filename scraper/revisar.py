"""Genera y refresca `data/revisiones.csv`, la hoja de trabajo de revisión manual.

La planilla lleva dos clases de columnas:

- **Contexto** (`norma`, `fecha_documento`, `archivos_detectados`,
  `fechas_candidatas`, `pdf`) — las escribe esta herramienta para que se pueda
  decidir sin abrir el PDF. El cargador las ignora.
- **Entrada** (`vigencia`, `sin_fecha`, `archivos`, `nota`, `revisado`) — las
  llena la persona.

    git pull                               # 1. traer lo que capturó el robot
    python scraper/revisar.py              # 2. crea o refresca la planilla
    python scraper/revisar.py --estado     #    (sólo informa, no escribe)
    # 3. llenar la planilla en Excel
    python scraper/dashboard.py            # 4. aplicar lo anotado
    git add data/revisiones.csv docs/index.html
    git commit -m "anota la vigencia de …"
    git push                               # 5. publicar (si lo rechaza: git pull y de nuevo git push)

**Antes de refrescar, `git pull`.** El workflow captura documentos dos veces al
día y los sube a `main`; esta herramienta lee `data/daily/` del disco. Con la
copia local atrasada, la planilla sale sin los pendientes nuevos y nada lo
indica: el 30-09-2026 el oficio circular 1425 figuraba en el dashboard
publicado y no en la planilla. Por eso `refrescar` compara con GitHub y **se
detiene** si la copia está atrasada (`--sin-verificar` lo omite, por ejemplo
sin red).

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
import subprocess
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


RAIZ = Path(__file__).parent.parent


def _commits_atrasados() -> int | None:
    """Cuántos commits de `origin/main` faltan en la copia local; None si no se sabe."""
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=RAIZ, check=True,
                       capture_output=True, timeout=60)
        r = subprocess.run(["git", "rev-list", "--count", "HEAD..origin/main"], cwd=RAIZ,
                           check=True, capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _verificar_al_dia() -> None:
    """Se detiene si la copia local está atrás de GitHub: ver el docstring del módulo.

    Falla ruidosa y no aviso: un aviso entre las líneas del log se pasa por alto,
    y la planilla incompleta se ve exactamente igual que una completa.
    """
    atrasados = _commits_atrasados()
    if atrasados is None:
        logger.warning("No se pudo comparar con GitHub (¿sin red?). Si el robot capturó "
                       "algo hoy, puede faltar en la planilla: corre `git pull` antes.")
        return
    if atrasados:
        logger.error("Tu copia está %d commit%s atrás de GitHub: faltan los documentos que "
                     "capturó el robot. Corre `git pull` y vuelve a correr este comando.",
                     atrasados, "" if atrasados == 1 else "s")
        sys.exit(1)


_PASOS_SIGUIENTES = """Siguientes pasos:
  1. Abrir data/revisiones.csv en Excel y llenar vigencia (o sin_fecha = si).
  2. python scraper/dashboard.py
  3. Publicar, un comando por línea:
       git add data/revisiones.csv docs/index.html
       git commit -m "anota la vigencia de ..."
       git push
     Si git push responde "rejected": git pull, y de nuevo git push."""


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
    print(_PASOS_SIGUIENTES)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Genera o refresca la planilla de revisión manual"
    )
    ap.add_argument("--estado", action="store_true", help="Sólo informar, sin escribir")
    ap.add_argument("--salida", type=Path, default=CSV_PATH, help="Ruta del CSV")
    ap.add_argument("--sin-verificar", action="store_true",
                    help="No comparar con GitHub antes de refrescar (p. ej. sin red)")
    args = ap.parse_args()
    if not args.sin_verificar:
        _verificar_al_dia()
    refrescar(args.salida, args.estado)


if __name__ == "__main__":
    main()
