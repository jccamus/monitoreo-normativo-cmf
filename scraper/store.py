import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

_NCG_EN_DESC = re.compile(r"N[°o]\s*(\d+)", re.IGNORECASE)

logger = logging.getLogger(__name__)

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"

TIPO_ACUERDO_MAP = {
    "APRUEBA CONSULTA PÚBLICA": "Consulta Pública",
    "POSPONER EL PLAZO LÍMITE DE LA CONSULTA PÚBLICA": "Prórroga Consulta Pública",
    "MODIFICA LA NORMA DE CARÁCTER GENERAL": "Modificación NCG",
    "APRUEBA NUEVA NORMATIVA": "Nueva Normativa",
    "EMITE CIRCULAR": "Circular",
}


def _inferir_tipo_acuerdo(descripcion: str) -> str:
    desc_upper = descripcion.upper()
    for frase, tipo in TIPO_ACUERDO_MAP.items():
        if frase in desc_upper:
            return tipo
    return "Otro"


def ensamblar_entrada(raw: dict, parsed: dict) -> dict:
    """Combina los datos del listado HTML con el parsing del PDF."""
    # Preferir fecha exacta del PDF sobre el placeholder YYYY-01-01 del URL
    fecha_pdf = (parsed.get("resolucion") or {}).get("fecha") or parsed.get("fecha_documento")
    fecha = fecha_pdf or raw.get("fecha")

    entrada = {
        "clave": raw.get("_key", ""),
        "fecha": fecha,
        "resolucion": parsed.get("resolucion") or {
            "tipo": "Exenta",
            "numero": raw.get("numero"),
            "fecha": raw.get("fecha"),
        },
        "sesion": parsed.get("sesion"),
        "ncg": parsed.get("ncg"),
        "tipo_acuerdo": _inferir_tipo_acuerdo(raw.get("descripcion", "")),
        "descripcion_cmf": raw.get("descripcion", ""),
        "url_documento": raw.get("url_documento"),
        "parsed": parsed.get("parsed", False),
    }

    if parsed.get("parsed"):
        modifica = parsed.get("modifica", [])
        # Fallback: extraer norma afectada desde descripcion_cmf cuando el PDF no la detecta
        if not modifica:
            modifica = _modifica_desde_descripcion(raw.get("descripcion", ""))
        entrada["modifica"] = modifica
        entrada["vigencia"] = parsed.get("vigencia", {})
        entrada["ran_referencias"] = parsed.get("ran_referencias", [])
        entrada["msi_referencias"] = parsed.get("msi_referencias", [])
        entrada["archivos_afectados"] = parsed.get("archivos_afectados", [])

    return entrada


def _modifica_desde_descripcion(descripcion: str) -> list[dict]:
    """Extrae normas afectadas desde la descripcion_cmf cuando el PDF no las detectó."""
    desc_upper = descripcion.upper()
    if "MODIFICA" not in desc_upper and "DEROGA" not in desc_upper:
        return []
    numeros = _NCG_EN_DESC.findall(descripcion)
    resultado = []
    for num in numeros:
        accion = "Derógase" if "DEROGA" in desc_upper else "Modifícase"
        resultado.append({
            "norma": f"NCG N°{num}",
            "numero_norma": int(num),
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

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(
        "Diferencial guardado: %s (%d nuevas, %d previas, %d total)",
        path, len(nuevas), len(previas), len(fusionadas),
    )
    return path
