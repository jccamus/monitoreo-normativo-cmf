import re
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Patrones regex identificados en documentos CMF reales ──────────────────
_NCG_NUM     = re.compile(r"NORMA DE CARÁCTER GENERAL\s+N[°o]\s*(\d+)", re.IGNORECASE)
_RESOLUCION  = re.compile(
    r"Resolución Exenta\s+N[°o]\s*(\d+)[,.]?\s*de fecha\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    re.IGNORECASE,
)
_SESION      = re.compile(
    r"Sesión\s+(Ordinaria|Extraordinaria)\s+N[°o]\s*(\d+)\s+de\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
    re.IGNORECASE,
)
_NORMA_MOD   = re.compile(
    r"(?:MODIFICACIONES?\s+(?:A\s+LA\s+)?|MODIFICA\s+(?:LA\s+)?)"
    r"NORMA DE CARÁCTER GENERAL\s+N[°o]\s*(\d+)",
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
_DOC_IDENTIDAD = re.compile(
    r"^\s*(OFICIO\s+CIRCULAR|CIRCULAR|NORMA\s+DE\s+CAR[ÁA]CTER\s+GENERAL)"
    r"\s+N[°ºo]\s*([\d.]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# ── Patrones RAN / MSI ──────────────────────────────────────────────────────
_RAN_CAP = re.compile(
    r"[Cc]apítulo\s+([\w][\w.\-]*)\s+(?:de\s+(?:la\s+)?)?(?:"
    r"Recopilación Actualizada de Normas|RAN\b)",
    re.IGNORECASE,
)
_MSI = re.compile(r"Manual de Sistemas de Información", re.IGNORECASE)

# ── Patrones archivos afectados ─────────────────────────────────────────────
_ARCHIVO_CREAR   = re.compile(r"(?:se\s+crea|deberá\s+presentar|nuevo\s+formulario|nuevo\s+archivo)\s+(?:el\s+)?([A-ZÁÉÍÓÚ][\w\s\-\.°N]+)", re.IGNORECASE)
_ARCHIVO_MOD     = re.compile(r"(?:modifica|reemplaza|sustituye)\s+(?:el\s+)?(?:formulario|archivo|anexo)\s+([\w\s\-\.°N]+)", re.IGNORECASE)
_ARCHIVO_ELIM    = re.compile(r"(?:elimina|deroga|suprime)\s+(?:el\s+)?(?:formulario|archivo|anexo)\s+([\w\s\-\.°N]+)", re.IGNORECASE)

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

    # ── RAN / MSI / Archivos ────────────────────────────────────────────────
    result["ran_referencias"] = _parse_ran(text)
    result["msi_referencias"] = _parse_msi(text)
    result["archivos_afectados"] = _parse_archivos(text)

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
    plazos = _parse_plazos(seccion_vig) if seccion_vig else []
    if plazos:
        result["vigencia"]["plazos"] = plazos

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
                    "vigencia": vigencia_sec,
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
                "vigencia": vigencia_global,
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


def _parse_archivos(text: str) -> list[dict]:
    """Detecta archivos/formularios afectados por la norma."""
    archivos = []

    for m in _ARCHIVO_CREAR.finditer(text):
        nombre = m.group(1).strip()[:120]
        if len(nombre) > 5:
            archivos.append({"accion": "crear", "nombre": nombre, "vigencia": None})

    for m in _ARCHIVO_MOD.finditer(text):
        nombre = m.group(1).strip()[:120]
        if len(nombre) > 5:
            archivos.append({"accion": "modificar", "nombre": nombre, "vigencia": None})

    for m in _ARCHIVO_ELIM.finditer(text):
        nombre = m.group(1).strip()[:120]
        if len(nombre) > 5:
            archivos.append({"accion": "eliminar", "nombre": nombre, "vigencia": None})

    return archivos


def _fecha_str_to_iso(texto: str) -> str | None:
    """Convierte '10 de abril de 2026' a '2026-04-10'."""
    m = _FECHA_SPAN.search(texto)
    if not m:
        return None
    d, mes, y = m.group(1), m.group(2), m.group(3)
    return f"{y}-{MESES.get(mes.lower(), 1):02d}-{int(d):02d}"
