import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).parent.parent / "data" / "state.json"


def _load_raw() -> dict:
    if not STATE_PATH.exists():
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _save_raw({"seen": []})
        return {"seen": []}
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_raw(data: dict) -> None:
    # newline: ver el comentario de `OUTPUT.write_text` en dashboard.py. Acá el
    # archivo lo escriben tanto el runner Linux como una máquina Windows, y sin
    # fijarlo cada uno lo reescribe entero para el otro.
    with open(STATE_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_state() -> set[str]:
    return set(_load_raw().get("seen", []))


def _save_state(seen: set[str]) -> None:
    # Lee y reescribe el archivo completo en vez de emitir `{"seen": ...}` a
    # secas: escrito así, cualquier clave que viva en state.json —hoy
    # `ultima_consulta`— se borraba en silencio en la primera corrida con
    # novedades. El bug sólo aparecía los días en que sí había resoluciones
    # nuevas, que son minoría.
    data = _load_raw()
    data["seen"] = sorted(seen)
    _save_raw(data)


def make_key(fecha: str, numero: str | None) -> str:
    """Genera clave YYYY_NNNN desde fecha ISO y número de resolución."""
    year = fecha[:4] if fecha else "0000"
    num = str(numero).zfill(4) if numero else "0000"
    return f"{year}_{num}"


def get_nuevas(resoluciones: list[dict]) -> list[dict]:
    """Retorna solo las resoluciones no vistas previamente."""
    seen = _load_state()
    nuevas = []
    for r in resoluciones:
        key = make_key(r.get("fecha", ""), r.get("numero"))
        if key not in seen:
            r["_key"] = key
            nuevas.append(r)
    logger.info("%d resoluciones nuevas de %d totales", len(nuevas), len(resoluciones))
    return nuevas


def registrar_consulta() -> str:
    """Sella en state.json el instante en que se obtuvo el listado de la CMF.

    Es el dato que el dashboard rotula «Última actualización», y tiene que
    venir de acá y no de `datetime.now()` al renderizar: `generar_html()` corre
    también sin tocar la CMF —`python scraper/dashboard.py`, el paso redundante
    del workflow, cualquier cambio de diseño— y en esos casos el sello decía
    que los datos eran de ese momento cuando en realidad no se había consultado
    nada. Persistido, sobrevive a todos esos re-render.

    Se llama tras un fetch exitoso y **antes** del corte por «sin novedades»:
    un día sin resoluciones nuevas igual es un día en que se revisó, y la
    pregunta que contesta este dato es «¿esto está al día?», no «¿cambió algo?».
    """
    momento = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = _load_raw()
    data["ultima_consulta"] = momento
    _save_raw(data)
    logger.info("Consulta a la CMF registrada: %s", momento)
    return momento


def ultima_consulta() -> str | None:
    """El sello de `registrar_consulta`, o None si todavía no se ha escrito."""
    return _load_raw().get("ultima_consulta")


def commit_nuevas(nuevas: list[dict]) -> None:
    """Agrega las claves de las nuevas resoluciones a state.json."""
    seen = _load_state()
    for r in nuevas:
        seen.add(r["_key"])
    _save_state(seen)
    logger.info("state.json actualizado con %d nuevas claves", len(nuevas))
