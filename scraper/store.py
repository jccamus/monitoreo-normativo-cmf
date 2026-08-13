import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# El número tiene que venir precedido de la designación de NCG para contar como
# tal. Antes esto era `N[°o]\s*(\d+)`, que capturaba cualquier "N° x" de la
# descripción del listado y lo etiquetaba como NCG — y la descripción nombra
# circulares, oficios, leyes y decretos con exactamente esa misma forma. Así,
# "MODIFICA CIRCULAR N°2022" producía una «NCG N°2022», "LEY N° 18490" una
# «NCG N°20285». Sobre el histórico eran 244 normas en la línea de tiempo
# donde hay 60, con números que no existen: la NCG más alta es la 570.
# El N° es opcional porque el listado escribe tanto "NCG N°306" como "NCG 306",
# y CARACTER va sin tilde porque las descripciones vienen en mayúsculas sin
# acentuar.
_NCG_EN_DESC = re.compile(
    r"(?:NORMAS?\s+DE\s+CAR[ÁA]CTER\s+GENERAL|NCG)\s*(?:N[°o]\s*)?(\d+)",
    re.IGNORECASE,
)

# Verbos que gobiernan cada mención, para atribuirle su propia acción.
_DEROGA_VERBO = re.compile(r"DEROGA\w*", re.IGNORECASE)
_MODIFICA_VERBO = re.compile(r"MODIFIC\w*", re.IGNORECASE)

logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"

def normalizar(texto: str) -> str:
    """Mayúsculas, sin tildes y con los espacios colapsados.

    Las descripciones del listado llegan de las dos formas —`CARÁCTER` y
    `CARACTER`, con saltos de línea y espacios dobles— porque abarcan
    décadas de capturas distintas. Comparar sobre el texto crudo hace que
    el mismo caso caiga en categorías distintas según cómo se tipeó.
    """
    desc = unicodedata.normalize("NFD", (texto or "").upper())
    return " ".join("".join(c for c in desc if unicodedata.category(c) != "Mn").split())


# Patrones (ya normalizados: MAYÚSCULAS, sin tildes) → categoría. El orden
# manda: gana el primero que calce.
#
# Eran frases literales que se buscaban con `in` sobre el texto crudo, y por
# eso el artículo y las tildes decidían la categoría: la clave decía
# "MODIFICA LA NORMA DE CARÁCTER GENERAL" y las 35 descripciones que escriben
# "MODIFICA NORMA DE CARACTER GENERAL N°507" —sin «la», sin tilde— no calzaban
# y caían al centinela "Otro". El filtro «Modificación NCG» del dashboard
# mostraba 37 entradas donde hay 72.
#
# Sigue habiendo asimetría con `fetch.FRASES_CLAVE` (~35 frases de captura
# contra 5 categorías), así que "Otro" continúa siendo el caso mayoritario;
# eso es una decisión de clasificación pendiente, no un defecto de este calce.
TIPO_ACUERDO_MAP = {
    r"APRUEBA\s+(?:LA\s+)?CONSULTA\s+PUBLICA": "Consulta Pública",
    # "Prórroga Consulta Pública" se eliminó: venía del brief y en 607
    # resoluciones no hay ninguna que posponga un plazo, así que su filtro sólo
    # podía vaciar la tabla. El botón correspondiente salió de
    # `dashboard.TIPOS_FILTRO`; reponer uno exige reponer el otro.
    # Postergar la entrada en vigencia de una norma es mover una fecha de
    # cumplimiento, que es exactamente lo que este panel existe para seguir.
    # Estaba invisible: no hay frase de captura para esto —entran por
    # "APRUEBA LA CIRCULAR QUE" o "MODIFICA LA NORMA…"— y ninguna categoría lo
    # nombraba, así que caían en "Modificación NCG" u "Otro". Ojo: no es la
    # antigua "Prórroga Consulta Pública", que postergaba el plazo de una
    # consulta y de la que no hay ni un caso en 607 resoluciones.
    r"(?:POSTERG\w*|POSPON\w*|PRORROG\w*)\s+(?:LA\s+|SU\s+|EL\s+)?"
    r"(?:ENTRADA\s+EN\s+)?(?:VIGENCIA|APLICACION|PLAZO)": "Postergación de vigencia",
    # `MODIFIC\w*` y el conector opcional cubren la nominalización: la CMF
    # escribe tanto "MODIFICA LA NORMA DE CARÁCTER GENERAL N°209" como
    # "APRUEBA MODIFICACIONES **A** LA NORMA DE CARÁCTER GENERAL N°209", y con
    # `MODIFICA\s+` la segunda no calzaba —el patrón exigía un espacio donde
    # va la "C" de MODIFICACIONES—.
    r"MODIFIC\w*\s+(?:A\s+|AL\s+)?(?:LA\s+|LAS\s+)?"
    r"(?:NORMAS?\s+DE\s+CARACTER\s+GENERAL|NCG)\b": "Modificación NCG",
    r"APRUEBA\s+(?:LA\s+)?NUEVA\s+NORMATIVA": "Nueva Normativa",
    r"EMITE\s+(?:LA\s+)?CIRCULAR": "Circular",
}

_TIPO_ACUERDO_RX = tuple(
    (re.compile(patron), tipo) for patron, tipo in TIPO_ACUERDO_MAP.items()
)


def inferir_tipos_acuerdo(descripcion: str) -> list[str]:
    """Todas las categorías que calzan, no sólo la primera.

    Las categorías no son excluyentes y hay documentos que hacen dos cosas a
    la vez: la circular 2370/2026 emite una circular *y* modifica las NCG
    N°303 y N°451. Con una sola etiqueta ganaba la primera del orden y el
    documento desaparecía del otro filtro — quedaba invisible justo para
    quien buscara por el criterio equivocado.
    """
    desc = normalizar(descripcion)
    tipos = [tipo for rx, tipo in _TIPO_ACUERDO_RX if rx.search(desc)]
    return tipos or ["Otro"]


def inferir_tipo_acuerdo(descripcion: str) -> str:
    """La categoría principal: la primera que calza, para el campo guardado."""
    return inferir_tipos_acuerdo(descripcion)[0]



def ensamblar_entrada(raw: dict, parsed: dict) -> dict:
    """Combina los datos del listado HTML con el parsing del PDF."""
    # Preferir fecha exacta del PDF sobre el placeholder YYYY-01-01 del URL
    fecha_pdf = (parsed.get("resolucion") or {}).get("fecha") or parsed.get("fecha_documento")
    fecha = fecha_pdf or raw.get("fecha")

    entrada = {
        "clave": raw.get("_key", ""),
        "fecha": fecha,
        # Sólo la resolución que el PDF declara de verdad. Antes, cuando no
        # había ninguna, se rellenaba con el número sacado del nombre del
        # archivo —que es el número del propio documento, no de una
        # resolución— y quedaba una "Resolución Exenta N°2.370" que no existe.
        # Eran 600 de 607 entradas, y el dashboard las mostraba bajo el
        # encabezado "N° Resolución".
        #
        # La identidad del documento vive en `documento`; la fecha no depende
        # de este campo (usa `parsed["resolucion"]`, no el relleno).
        "resolucion": parsed.get("resolucion"),
        "sesion": parsed.get("sesion"),
        "ncg": parsed.get("ncg"),
        "documento": parsed.get("documento"),
        "tipo_acuerdo": inferir_tipo_acuerdo(raw.get("descripcion", "")),
        "descripcion_cmf": raw.get("descripcion", ""),
        "url_documento": raw.get("url_documento"),
        "parsed": parsed.get("parsed", False),
    }

    # Estos campos se guardan SIEMPRE, no sólo cuando parsed=True.
    #
    # `parsed` es False cuando el PDF no dio ni un `ncg` ni un `modifica[]`, que
    # es el caso de casi toda circular u oficio circular. Pero ese documento
    # igual pudo aportar vigencia, capítulos RAN, referencias MSI o el bloque
    # REF. Condicionar el guardado al flag tiraba todo eso a la basura: el
    # dashboard construye la Agenda de tareas a partir de `vigencia` y las
    # tarjetas a partir de `tema`/`resumen_acciones`, así que las pestañas
    # quedaban vacías para el 83% de las entradas.
    modifica = parsed.get("modifica") or []
    # Fallback: extraer norma afectada desde descripcion_cmf cuando el PDF no la detecta
    if not modifica:
        modifica = _modifica_desde_descripcion(raw.get("descripcion", ""))

    entrada["modifica"] = modifica
    entrada["vigencia"] = parsed.get("vigencia") or {}
    entrada["ran_referencias"] = parsed.get("ran_referencias") or []
    entrada["msi_referencias"] = parsed.get("msi_referencias") or []
    entrada["archivos_afectados"] = parsed.get("archivos_afectados") or []
    entrada["tema"] = parsed.get("tema") or ""
    entrada["resumen_acciones"] = parsed.get("resumen_acciones") or []

    return entrada


def _modifica_desde_descripcion(descripcion: str) -> list[dict]:
    """Extrae normas afectadas desde la descripcion_cmf cuando el PDF no las detectó."""
    desc_upper = descripcion.upper()
    if "MODIFICA" not in desc_upper and "DEROGA" not in desc_upper:
        return []
    resultado = []
    for m in _NCG_EN_DESC.finditer(descripcion):
        # La acción se decide por norma y no para toda la descripción. Antes
        # era `"Derógase" if "DEROGA" in desc_upper else "Modifícase"`, o sea
        # en bloque: "MODIFICA NORMA DE CARÁCTER GENERAL N°152 … DEROGA OFICIO
        # CIRCULAR N°502" dejaba también la 152 como derogada. Manda el verbo
        # que gobierna a *esa* mención, que es el más cercano por la izquierda.
        previo = desc_upper[max(0, m.start() - 60):m.start()]
        deroga = _DEROGA_VERBO.search(previo)
        modifica = _MODIFICA_VERBO.search(previo)
        if deroga and (not modifica or deroga.start() > modifica.start()):
            accion = "Derógase"
        else:
            accion = "Modifícase"
        resultado.append({
            "norma": f"NCG N°{m.group(1)}",
            "numero_norma": int(m.group(1)),
            "seccion_romana": None,
            "acciones": [accion],
            "vigencia": {},
            "fuente": "descripcion_cmf",
        })
    return resultado


def guardar_diferencial(nuevas: list[dict], fecha: str | None = None) -> Path:
    """Escribe el JSON diferencial del día en data/daily/YYYY-MM-DD.json.

    Idempotente: si el archivo ya existe (workflow corrió dos veces el mismo
    día, bootstrap + run), mergea las entradas previas con las nuevas usando
    `clave` como llave de deduplicación. Las nuevas pisan a las previas para
    que un re-procesamiento corregido prevalezca.
    """
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    hoy = fecha or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DAILY_DIR / f"{hoy}.json"

    previas: list[dict] = []
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                previas = json.load(f).get("new_entries", []) or []
        except (json.JSONDecodeError, OSError) as e:
            # No perder datos: respaldar el archivo corrupto antes de
            # sobrescribirlo. El sufijo deja de terminar en .json para que
            # dashboard.py (glob "*.json") no intente parsear el respaldo.
            backup = path.with_name(
                f"{path.name}.corrupt-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
            )
            try:
                path.rename(backup)
                logger.warning(
                    "No se pudo leer %s para merge (%s); respaldado en %s",
                    path, e, backup.name,
                )
            except OSError as e2:
                logger.error("No se pudo respaldar %s corrupto: %s", path, e2)

    por_clave: dict[str, dict] = {e.get("clave", ""): e for e in previas if e.get("clave")}
    for e in nuevas:
        por_clave[e.get("clave", "")] = e
    fusionadas = [e for k, e in por_clave.items() if k]

    payload = {
        "date": hoy,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "new_entries": fusionadas,
    }

    # newline: ver el comentario de `OUTPUT.write_text` en dashboard.py.
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(
        "Diferencial guardado: %s (%d nuevas, %d previas, %d total)",
        path, len(nuevas), len(previas), len(fusionadas),
    )
    return path
