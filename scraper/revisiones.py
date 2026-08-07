"""Anotaciones manuales de vigencia, leídas desde `data/revisiones.csv`.

Existe porque hay documentos —sobre todo oficios circulares— que declaran su
fecha de aplicación entrelazada con el ciclo de reporte ("la información
referida al cierre de agosto, y por lo tanto enviarse en septiembre de 2025").
Cuál de esas fechas rige es un juicio, no un patrón, así que lo resuelve una
persona leyendo el PDF.

**Las anotaciones viven fuera de `data/daily/` a propósito.** Editar la entrada
directamente no funciona por dos razones:

- `reparse.py` hace `entrada.update(nueva)`, así que pisa todo campo que el
  parser produce, incluida `vigencia`: la corrección se perdería en el próximo
  `--recalcular`.
- `store.guardar_diferencial` fusiona por `clave` reemplazando la entrada
  entera, así que una recarga histórica también la borraría.

Como capa aparte sobreviven a las dos cosas, y cada revisión queda como un
commit atribuible en git.

La capa se aplica **al renderizar**, no al guardar: los datos parseados quedan
intactos y la anotación manda sin necesidad de reparsear nada.
"""
import csv
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CSV_PATH = Path(__file__).parent.parent / "data" / "revisiones.csv"

# Columnas que llena la persona. El resto del archivo es contexto para poder
# decidir sin abrir el PDF, y el cargador lo ignora.
COLUMNAS_ENTRADA = ("clave", "vigencia", "sin_fecha", "archivos", "nota", "revisado")
# Columnas de contexto que escribe `revisar.py`.
COLUMNAS_CONTEXTO = (
    "norma", "fecha_documento", "archivos_detectados", "fechas_candidatas", "pdf",
)
# Las de entrada van inmediatamente después de `clave`, antes del contexto.
#
# Al revés —contexto primero— la planilla es una trampa: en los documentos sin
# fechas candidatas esa celda queda vacía y es la primera en blanco de la fila,
# así que uno escribe ahí en vez de en `vigencia`, seis columnas más allá. Pasó
# con los 7 documentos sin pistas en el primer uso real.
COLUMNAS = COLUMNAS_ENTRADA + COLUMNAS_CONTEXTO

_VERDADERO = {"si", "sí", "s", "x", "1", "true", "verdadero"}

# Un documento que no declara vigencia rige desde su publicación. Se anota como
# "inmediata" y no con la fecha del propio documento: la fecha afirmaría que el
# texto la declara, y es justamente la confusión que originó los bugs de este
# proyecto —600 resoluciones y 126 vigencias rellenadas con la fecha del
# documento—. `inmediata` dice lo que de verdad pasa, y aguas abajo se fecha con
# la publicación igual que las que el parser detecta.
_INMEDIATA = {"inmediata", "inmediato", "inm", "publicacion", "publicación"}

# Formatos que puede tener la celda al volver de Excel. Se escribe YYYY-MM-DD,
# pero Excel en español reformatea la celda al guardar y la devuelve como
# DD-MM-YYYY o DD/MM/YYYY. Rechazarlas obligaría a pelear con el formato de
# celda en cada edición.
#
# La distinción es por dónde están los cuatro dígitos del año, no por el orden
# convencional: así `2025-11-01` y `01-11-2025` nunca se confunden entre sí.
_FECHA_ANIO_PRIMERO = re.compile(r"^(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?$")
_FECHA_ANIO_ULTIMO = re.compile(r"^(?:(\d{1,2})[-/])?(\d{1,2})[-/](\d{4})$")


def _leer_filas(path: Path) -> list[dict]:
    """Filas del CSV, tolerando el separador que use Excel.

    Excel en español usa `;` como separador de listas y `,` en inglés; el
    archivo puede volver guardado con cualquiera de los dos según quién lo
    edite. Se decide por la cabecera en vez de asumir.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        cabecera = f.readline()
        f.seek(0)
        sep = ";" if cabecera.count(";") > cabecera.count(",") else ","
        return list(csv.DictReader(f, delimiter=sep))


def _parse_fecha(valor: str) -> tuple[str | None, str]:
    """Normaliza la celda a (ISO, precisión).

    Acepta con y sin día, y con el año al principio o al final:
    `2025-11-01`, `2025-11`, `01-11-2025`, `01/11/2025`, `11-2025`.

    Sin día, la fecha se normaliza al día 1 y la precisión queda en "mes", igual
    que hace el parser: el documento fija el mes y ordenar exige un día, pero
    esa precisión no se puede mostrar como si viniera del texto.
    """
    valor = valor.strip()

    m = _FECHA_ANIO_PRIMERO.match(valor)
    if m:
        año, mes, dia = m.group(1), m.group(2), m.group(3)
    else:
        m = _FECHA_ANIO_ULTIMO.match(valor)
        if not m:
            return None, "dia"
        dia, mes, año = m.group(1), m.group(2), m.group(3)

    if not 1 <= int(mes) <= 12:
        return None, "dia"
    if dia is None:
        return f"{año}-{int(mes):02d}-01", "mes"
    if not 1 <= int(dia) <= 31:
        return None, "dia"
    return f"{año}-{int(mes):02d}-{int(dia):02d}", "dia"


def _parse_archivos(valor: str) -> dict[str, str]:
    """'RDC40=2026-01-01;RDC02=2025-11-01' -> {'RDC40': '2026-01-01', ...}."""
    fechas: dict[str, str] = {}
    for par in re.split(r"[;,]", valor):
        if "=" not in par:
            continue
        codigo, _, fecha = par.partition("=")
        iso, _ = _parse_fecha(fecha)
        if iso:
            fechas[codigo.strip().upper()] = iso
        else:
            logger.warning("Fecha inválida para el archivo %r: %r", codigo, fecha)
    return fechas


def cargar(path: Path | None = None) -> dict[str, dict]:
    """Anotaciones válidas, indexadas por `clave`.

    Una fila mal formada se descarta con un aviso en vez de abortar: el
    dashboard se regenera todos los días de forma desatendida y un error de
    tipeo en la planilla no puede dejar el sitio sin construir.
    """
    path = path or CSV_PATH
    if not path.exists():
        return {}

    try:
        filas = _leer_filas(path)
    except OSError as e:
        logger.error("No se pudo leer %s: %s", path, e)
        return {}

    anotaciones: dict[str, dict] = {}
    for n, fila in enumerate(filas, start=2):
        clave = (fila.get("clave") or "").strip()
        if not clave:
            continue

        # Guardarraíl contra el error más probable: escribir la fecha en una
        # columna de contexto. Las citas que genera `revisar.py` siempre traen
        # puntos suspensivos, así que un valor sin ellos se escribió a mano.
        # Sin este aviso el dato se pierde en silencio y el documento sigue
        # figurando como pendiente sin explicación.
        candidatas = (fila.get("fechas_candidatas") or "").strip()
        if candidatas and "…" not in candidatas:
            logger.warning(
                "revisiones.csv línea %d (%s): %r está en la columna "
                "'fechas_candidatas', que es de contexto y no se lee. "
                "Si es la fecha de vigencia, va en la columna 'vigencia'.",
                n, clave, candidatas,
            )

        sin_fecha = (fila.get("sin_fecha") or "").strip().lower() in _VERDADERO
        crudo = (fila.get("vigencia") or "").strip()
        nota = (fila.get("nota") or "").strip()
        revisado = (fila.get("revisado") or "").strip()

        if not crudo and not sin_fecha:
            continue  # fila pendiente, todavía sin decidir

        anotacion: dict = {
            "sin_fecha": sin_fecha,
            "nota": nota,
            "revisado": revisado,
            "archivos": _parse_archivos(fila.get("archivos") or ""),
        }

        if crudo:
            if crudo.lower() in _INMEDIATA:
                anotacion["inicio"] = "inmediata"
                anotacion["precision"] = "dia"
            else:
                iso, precision = _parse_fecha(crudo)
                if not iso:
                    logger.warning(
                        "revisiones.csv línea %d (%s): %r no se reconoce como fecha "
                        "(se espera YYYY-MM-DD, YYYY-MM, DD-MM-YYYY o la palabra "
                        "'inmediata') — fila ignorada",
                        n, clave, crudo,
                    )
                    continue
                anotacion["inicio"] = iso
                anotacion["precision"] = precision

        anotaciones[clave] = anotacion

    if anotaciones:
        logger.info("Anotaciones manuales cargadas: %d", len(anotaciones))
    return anotaciones


def aplicar(entradas: list[dict], anotaciones: dict[str, dict]) -> None:
    """Superpone las anotaciones sobre las entradas, in situ.

    La vigencia anotada **pisa** a la parseada: quien anotó leyó el PDF. Queda
    marcada con `fuente: "revision_manual"` para que el dashboard nunca la
    presente como si viniera del parser, y con `discrepa` cuando el parser sí
    tenía una fecha y no coincide — señal de que la anotación quizá ya sobra.
    """
    if not anotaciones:
        return

    for entrada in entradas:
        anotacion = anotaciones.get(entrada.get("clave") or "")
        if not anotacion:
            continue

        entrada["_revision"] = {
            "nota": anotacion["nota"],
            "revisado": anotacion["revisado"],
            "sin_fecha": anotacion["sin_fecha"],
        }

        if anotacion["sin_fecha"]:
            # Revisado y confirmado que el documento no declara fecha. No se
            # inventa ninguna: sólo deja de figurar como pendiente.
            continue

        previa = (entrada.get("vigencia") or {}).get("inicio")
        vigencia = {
            "inicio": anotacion["inicio"],
            "fuente": "revision_manual",
        }
        if anotacion["precision"] != "dia":
            vigencia["precision"] = anotacion["precision"]
        if previa and previa not in ("no especificado", "ver texto") \
                and previa != anotacion["inicio"]:
            vigencia["discrepa"] = previa
        entrada["vigencia"] = vigencia

        for archivo in entrada.get("archivos_afectados") or []:
            codigo = (archivo.get("nombre") or "").upper()
            archivo["vigencia"] = anotacion["archivos"].get(codigo, anotacion["inicio"])


def discrepancias(entradas: list[dict]) -> list[dict]:
    """Entradas donde el parser ahora propone otra fecha que la anotada.

    Sirve para retirar anotaciones que dejaron de hacer falta cuando el parser
    aprendió a leer ese documento.
    """
    return [e for e in entradas if (e.get("vigencia") or {}).get("discrepa")]
