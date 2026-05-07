import time
import random
import logging
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

CMF_URL = (
    "https://www.cmfchile.cl/institucional/legislacion_normativa/normativa2.php"
    "?tiponorma=ALL&numero=&dd=&mm=&aa=&dd2=&mm2=&aa2=&buscar="
    "&entidad_web=ALL&materia=ALL&enviado=1&hidden_mercado=%25"
)

FRASES_CLAVE = [
    # Originales del brief
    "APRUEBA CONSULTA PÚBLICA DE LA NORMA DE CARÁCTER GENERAL",
    "POSPONER EL PLAZO LÍMITE DE LA CONSULTA PÚBLICA",
    "MODIFICA LA NORMA DE CARÁCTER GENERAL",
    "APRUEBA NUEVA NORMATIVA",
    "EMITE CIRCULAR",
    # Variantes de modificación de NCG sin "LA" o abreviadas
    "MODIFICA NORMA DE CARÁCTER GENERAL",
    "MODIFICA LAS NORMAS DE CARÁCTER GENERAL",
    # Variantes de emisión de normativa nueva
    "APRUEBA EMISIÓN DE NORMATIVA",
    "APRUEBA EMISIÓN DE NORMA",
    "APRUEBA EMISIÓN DE CIRCULAR",
    "APRUEBA EMISIÓN DE LA NORMA",
    "APRUEBA NORMA QUE",
    "APRUEBA NORMA DE CARÁCTER GENERAL",
    "APRUEBA CIRCULAR QUE",
    "APRUEBA LA CIRCULAR QUE",
    # Ajustes técnicos a archivos/capítulos del MSI, RAN, CNC
    "INTRODUCE AJUSTES TÉCNICOS",
    "INTRODUCE AJUSTES AL CAPÍTULO",
    # Modificación de circulares u oficios circulares
    "MODIFICA OFICIO CIRCULAR",
    "MODIFICA LA CIRCULAR",
    "MODIFICA LAS CIRCULARES",
    "MODIFICA ANEXOS DE OFICIO CIRCULAR",
    # Modificación de compendios y secciones específicas
    "MODIFICA EL COMPENDIO DE NORMAS",
    "MODIFICA LAS LETRAS",
    # Instrucciones nuevas con cuerpo normativo
    "INSTRUCCIONES PARA LA PRESTACIÓN",
    "IMPARTE INSTRUCCIONES SOBRE",
    "IMPARTE INSTRUCCIONES RELATIVAS",
    "IMPARTE NORMAS SOBRE",
    # Establecimiento de normas y obligaciones nuevas
    "ESTABLECE NORMAS PARA",
    "ESTABLECE OBLIGACIÓN DE INFORMAR",
    "ESTABLECE REGULACIONES PARA",
    "REGULA FORMA Y CONTENIDO",
    "AUTORIZA ACTIVIDADES COMPLEMENTARIAS",
    # Derogaciones explícitas
    "APRUEBA DEROGACIÓN",
    "DEROGA NORMA DE CARÁCTER",
    "DEROGA NORMA DE CARACTER",
    "DEROGA CIRCULAR N°",
    "DEROGA CIRCULAR Nº",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MonitoreoNormativoCMF/1.0; "
        "+https://github.com/monitoreo-normativo-cmf)"
    )
}


TIMEOUT_LISTADO = 300   # 5 minutos para la página principal
TIMEOUT_PDF     = 120   # 2 minutos por PDF
MAX_REINTENTOS  = 3


def _pause():
    time.sleep(random.uniform(2.0, 3.0))


def _get_con_reintentos(url: str, timeout: int = TIMEOUT_LISTADO) -> requests.Response | None:
    """GET con reintentos y backoff exponencial."""
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            logger.info("Intento %d/%d: %s", intento, MAX_REINTENTOS, url[:80])
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            wait = 30 * intento
            if intento < MAX_REINTENTOS:
                logger.warning("Error (intento %d): %s — reintentando en %ds", intento, e, wait)
                time.sleep(wait)
            else:
                logger.error("Error al acceder a %s: %s", url, e)
    return None


def fetch_listado(from_date: str | None = None) -> list[dict]:
    """Descarga el listado de resoluciones CMF y retorna las relevantes.

    Args:
        from_date: fecha ISO 'YYYY-MM-DD' para filtrar resoluciones desde esa fecha.
    Returns:
        Lista de dicts con keys: fecha, numero, descripcion, url_documento.
    """
    url = _build_url(from_date)
    logger.info("Descargando listado CMF: %s", url)

    response = _get_con_reintentos(url)
    if response is None:
        sys.exit(1)

    resoluciones = _parse_listado(response.text)
    relevantes = _filtrar(resoluciones)
    if from_date:
        relevantes = _filtrar_desde(relevantes, from_date)
    logger.info(
        "Extraídas %d resoluciones, %d relevantes", len(resoluciones), len(relevantes)
    )
    return relevantes


def _build_url(from_date: str | None) -> str:
    if not from_date:
        return CMF_URL
    try:
        d = datetime.strptime(from_date, "%Y-%m-%d")
        # El formulario CMF acepta dd, mm, aa para el rango inicial
        params_extra = f"&dd={d.day:02d}&mm={d.month:02d}&aa={d.year}"
        return CMF_URL + params_extra
    except ValueError:
        logger.warning("Fecha from_date inválida '%s', ignorando filtro", from_date)
        return CMF_URL


def _parse_listado(html: str) -> list[dict]:
    """Parsea el HTML del listado CMF y extrae filas de resoluciones."""
    soup = BeautifulSoup(html, "lxml")
    resoluciones = []

    # La página CMF presenta los resultados en una tabla HTML.
    # Buscamos todas las filas con links a documentos.
    tabla = soup.find("table", {"class": lambda c: c and "tabla" in c.lower()})
    if not tabla:
        # Fallback: buscar cualquier tabla con contenido normativo
        tablas = soup.find_all("table")
        for t in tablas:
            if t.find("a", href=lambda h: h and (".pdf" in h.lower() or "normativa" in h.lower())):
                tabla = t
                break

    if not tabla:
        logger.error("No se encontró tabla de resoluciones en el HTML de CMF")
        return []

    filas = tabla.find_all("tr")
    for fila in filas[1:]:  # saltar encabezado
        celdas = fila.find_all("td")
        if len(celdas) < 3:
            continue

        entrada = _extraer_celda(celdas)
        if entrada:
            resoluciones.append(entrada)

    return resoluciones


def _extraer_celda(celdas: list) -> dict | None:
    """Extrae una resolución desde las celdas de una fila de la tabla CMF.

    El CMF lista cada normativa con la fecha de la NCG ORIGINAL (puede ser de 1986).
    La fecha real de la nueva resolución se extrae del nombre del PDF (ej. ncg_564_2026.pdf).
    """
    try:
        texto_fila = " ".join(c.get_text(strip=True) for c in celdas)

        # Descripción: celda más larga de texto
        descripcion = max(
            (c.get_text(strip=True) for c in celdas), key=len, default=""
        )

        # URL del documento: primer link en la fila
        link = None
        for celda in celdas:
            a = celda.find("a", href=True)
            if a:
                href = a["href"]
                link = urljoin("https://www.cmfchile.cl/institucional/legislacion_normativa/", href)
                break

        if not descripcion or not link:
            return None

        # Fecha y número de la NUEVA normativa desde el nombre del PDF
        # ej. ncg_564_2026.pdf → año=2026, numero=564
        fecha, numero = _fecha_y_numero_desde_url(link)

        return {
            "fecha": fecha,
            "numero": numero or _extraer_numero_de_texto(texto_fila),
            "descripcion": descripcion,
            "url_documento": link,
        }
    except Exception as e:
        logger.debug("Error parseando celda: %s", e)
        return None


def _fecha_y_numero_desde_url(url: str) -> tuple[str | None, str | None]:
    """Extrae año y número desde el nombre del archivo PDF.

    Ejemplos:
      ncg_564_2026.pdf  → ('2026-01-01', '564')
      cir_2370_2026.pdf → ('2026-01-01', '2370')
    """
    import re
    m = re.search(r'[/_](\d+)[/_](\d{4})\.pdf', url, re.IGNORECASE)
    if m:
        numero, year = m.group(1), m.group(2)
        return f"{year}-01-01", numero  # día exacto vendrá del PDF
    # Fallback: solo el año
    m2 = re.search(r'(\d{4})\.pdf', url, re.IGNORECASE)
    if m2:
        return f"{m2.group(1)}-01-01", None
    return None, None


def _filtrar(resoluciones: list[dict]) -> list[dict]:
    """Filtra resoluciones que contienen al menos una frase clave."""
    resultado = []
    for r in resoluciones:
        desc = r.get("descripcion", "").upper()
        if any(frase in desc for frase in FRASES_CLAVE):
            resultado.append(r)
    return resultado


def _filtrar_desde(resoluciones: list[dict], from_date: str) -> list[dict]:
    """Filtra resoluciones cuyo año de PDF es >= from_date."""
    try:
        from_year = int(from_date[:4])
    except (ValueError, TypeError):
        return resoluciones
    resultado = []
    for r in resoluciones:
        fecha = r.get("fecha") or ""
        try:
            year = int(fecha[:4])
            if year >= from_year:
                resultado.append(r)
        except (ValueError, TypeError):
            resultado.append(r)  # incluir si no hay fecha
    return resultado


def _es_fecha(texto: str) -> bool:
    import re
    return bool(re.match(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", texto.strip()))


def _normalizar_fecha(texto: str) -> str:
    """Convierte fecha dd/mm/aaaa o dd-mm-aaaa a formato ISO YYYY-MM-DD."""
    import re
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", texto)
    if not m:
        return texto
    d, mo, y = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _es_numero_resolucion(texto: str) -> bool:
    import re
    return bool(re.match(r"^[\d.]+$", texto.strip()))


def _extraer_numero(texto: str) -> str | None:
    import re
    m = re.search(r"(\d+)", texto)
    return m.group(1) if m else None


def _extraer_numero_de_texto(texto: str) -> str | None:
    import re
    m = re.search(r"N°\s*(\d+)", texto)
    return m.group(1) if m else None


def fetch_pdf(url: str) -> bytes | None:
    """Descarga un PDF desde la URL dada. Respeta el rate limit."""
    _pause()
    logger.info("Descargando PDF: %s", url)
    r = _get_con_reintentos(url, timeout=TIMEOUT_PDF)
    if r is None:
        return None
    if "pdf" not in r.headers.get("Content-Type", "").lower() and not url.lower().endswith(".pdf"):
        logger.warning("El documento no parece ser un PDF: %s", url)
    return r.content
