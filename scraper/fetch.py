import time
import random
import logging
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime

logger = logging.getLogger(__name__)

CMF_URL = (
    "https://www.cmfchile.cl/institucional/legislacion_normativa/normativa2.php"
    "?tiponorma=ALL&numero=&dd=&mm=&aa=&dd2=&mm2=&aa2=&buscar="
    "&entidad_web=ALL&materia=ALL&enviado=1&hidden_mercado=%25"
)

FRASES_CLAVE = [
    "APRUEBA CONSULTA PÚBLICA DE LA NORMA DE CARÁCTER GENERAL",
    "POSPONER EL PLAZO LÍMITE DE LA CONSULTA PÚBLICA",
    "MODIFICA LA NORMA DE CARÁCTER GENERAL",
    "APRUEBA NUEVA NORMATIVA",
    "EMITE CIRCULAR",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MonitoreoNormativoCMF/1.0; "
        "+https://github.com/monitoreo-normativo-cmf)"
    )
}


def _pause():
    time.sleep(random.uniform(2.0, 3.0))


def fetch_listado(from_date: str | None = None) -> list[dict]:
    """Descarga el listado de resoluciones CMF y retorna las relevantes.

    Args:
        from_date: fecha ISO 'YYYY-MM-DD' para filtrar resoluciones desde esa fecha.
    Returns:
        Lista de dicts con keys: fecha, numero, descripcion, url_documento.
    """
    url = _build_url(from_date)
    logger.info("Descargando listado CMF: %s", url)

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Error al acceder a cmfchile.cl: %s", e)
        sys.exit(1)

    resoluciones = _parse_listado(response.text)
    relevantes = _filtrar(resoluciones)
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
    """Extrae una resolución desde las celdas de una fila de la tabla CMF."""
    try:
        # Estructura típica CMF: Fecha | Tipo | Descripción | Entidad | Documento
        # Puede variar — ajustar índices si la estructura cambia
        texto_fila = " ".join(c.get_text(strip=True) for c in celdas)

        # Fecha: primera celda que tenga formato dd/mm/aaaa o similar
        fecha = None
        numero = None
        for celda in celdas:
            texto = celda.get_text(strip=True)
            if _es_fecha(texto):
                fecha = _normalizar_fecha(texto)
            if _es_numero_resolucion(texto):
                numero = _extraer_numero(texto)

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
                if not href.startswith("http"):
                    href = "https://www.cmfchile.cl" + href
                link = href
                break

        if not fecha or not descripcion:
            return None

        return {
            "fecha": fecha,
            "numero": numero or _extraer_numero_de_texto(texto_fila),
            "descripcion": descripcion,
            "url_documento": link,
        }
    except Exception as e:
        logger.debug("Error parseando celda: %s", e)
        return None


def _filtrar(resoluciones: list[dict]) -> list[dict]:
    """Filtra resoluciones que contienen al menos una frase clave."""
    resultado = []
    for r in resoluciones:
        desc = r.get("descripcion", "").upper()
        if any(frase in desc for frase in FRASES_CLAVE):
            resultado.append(r)
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
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        if "pdf" not in r.headers.get("Content-Type", "").lower() and not url.lower().endswith(".pdf"):
            logger.warning("El documento no parece ser un PDF: %s", url)
        return r.content
    except requests.RequestException as e:
        logger.error("Error descargando PDF %s: %s", url, e)
        return None
