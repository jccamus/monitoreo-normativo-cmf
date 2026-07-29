import re
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Patrones regex identificados en documentos CMF reales ──────────────────


def _frase(texto: str) -> str:
    """Frase literal convertida en patrón que tolera el salto de línea.

    Los PDF de la CMF cortan las frases donde termina el renglón, así que el
    mismo documento escribe "NORMA DE CARÁCTER GENERAL N°550" en un párrafo y
    "NORMA DE CARÁCTER\\nGENERAL N°550" en otro. Un espacio literal no cruza ese
    salto: la NCG 564/2026 abre con "REF: MODIFICA NORMA DE CARÁCTER\\nGENERAL
    N°550" y el parser no reconocía ni la norma modificada ni el número de NCG,
    en un documento cuyo único contenido es justamente esa modificación.

    Usar `\\s+` entre palabra y palabra también absorbe el doble espacio y el
    espacio duro que deja el extractor al justificar el texto.
    """
    return r"\s+".join(map(re.escape, texto.split()))


_NCG_NUM     = re.compile(
    _frase("NORMA DE CARÁCTER GENERAL") + r"\s+N[°o]\s*(\d+)", re.IGNORECASE
)
_RESOLUCION  = re.compile(
    _frase("Resolución Exenta") + r"\s+N[°o]\s*(\d+)[,.]?\s*" + _frase("de fecha")
    + r"\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    re.IGNORECASE,
)
_SESION      = re.compile(
    r"Sesión\s+(Ordinaria|Extraordinaria)\s+N[°o]\s*(\d+)\s+de\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    re.IGNORECASE,
)
_NORMA_MOD   = re.compile(
    r"(?:MODIFICACIONES?\s+(?:A\s+LA\s+)?|MODIFICA\s+(?:LA\s+)?)"
    + _frase("NORMA DE CARÁCTER GENERAL") + r"\s+N[°o]\s*(\d+)",
    re.IGNORECASE,
)
_ACCION      = re.compile(
    r"\b(Agréguese|Intercálase|Elimínese|Sustitúyase|Derógase|Modifíquese|Reemplácese|Agrégase)\b",
    re.IGNORECASE,
)
_SECCION_ROM = re.compile(r"^(I{1,3}|IV|VI{0,3}|IX|X{1,3}|XI{0,3}|XIV|XV)\.\s+", re.MULTILINE)
# `del?` porque la CMF alterna entre "24 de noviembre de 2025" y
# "24 de noviembre del 2025"; sin la variante, el oficio circular 1394/2025
# perdía su fecha de encabezado.
#
# `[°º]?` porque los plazos de vigencia casi siempre caen el día 1 y la CMF lo
# escribe como ordinal: "aplicables desde el 1° de julio de 2026". Sin esto la
# fecha más importante del documento —la única que le dice a alguien cuándo
# tiene que actuar— era justamente la que no se reconocía.
_FECHA_SPAN  = re.compile(
    r"(\d{1,2})[°º]?\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre)\s+del?\s+(\d{4})",
    re.IGNORECASE,
)
# Línea de guiones bajos que cierra el bloque REF del encabezado. Ver
# _fecha_encabezado: es el ancla para ubicar la fecha del documento.
# `(?:^|\n)` y no `\n`: según cómo el extractor trate los márgenes, la línea de
# guiones bajos puede quedar al inicio absoluto del texto, y exigir un salto
# previo hacía perder el ancla justo en esos documentos.
_SEPARADOR_ENCABEZADO = re.compile(r"(?:^|\n)\s*_{3,}\s*\n")
_MAX_BUSQUEDA_SEPARADOR = 4000   # hasta dónde buscar el separador
_VENTANA_TRAS_SEPARADOR = 300    # texto útil inmediatamente bajo el separador
_VENTANA_ENCABEZADO     = 600    # respaldo cuando no hay separador

# Identidad propia del documento (su tipo y su número, no los de las normas que
# modifica). Va en una línea que contiene SÓLO eso, bajo el bloque REF.
#
# Exigir la línea completa es lo que la distingue de las menciones a otras
# normas dentro del REF: la circular 2369/2026 abre con
# "REF: MODIFICA CIRCULAR N°1459, QUE ..." y su propio número, "CIRCULAR N°2369",
# aparece solo en su línea más abajo. Un match laxo devolvería 1459.
#
# El número admite separador de miles: la CMF escribe tanto "N°1394" como
# "N° 2.373" para el mismo tipo de documento.
#
# Los separadores son `[ \t]` y no `\s`: `\s` incluye el salto de línea, así que
# la exigencia de "línea completa" se caía justo en las menciones que el bloque
# REF parte en dos. La NCG 470/2022 abre con "DEROGA\nNORMA DE CARACTER
# GENERAL\nN°342." y se identificaba como la 342, la norma que deroga.
_DOC_IDENTIDAD = re.compile(
    r"^[ \t]*(OFICIO[ \t]+CIRCULAR|CIRCULAR|NORMA[ \t]+DE[ \t]+CAR[ÁA]CTER[ \t]+GENERAL)"
    r"[ \t]+N[°ºo][ \t]*([\d.]+)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# ── Patrones RAN / MSI ──────────────────────────────────────────────────────
_RAN_CAP = re.compile(
    r"[Cc]apítulo\s+([\w][\w.\-]*)\s+(?:de\s+(?:la\s+)?)?(?:"
    + _frase("Recopilación Actualizada de Normas") + r"|RAN\b)",
    re.IGNORECASE,
)
# La CMF alterna entre "Manual de Sistemas de Información" y "Manual del
# Sistema de Información" para el mismo manual, a veces dentro del mismo
# documento. Reconocer sólo la primera forma dejaba fuera todos los oficios
# circulares, que usan la segunda.
_MSI = re.compile(
    _frase("Manual de") + r"\s+(?:Sistemas|Sistema)\s+" + _frase("de Información"),
    re.IGNORECASE,
)

# ── Patrones archivos afectados ─────────────────────────────────────────────
#
# Los archivos normativos del MSI se identifican por un código de 1-3 letras y
# 2-3 dígitos: C11, R06, E24, D62, RDC01. Se exige la palabra "archivo" delante
# porque el código suelto se confunde con demasiadas cosas (capítulos de la RAN
# como 8-4 o 21-30, números de tablas, códigos de campo).
#
# El patrón anterior buscaba prosa genérica ("se crea X", "modifica el
# formulario X") y capturaba fragmentos de oración en vez de identificadores:
# de 154 entradas sólo 9 tenían archivos, con nombres como "un fondo" o "copia
# del poder en virtud del cual actúa". Ninguno era un archivo.
_COD_ARCHIVO = r"[A-Z]{1,3}\d{2,3}"
_LISTA_ARCHIVOS = re.compile(
    r"[Aa]rchivos?\s+(?:[Nn]ormativos?\s+)?"
    rf"({_COD_ARCHIVO}(?:\s*(?:,|y|e)\s*{_COD_ARCHIVO})*)"
)
# Verbo que rige sobre el archivo. Se busca hacia atrás desde la mención porque
# la CMF escribe "Realizar ajustes al archivo normativo C70", con el verbo antes.
_ARCHIVO_CREAR = re.compile(
    r"(?:se\s+crea|cr[ée]ase|se\s+incorpora|nuevo\s+archivo|nuevos?\s+archivos?)",
    re.IGNORECASE,
)
_ARCHIVO_ELIM = re.compile(
    r"(?:se\s+elimina|elim[íi]nese|se\s+deroga|der[óo]gase|se\s+suprime|"
    r"deja\s+sin\s+efecto)",
    re.IGNORECASE,
)
_VENTANA_ACCION_ARCHIVO = 140   # cuánto texto antes de la mención se inspecciona

# Referencia cruzada, no un cambio. El oficio circular 1375/2025 dice "estas
# operaciones se reportan exclusivamente en este archivo, no deben incluirse en
# los archivos D32, D33 y D35": nombra tres archivos para decir dónde *no* hay
# que informar. Sin este filtro entraban como archivos modificados.
_ARCHIVO_NO_CAMBIO = re.compile(
    r"\bno\s+(?:se\s+)?(?:deben?|corresponde|incluir(?:se)?|reportar(?:se)?|"
    r"informar(?:se)?|considerar(?:se)?)",
    re.IGNORECASE,
)

_MESES_ALT = (
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre"
)
# Las tres formas en que la CMF fecha un plazo, en orden de precisión. La de mes
# sin día es la habitual para las obligaciones de reporte —"a partir del mes de
# diciembre de 2024"— y la numérica aparece en las circulares más antiguas
# ("a partir del día 13-07-2021"). Reconocer sólo la forma larga mandaba las dos
# a revisión manual pese a que el documento sí declara cuándo rige.
_FECHA_ALT = (
    r"(\d{1,2}[°º]?\s+de\s+(?:" + _MESES_ALT + r")\s+del?\s+\d{4}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{4}"
    r"|(?:mes\s+de\s+)?(?:" + _MESES_ALT + r")\s+del?\s+\d{4})"
)
_FECHA_NUMERICA = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})")
_MES_ANIO = re.compile(rf"({_MESES_ALT})\s+del?\s+(\d{{4}})", re.IGNORECASE)

# Cláusula de aplicación sin encabezado de sección. Los oficios circulares no
# titulan una sección "Vigencia": declaran cuándo rige el cambio en un párrafo
# de cierre. El oficio 1403/2026 dice "Los ajustes señalados se exigirán a
# contar de los reportes que deben remitirse a partir del 1 de marzo de 2026".
#
# Es un respaldo deliberadamente estrecho, y sólo se usa cuando no hay sección.
# La fecha tiene que colgar de un verbo de aplicación y estar en la misma
# oración (`[^.]`): así no se repite el bug de tomar la primera fecha del
# documento, que es la del encabezado. Ante la duda, no hay fecha.
_CLAUSULA_APLICACION = re.compile(
    r"(?:se\s+exigir[áa]n?|regir[áa]?n?|ser[áa]n?\s+aplicables?|ser[áa]n?\s+requerid\w*|"
    r"aplicar[áa]n?\s+a\s+contar|entrar[áa]n?\s+en\s+(?:vigencia|vigor)|se\s+aplicar[áa]n?|"
    # `regir` sin tilde: "empieza a regir desde enero de 2025". El patrón
    # anterior exigía `regir[áa]`, así que sólo veía "regirá"/"regirán".
    r"(?:comenzar[áa]?n?|empezar[áa]?n?|empieza|comienza)\s+a\s+(?:regir|aplicarse)|"
    r"podr[áa]n?\s+comenzar\s+a\s+(?:enviar|remitir)|primer\s+env[íi]o|"
    r"deber[áa]n?\s+(?:remitirse|enviarse|informarse|reportarse)|"
    # Forma pasiva: el oficio 1381/2025 fija el plazo como "que debe ser
    # reportada hasta el 30 de septiembre de 2025".
    r"deben?\s+ser\s+(?:reportad|informad|enviad|remitid|present)\w*)"
    r"[^.]{0,160}?" + _FECHA_ALT,
    re.IGNORECASE,
)

# Una norma que fija la vigencia de otra: la NCG 564/2026 no tiene contenido
# propio más allá de reemplazar la sección Vigencia de la NCG 550. La fecha que
# importa —cuándo empieza a regir la 550— vive dentro de ese texto citado, no en
# la sección de vigencia de la 564, que dice "rige a contar de esta fecha".
_MOD_VIGENCIA = re.compile(
    r"(?:secci[óo]n|t[íi]tulo|numeral|p[áa]rrafo|texto)\s+[Vv]igencia\s+"
    r"(?:de|del)\s+(?:la\s+)?(?:" + _frase("NORMA DE CARÁCTER GENERAL")
    + r"|NCG)\s+N[°o]\s*(\d+)(.{0,900})",
    re.IGNORECASE | re.DOTALL,
)

# ── Resumen accionable: bloque REF + bullets de cambios ─────────────────────
_REF_BLOCK = re.compile(r"REF\s*:\s*(.+?)(?:\n\s*_{3,}|\n\s*\n)", re.DOTALL | re.IGNORECASE)
# Encabezado de la sección de vigencia. Delimita el cuerpo y, sobre todo, acota
# dónde buscar las fechas de entrada en vigor.
#
# Sigue exigiendo que la palabra ocupe la línea entera —eso es lo que evita
# truncar el texto al toparse con "vigencia" dentro de un párrafo del cuerpo,
# como en "Reemplácese el texto de la sección Vigencia de la NCG N°550..."— pero
# ahora admite el enumerador que la precede y no exige mayúsculas.
#
# El patrón anterior (`\n\s*VIGENCIA\s*\n`) sólo aceptaba la palabra desnuda y
# en mayúsculas, así que perdía las formas que la CMF usa de verdad: "II.
# VIGENCIA", "IV. Vigencia", "m. Vigencia". Sobre 46 documentos de 2025-2026
# reconocía 11 secciones y se le escapaban 17.
_VIGENCIA_HEADING = re.compile(
    r"^[ \t]*(?:[IVXLC]+|[A-Za-z]|\d{1,2})?[.\-)]?[ \t]*VIGENCIA[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Frases con las que la CMF expresa "rige de inmediato". Se comparan en
# minúsculas contra la sección de vigencia.
_FRASES_INMEDIATA = (
    "a partir de esta fecha",
    "a contar de esta fecha",
    "a contar de la fecha",
    "aplicación inmediata",
    "aplicacion inmediata",
    "será inmediata",
    "sera inmediata",
    "vigencia inmediata",
)

# Viñetas dentro de la sección de vigencia. Cuando hay más de una, cada una
# acota un plazo distinto a un subconjunto de capítulos o archivos, y el
# documento no tiene una única fecha de entrada en vigor sino varias.
_MARCA_PLAZO = re.compile(r"(?:^|\n)[ \t]*(?:[•·▪●‐–]|[a-z]\)|\d+\))[ \t]*")

# Bloque de firma que cierra el documento. La sección de vigencia se extiende
# hasta el final del texto, así que sin este corte la firma queda pegada al
# último plazo. Se ancla en el cargo, que es estable, y no en el nombre.
_FIRMA = re.compile(
    r"\n[ \t]*(?:VICE)?PRESIDENT[AE][ \t]*\n|"
    r"\n[ \t]*COMISI[ÓO]N PARA EL MERCADO FINANCIERO[ \t]*\n",
    re.IGNORECASE,
)
# Línea en mayúsculas que precede al cargo: el nombre de quien firma.
_NOMBRE_FIRMA = re.compile(r"\n[ \t]*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .]{5,}\s*$")

# La última viñeta no tiene una viñeta siguiente que la acote, así que se
# extiende sobre el párrafo que sigue. Se corta donde el documento retoma el
# cuerpo: una línea que arranca con mayúscula justo después de una que cerró con
# punto. Las líneas de continuación de una viñeta siempre parten en minúscula,
# porque vienen del salto de línea del PDF y no de una oración nueva.
_FIN_ULTIMO_PLAZO = re.compile(r"\.[ \t]*\n(?=[ \t]*[A-ZÁÉÍÓÚÑ])")
_VERBOS_ACCION = (
    r"(?:Reempl[áa]cese|Agr[ée]guese|Agr[ée]gase|Intercálase|Elim[íi]nese|"
    r"Sust[íi]t[úu]yase|Der[óo]gase|Modif[íi]quese|Cr[ée]ase|Incorp[óo]rase|"
    r"Adic[íi]onase)"
)
_VERBOS_IMPERSONAL = (
    r"[Ss]e\s+(?:reemplaza(?:n)?|modifica|elimina|incorpora|deroga|crea|"
    r"sustituye|adiciona|agrega|intercala|reformula|introduce)"
)
_BULLET_NUM_VERBO = re.compile(
    rf"(?:^|\n)\s*\d+\.\s*({_VERBOS_ACCION}[^\n]*"
    rf"(?:\n(?!\s*(?:\d+|[IVX]+|[a-z])\.\s)[^\n]+){{0,4}})",
    re.MULTILINE | re.IGNORECASE,
)
_BULLET_ROM_IMPERSONAL = re.compile(
    rf"(?:^|\n)\s*[IVX]+\.\s*({_VERBOS_IMPERSONAL}[^\n.]*"
    rf"(?:\n(?!\s*(?:\d+|[IVX]+|[a-z])\.\s)[^\n.]+){{0,2}})",
    re.MULTILINE,
)
_BULLET_LETRA_CAP = re.compile(
    r"(?:^|\n)\s*[a-z]\.\s*(Cap[íi]tulo\s+[\w\-]+\s+(?:de\s+(?:la\s+)?)?"
    r"(?:Recopilaci[óo]n|RAN|Compendio|Manual)[^\n:]*)",
    re.MULTILINE | re.IGNORECASE,
)

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_pdf(pdf_bytes: bytes, url: str = "") -> dict[str, Any]:
    """Parsea un PDF normativo CMF y retorna estructura JSON.

    Intenta pdfplumber primero; si falla, usa PyMuPDF como fallback.
    """
    text = _extract_text_pdfplumber(pdf_bytes)
    if not text:
        text = _extract_text_pymupdf(pdf_bytes)
    if not text:
        logger.warning("No se pudo extraer texto del PDF: %s", url)
        return {"parsed": False, "url": url}

    return _parse_text(text, url)


def _extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        text = "\n".join(pages).strip()
        return text if len(text) > 50 else ""
    except Exception as e:
        logger.debug("pdfplumber falló: %s", e)
        return ""


def _extract_text_pymupdf(pdf_bytes: bytes) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = [doc[i].get_text() for i in range(len(doc))]
        text = "\n".join(pages).strip()
        return text if len(text) > 50 else ""
    except Exception as e:
        logger.debug("PyMuPDF falló: %s", e)
        return ""


def _parse_text(text: str, url: str) -> dict[str, Any]:
    result: dict[str, Any] = {"parsed": True, "url": url}

    # ── Encabezado ──────────────────────────────────────────────────────────
    m = _NCG_NUM.search(text)
    result["ncg"] = int(m.group(1)) if m else None

    m = _RESOLUCION.search(text)
    if m:
        result["resolucion"] = {
            "tipo": "Exenta",
            "numero": int(m.group(1)),
            "fecha": _fecha_str_to_iso(m.group(2)),
        }
    else:
        result["resolucion"] = None

    m = _SESION.search(text)
    if m:
        result["sesion"] = {
            "tipo": m.group(1).capitalize(),
            "numero": int(m.group(2)),
            "fecha": _fecha_str_to_iso(m.group(3)),
        }
    else:
        result["sesion"] = None

    # ── Fecha del documento (encabezado, si no hay resolución) ───────────────
    if not result.get("resolucion"):
        result["fecha_documento"] = _fecha_encabezado(text)

    # ── Modificaciones ───────────────────────────────────────────────────────
    result["modifica"] = _parse_modificaciones(text)

    # ── RAN / MSI ───────────────────────────────────────────────────────────
    result["ran_referencias"] = _parse_ran(text)
    result["msi_referencias"] = _parse_msi(text)

    # ── Vigencia ────────────────────────────────────────────────────────────
    #
    # Sólo se mira la sección de vigencia, nunca el documento completo. Antes se
    # pasaba `text` entero: sin sección de vigencia reconocida, la búsqueda caía
    # sobre la primera fecha del documento, que es la del encabezado. El
    # resultado era una fecha de entrada en vigor plausible y siempre falsa —las
    # 126 entradas con vigencia fechada del histórico repetían, sin excepción, la
    # fecha del propio documento.
    #
    # Sin sección reconocida, `inicio` queda en "no especificado": visiblemente
    # incompleto, que es preferible a un dato inventado (mismo criterio que
    # `_fecha_encabezado`).
    seccion_vig = _seccion_vigencia(text)
    result["vigencia"] = _parse_vigencia_global(seccion_vig)
    result["vigencia"]["fuente"] = "seccion" if seccion_vig else "ninguna"
    plazos = _parse_plazos(seccion_vig) if seccion_vig else []
    if plazos:
        result["vigencia"]["plazos"] = plazos

    # Sin sección, buscar una cláusula de aplicación explícita en el cuerpo.
    # `fuente` queda registrada para que se pueda auditar de dónde salió cada
    # fecha y para distinguir lo extraído de lo que de verdad falta.
    if not seccion_vig:
        clausula = _CLAUSULA_APLICACION.search(text)
        if clausula:
            inicio, precision = _resolver_fecha(clausula.group(1))
            if inicio:
                result["vigencia"] = {
                    "inicio": inicio,
                    "precision": precision,
                    "fuente": "clausula_aplicacion",
                }

    # ── Archivos afectados ──────────────────────────────────────────────────
    # Después de la vigencia, no antes: cada archivo se fecha con la viñeta que
    # lo nombra, así que necesita los plazos ya resueltos.
    result["archivos_afectados"] = _parse_archivos(text, result["vigencia"])

    # Un cambio de archivo sin fecha crea una obligación de reporte sin plazo
    # conocido: se adjuntan las fechas del cuerpo como pistas para la revisión
    # manual que el dashboard va a pedir.
    if result["archivos_afectados"] and result["vigencia"].get("inicio") in (
        "no especificado", "ver texto", None
    ):
        candidatas = _fechas_candidatas(text)
        if candidatas:
            result["vigencia"]["candidatas"] = candidatas

    # ── Resumen accionable (tema + bullets) ─────────────────────────────────
    result["tema"] = _extraer_tema(text)
    result["resumen_acciones"] = _extraer_resumen_acciones(text)

    # ── Identidad del documento ─────────────────────────────────────────────
    result["documento"] = _identidad_documento(text)

    # Validación mínima.
    #
    # Antes bastaba con no encontrar `ncg` ni `modifica[]` para marcar
    # parsed=False, pero una circular u oficio circular no tiene número de NCG
    # por definición: 399 de las 502 entradas degradadas eran documentos
    # perfectamente legibles que simplemente no eran NCG. El dashboard les
    # mostraba "PDF no procesado", que era falso, y store descartaba su
    # vigencia y sus referencias.
    #
    # Ahora parsed=False significa lo que dice: no se pudo identificar el
    # documento ni extraer nada útil de él.
    if (
        result["ncg"] is None
        and not result["modifica"]
        and not result["documento"]
    ):
        result["parsed"] = False

    return result


def _identidad_documento(text: str) -> dict | None:
    """Tipo y número del documento en sí (Circular, Oficio Circular o NCG)."""
    m = _DOC_IDENTIDAD.search(text[:_MAX_BUSQUEDA_SEPARADOR])
    if not m:
        return None
    etiqueta = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    if etiqueta.startswith("norma"):
        tipo = "NCG"
    elif etiqueta.startswith("oficio"):
        tipo = "Oficio Circular"
    else:
        tipo = "Circular"
    return {"tipo": tipo, "numero": int(m.group(2).replace(".", ""))}


def _fecha_encabezado(text: str) -> str | None:
    """Extrae la fecha del encabezado del documento.

    Los documentos CMF abren con el bloque `REF:`, una línea de guiones bajos
    y, justo debajo, la fecha:

        REF: Modifica la Circular N°2.364 para bancos.
        _________________________________
        15 de junio de 2026
        CIRCULAR N°2.371

    El bloque `REF:` mide desde una línea hasta más de diez, así que anclar en
    el separador es más fiable que mirar los primeros N caracteres. Con la
    ventana fija de 500 que había antes, la circular 2.373/2026 perdía su fecha
    por 17 caracteres (estaba en la posición 517) y terminaba archivada con el
    placeholder `2026-01-01`, invisible en el dashboard.

    Cuando hay separador se mira SÓLO debajo de él. La ventana amplia queda
    reservada a los documentos sin separador: ampliarla indiscriminadamente
    hace que se cuele una fecha del cuerpo — en el oficio circular 1394/2025
    capturaba "15 de mayo de 2024", que es una referencia a otra resolución, y
    fechaba el documento un año antes. Ante la duda es preferible devolver None
    y dejar que store caiga al placeholder, que es visiblemente sospechoso.
    """
    m = _SEPARADOR_ENCABEZADO.search(text[:_MAX_BUSQUEDA_SEPARADOR])
    ventana = (
        text[m.end():m.end() + _VENTANA_TRAS_SEPARADOR] if m
        else text[:_VENTANA_ENCABEZADO]
    )
    fechas = _FECHA_SPAN.findall(ventana)
    if not fechas:
        return None
    d, mes, y = fechas[0]
    # Sin default silencioso: hoy MESES cubre exactamente las 12 alternativas de
    # _FECHA_SPAN, pero un `MESES.get(mes, 1)` convertiría cualquier mes futuro
    # que se agregue a la regex y no al dict en un enero plausible — y una fecha
    # creíble pero falsa es peor que no tener fecha.
    num_mes = MESES.get(mes.lower())
    if num_mes is None:
        logger.warning("Mes no reconocido en el encabezado: %r", mes)
        return None
    return f"{y}-{num_mes:02d}-{int(d):02d}"


def _normaliza_frase(s: str, maxlen: int = 180) -> str:
    """Colapsa espacios, recorta puntuación común y trunca con elipsis si excede."""
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(":. ").lstrip("\"“ ")
    if len(s) > maxlen:
        s = s[:maxlen].rsplit(" ", 1)[0] + "…"
    return s


def _extraer_tema(text: str) -> str:
    """Extrae el bloque 'REF: ...' del encabezado del PDF como una sola línea."""
    m = _REF_BLOCK.search(text[:3000])
    if not m:
        return ""
    return _normaliza_frase(m.group(1), maxlen=400)


def _extraer_resumen_acciones(text: str) -> list[str]:
    """Bullets cortos con frases accionables del cuerpo del PDF.

    Aplica tres patrones complementarios: párrafos numerados que arrancan con
    verbo imperativo ('1. Reemplácese...'), incisos romanos con construcción
    impersonal ('I. Se reemplaza...') y letras minúsculas que introducen
    capítulos RAN/CNC/MSI ('a. Capítulo 8-4 de la RAN: ...').
    """
    vp = _VIGENCIA_HEADING.search(text)
    cuerpo = text[: vp.start()] if vp else text

    out: list[str] = []
    for rx in (_BULLET_NUM_VERBO, _BULLET_ROM_IMPERSONAL, _BULLET_LETRA_CAP):
        for m in rx.finditer(cuerpo):
            frag = _normaliza_frase(m.group(1))
            if len(frag) >= 15 and frag not in out:
                out.append(frag)
    return out[:6]


def _parse_modificaciones(text: str) -> list[dict]:
    """Detecta secciones de modificación a normas anteriores."""
    modificaciones = []

    # Vigencias que este documento le fija a otras normas. Pisan a la vigencia
    # de la sección, porque son la fecha de la norma modificada y no la del
    # documento que la modifica: la NCG 564/2026 rige de inmediato, pero lo que
    # hace es que la NCG 550 empiece a regir el 1 de marzo de 2027.
    impuestas = _vigencias_impuestas(text)

    # Dividir por secciones romanas
    secciones_pos = [(m.start(), m.group(1)) for m in _SECCION_ROM.finditer(text)]

    # Encontrar la sección VIGENCIA (encabezado standalone) para delimitar el
    # cuerpo. Se usa _VIGENCIA_HEADING y no un match laxo de la palabra: este
    # último captura "vigencia" en cualquier párrafo del cuerpo y truncaría el
    # texto antes de tiempo, perdiendo modificaciones reales.
    vigencia_pos = _VIGENCIA_HEADING.search(text)
    cuerpo_fin = vigencia_pos.start() if vigencia_pos else len(text)

    if secciones_pos:
        for i, (pos, num_rom) in enumerate(secciones_pos):
            fin = secciones_pos[i + 1][0] if i + 1 < len(secciones_pos) else cuerpo_fin
            segmento = text[pos:fin]

            normas = _NORMA_MOD.findall(segmento)
            if not normas:
                continue

            acciones = list({a.capitalize() for a in _ACCION.findall(segmento)})
            vigencia_sec = _parse_vigencia_seccion(segmento, num_rom, text[cuerpo_fin:])

            for norma_num in normas:
                modificaciones.append({
                    "norma": f"NCG N°{norma_num}",
                    "numero_norma": int(norma_num),
                    "seccion_romana": num_rom,
                    "acciones": acciones,
                    "vigencia": impuestas.get(int(norma_num), vigencia_sec),
                })
    else:
        # Documento sin secciones romanas: modificación directa
        normas = _NORMA_MOD.findall(text[:cuerpo_fin])
        acciones = list({a.capitalize() for a in _ACCION.findall(text[:cuerpo_fin])})
        vigencia_global = _parse_vigencia_global(text[cuerpo_fin:])
        for norma_num in normas:
            modificaciones.append({
                "norma": f"NCG N°{norma_num}",
                "numero_norma": int(norma_num),
                "seccion_romana": None,
                "acciones": acciones,
                "vigencia": impuestas.get(int(norma_num), vigencia_global),
            })

    return modificaciones


def _parse_vigencia_seccion(segmento: str, num_rom: str, seccion_vigencia: str) -> dict:
    """Extrae vigencia para una sección romana específica."""
    # Busca referencias a la sección en el texto de vigencia
    patron_sec = re.compile(
        rf"[Ss]ección\s+{re.escape(num_rom)}[^.]*?([^.]+\.)", re.DOTALL
    )
    m = patron_sec.search(seccion_vigencia)
    if m:
        return _clasificar_vigencia(m.group(1))
    return _parse_vigencia_global(seccion_vigencia)


def _seccion_vigencia(text: str) -> str | None:
    """Texto que sigue al encabezado de vigencia, o None si el documento no tiene.

    Devolver None —y no el documento entero— es lo que impide que
    `_parse_vigencia_global` invente una fecha. Ver el comentario en
    `_parse_text`.
    """
    m = _VIGENCIA_HEADING.search(text)
    if not m:
        return None
    seccion = text[m.end():]
    firma = _FIRMA.search(seccion)
    if firma:
        seccion = seccion[: firma.start()]
        # El cargo va precedido del nombre, también en mayúsculas y en su
        # propia línea; se recorta para que no quede pegado al último plazo.
        seccion = _NOMBRE_FIRMA.sub("", seccion)
    return seccion


def _parse_plazos(seccion_vigencia: str) -> list[dict]:
    """Plazos individuales cuando la vigencia se reparte en varias viñetas.

    La circular 2370/2026 es el caso típico: "La entrada en vigor de la norma
    considera los siguientes plazos:" y luego dos viñetas, una con aplicación
    inmediata y otra que rige desde el 1° de julio de 2026, cada una acotada a
    capítulos distintos. Con un único campo `inicio` esa segunda fecha —la que
    obliga a hacer algo— desaparecía.

    Se exigen al menos dos viñetas: con una sola, el plazo ya queda descrito por
    la vigencia global y desdoblarlo sólo duplica información.
    """
    marcas = list(_MARCA_PLAZO.finditer(seccion_vigencia))
    if len(marcas) < 2:
        return []

    plazos: list[dict] = []
    for i, m in enumerate(marcas):
        ultima = i + 1 == len(marcas)
        fin = len(seccion_vigencia) if ultima else marcas[i + 1].start()
        segmento = seccion_vigencia[m.end():fin]
        if ultima:
            corte = _FIN_ULTIMO_PLAZO.search(segmento)
            if corte:
                segmento = segmento[: corte.start() + 1]
        texto = _normaliza_frase(segmento, maxlen=400)
        if len(texto) < 20:
            continue
        plazos.append({"texto": texto, "inicio": _parse_vigencia_global(texto)["inicio"]})
    return plazos


def _parse_vigencia_global(texto_vigencia: str | None) -> dict:
    """Clasifica el texto de vigencia en un dict estructurado."""
    if not texto_vigencia:
        return {"inicio": "no especificado"}

    texto = texto_vigencia.lower()
    resultado: dict[str, Any] = {}

    if any(f in texto for f in _FRASES_INMEDIATA):
        resultado["inicio"] = "inmediata"
    else:
        fechas = _FECHA_SPAN.findall(texto_vigencia)
        if fechas:
            d, mes, y = fechas[0]
            resultado["inicio"] = f"{y}-{MESES.get(mes.lower(), 1):02d}-{int(d):02d}"
        else:
            # Sin fecha con día, aceptar las formas menos precisas: la CMF
            # también fija plazos por mes ("a contar de diciembre de 2025") o en
            # formato numérico. Antes caían en "ver texto" y el plazo quedaba
            # invisible pese a estar declarado.
            inicio, precision = _resolver_fecha(texto_vigencia)
            if inicio:
                resultado["inicio"] = inicio
                if precision != "dia":
                    resultado["precision"] = precision
            else:
                resultado["inicio"] = "ver texto"

    # Detectar cláusula de transición
    m_trans = re.search(r"a más tardar el\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})", texto_vigencia, re.IGNORECASE)
    if m_trans:
        resultado["plazo_transicion"] = _fecha_str_to_iso(m_trans.group(1))

    # Detectar "cierre del mes siguiente"
    if "cierre del mes siguiente" in texto:
        resultado["inicio"] = "cierre_mes_siguiente"

    return resultado


def _clasificar_vigencia(texto: str) -> dict:
    return _parse_vigencia_global(texto)


def _parse_ran(text: str) -> list[str]:
    """Extrae identificadores de capítulos RAN mencionados."""
    return sorted(set(_RAN_CAP.findall(text)))


def _parse_msi(text: str) -> list[dict]:
    """Extrae menciones al MSI con contexto."""
    resultado = []
    for m in _MSI.finditer(text):
        inicio = max(0, m.start() - 100)
        fin = min(len(text), m.end() + 100)
        resultado.append({"contexto": text[inicio:fin].strip()})
    return resultado


def _accion_archivo(text: str, pos: int) -> str:
    """Verbo que rige sobre el archivo mencionado en `pos`.

    Se mira hacia atrás: la CMF antepone el verbo ("Realizar ajustes al archivo
    normativo C70"). Por defecto "modificar", que es lo que hace la enorme
    mayoría de estos documentos —ajustes técnicos a un archivo que ya existe—.
    """
    ventana = text[max(0, pos - _VENTANA_ACCION_ARCHIVO):pos]
    # Sólo la oración en curso: una negación de la frase anterior no dice nada
    # sobre esta mención.
    clausula = re.split(r"[.;]", ventana)[-1]
    if _ARCHIVO_NO_CAMBIO.search(clausula):
        return "mencion"
    if _ARCHIVO_ELIM.search(ventana):
        return "eliminar"
    if _ARCHIVO_CREAR.search(ventana):
        return "crear"
    return "modificar"


def _vigencia_de_archivo(codigo: str, vigencia: dict) -> str | None:
    """Fecha en que rige el cambio a un archivo concreto.

    Cuando el documento escalona la vigencia, cada viñeta acota su plazo a un
    conjunto de capítulos o archivos y los nombra: la circular 2370/2026 dice
    que los ajustes a R06 y R07 rigen desde el 1 de julio de 2026, mientras
    otros capítulos aplican de inmediato. Si el código aparece en una viñeta,
    manda esa viñeta; si no, la vigencia global del documento.
    """
    for plazo in vigencia.get("plazos") or []:
        if re.search(rf"\b{re.escape(codigo)}\b", plazo.get("texto") or ""):
            return plazo.get("inicio")
    return vigencia.get("inicio")


def _parse_archivos(text: str, vigencia: dict | None = None) -> list[dict]:
    """Archivos normativos del MSI afectados, con su fecha de aplicación."""
    vigencia = vigencia or {}
    por_codigo: dict[str, dict] = {}

    for m in _LISTA_ARCHIVOS.finditer(text):
        accion = _accion_archivo(text, m.start())
        if accion == "mencion":
            continue
        for codigo in re.findall(_COD_ARCHIVO, m.group(1)):
            # Un mismo archivo se menciona muchas veces en el documento. Se
            # conserva la primera aparición, que es la que trae el verbo; las
            # repeticiones posteriores suelen ser referencias dentro del
            # detalle campo por campo.
            if codigo in por_codigo:
                continue
            por_codigo[codigo] = {
                "accion": accion,
                "nombre": codigo,
                "vigencia": _vigencia_de_archivo(codigo, vigencia),
            }

    return list(por_codigo.values())


def _vigencias_impuestas(text: str) -> dict[int, dict]:
    """Vigencias que este documento le fija a *otra* norma.

    Mapea número de NCG -> vigencia. Ver `_MOD_VIGENCIA`: sin esto la fecha se
    perdía o, peor, se le atribuía al documento que la impone en vez de a la
    norma que la recibe.
    """
    impuestas: dict[int, dict] = {}
    for m in _MOD_VIGENCIA.finditer(text):
        numero = int(m.group(1))
        if numero in impuestas:
            continue
        # Acotar el texto citado. Sin esto la ventana se desborda hasta la
        # sección de vigencia del propio documento y la NCG 564/2026 devolvía
        # "inmediata" —su vigencia, no la que le impone a la 550— pisando el
        # 1 de marzo de 2027 que sí estaba en la cita.
        impuestas[numero] = _parse_vigencia_global(_acotar_cita(m.group(2)))
    return impuestas


def _acotar_cita(texto: str) -> str:
    """Recorta el texto que una norma le inserta a otra, entre “ y ”.

    Cuenta la profundidad de comillas en vez de cortar en la primera de cierre:
    la cita de la NCG 564/2026 contiene “CMF Supervisa” anidado, y cortar ahí
    dejaba fuera el plazo del 1 de abril de 2027 que venía después.
    """
    ini = texto.find("“")
    if ini == -1:
        # Sin comillas: al menos no invadir la sección de vigencia propia.
        fin = _VIGENCIA_HEADING.search(texto)
        return texto[: fin.start()] if fin else texto

    profundidad = 0
    for i in range(ini, len(texto)):
        if texto[i] == "“":
            profundidad += 1
        elif texto[i] == "”":
            profundidad -= 1
            if profundidad == 0:
                return texto[ini + 1:i]
    return texto[ini + 1:]


def _fecha_str_to_iso(texto: str) -> str | None:
    """Convierte '10 de abril de 2026' a '2026-04-10'."""
    m = _FECHA_SPAN.search(texto)
    if not m:
        return None
    d, mes, y = m.group(1), m.group(2), m.group(3)
    return f"{y}-{MESES.get(mes.lower(), 1):02d}-{int(d):02d}"


_REFERENCIA_NORMA = re.compile(
    r"(?i)(resoluci[óo]n|circular\s+n|ley\s+n|decreto|sesi[óo]n|acordado|"
    r"\bNCG\b|norma\s+de\s+car[áa]cter)"
)
_INICIO_CUERPO = 900        # el encabezado del PDF cabe holgado en este margen
_MAX_CANDIDATAS = 4


def _recorte_legible(text: str, ini: int, fin: int) -> str:
    """Fragmento de texto que empieza y termina en palabra completa.

    Cortar por offset deja pedazos como "mación referida…" o "…en el c", que en
    un panel de revisión se leen como ruido y obligan a abrir el PDF para
    entender la frase, que es justo lo que la pista intenta evitar.
    """
    ini, fin = max(0, ini), min(len(text), fin)
    fragmento = text[ini:fin]
    if ini > 0 and not text[ini - 1].isspace():
        _, _, fragmento = fragmento.partition(" ")
    if fin < len(text) and not text[fin].isspace():
        fragmento = fragmento.rpartition(" ")[0]
    fragmento = _normaliza_frase(fragmento, maxlen=170)
    sufijo = "…" if fin < len(text) and not fragmento.endswith("…") else ""
    return f"…{fragmento}{sufijo}" if ini > 0 else f"{fragmento}{sufijo}"


def _fechas_candidatas(text: str) -> list[dict]:
    """Fechas del cuerpo que podrían ser la vigencia, para revisión humana.

    Sólo se usan cuando no se pudo determinar la vigencia. No son un dato del
    pipeline —nada aguas abajo las trata como fecha de entrada en vigor— sino
    una pista para quien revise a mano: la CMF entrelaza la fecha con el ciclo
    de reporte ("información referida al cierre de agosto, enviarse en
    septiembre de 2025") y decidir cuál manda es un juicio, no un regex.

    Se descartan las del encabezado y las que cuelgan de una referencia a otra
    norma, que son las dos fuentes de ruido dominantes.
    """
    fuera: list[dict] = []
    vistas: set[str] = set()
    for m in re.finditer(_FECHA_ALT, text[_INICIO_CUERPO:], re.IGNORECASE):
        pos = m.start() + _INICIO_CUERPO
        previo = text[max(0, pos - 110):pos]
        if _REFERENCIA_NORMA.search(previo):
            continue
        iso, precision = _resolver_fecha(m.group(0))
        if not iso or iso in vistas:
            continue
        vistas.add(iso)
        fuera.append({
            "fecha": iso,
            "precision": precision,
            "contexto": _recorte_legible(text, pos - 90, pos + 45),
        })
        if len(fuera) >= _MAX_CANDIDATAS:
            break
    return fuera


def _resolver_fecha(texto: str) -> tuple[str | None, str]:
    """Fecha ISO y su precisión ('dia' o 'mes') desde cualquiera de las formas.

    La precisión se devuelve porque un plazo que el documento fija sólo por mes
    ("a partir del mes de diciembre de 2024") se normaliza al día 1 para poder
    ordenarlo, y esa precisión inventada no se puede mostrar como si fuera una
    fecha exacta. El dashboard la usa para rotularla como mes.
    """
    iso = _fecha_str_to_iso(texto)
    if iso:
        return iso, "dia"

    m = _FECHA_NUMERICA.search(texto)
    if m:
        d, mes, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mes <= 12 and 1 <= d <= 31:
            return f"{y}-{mes:02d}-{d:02d}", "dia"

    m = _MES_ANIO.search(texto)
    if m:
        mes = MESES.get(m.group(1).lower())
        if mes:
            return f"{m.group(2)}-{mes:02d}-01", "mes"

    return None, "dia"
