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
_FECHA_SPAN  = re.compile(
    r"(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre)\s+de\s+(\d{4})",
    re.IGNORECASE,
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
# VIGENCIA como encabezado standalone (mayúsculas, en su propia línea). Se usa
# como delimitador del cuerpo: exigir que esté sola en su línea evita truncar el
# texto al toparse con la palabra "vigencia" dentro de un párrafo del cuerpo.
_VIGENCIA_HEADING = re.compile(r"\n\s*VIGENCIA\s*\n")
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
        fechas = _FECHA_SPAN.findall(text[:500])
        if fechas:
            d, mes, y = fechas[0]
            result["fecha_documento"] = f"{y}-{MESES.get(mes.lower(), 1):02d}-{int(d):02d}"

    # ── Modificaciones ───────────────────────────────────────────────────────
    result["modifica"] = _parse_modificaciones(text)

    # ── RAN / MSI / Archivos ────────────────────────────────────────────────
    result["ran_referencias"] = _parse_ran(text)
    result["msi_referencias"] = _parse_msi(text)
    result["archivos_afectados"] = _parse_archivos(text)

    # ── Vigencia global ─────────────────────────────────────────────────────
    result["vigencia"] = _parse_vigencia_global(text)

    # ── Resumen accionable (tema + bullets) ─────────────────────────────────
    result["tema"] = _extraer_tema(text)
    result["resumen_acciones"] = _extraer_resumen_acciones(text)

    # Validación mínima
    if result["ncg"] is None and not result["modifica"]:
        result["parsed"] = False

    return result


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


def _parse_vigencia_global(texto_vigencia: str) -> dict:
    """Clasifica el texto de vigencia en un dict estructurado."""
    if not texto_vigencia:
        return {"inicio": "no especificado"}

    texto = texto_vigencia.lower()
    resultado: dict[str, Any] = {}

    if "a partir de esta fecha" in texto or "rige a contar de esta fecha" in texto or "rige a contar de la fecha" in texto:
        resultado["inicio"] = "inmediata"
    elif "a contar de esta fecha" in texto:
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
