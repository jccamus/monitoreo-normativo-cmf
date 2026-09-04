import re
import io
import logging
from calendar import monthrange
from datetime import date, timedelta
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


def _frase_flex(texto: str) -> str:
    """Como `_frase()`, pero además tolera que falte la tilde.

    El extractor devuelve "aplicación" en un PDF y "aplicacion" en otro según
    cómo esté incrustada la fuente. La lista de frases que esta función
    reemplaza cargaba las dos formas escritas a mano y aun así se le escapaban
    combinaciones; generarlas es más corto y no se olvida ninguna.
    """
    patron = _frase(texto)
    for con, sin in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        patron = patron.replace(con, f"[{con}{sin}]")
    return patron


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
# La preposición antes del año es opcional: la CMF escribe tanto "1° de julio
# de 2023" como "1° de julio 2023", y exigir el "de" mandaba la segunda forma a
# "ver texto" pese a ser una fecha completa y explícita. Eran 3 entradas del
# histórico (NCG 496/2023, NCG 506/2024, circular 2357/2024), todas con su
# vigencia declarada sin ambigüedad en la sección correspondiente.
#
# `primero` es el único día que la CMF llega a escribir con palabras, y sólo
# porque el día 1 es donde caen casi todos los plazos: la circular 2317/2022
# fija su vigencia en "el primero de julio de 2023". Sin él la fecha caía a la
# rama de mes suelto y se guardaba con `precision: "mes"`, o sea rotulada
# "julio de 2023" cuando el documento dice el día. Los demás ordinales no se
# agregan: sobre los 191 documentos del corpus no aparece ni uno, y cada
# alternativa que no responde a un caso real es una forma más de calzar de
# casualidad.
_FECHA_SPAN  = re.compile(
    r"(\d{1,2}[°º]?|primero)\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre)\s+(?:del?\s+)?(\d{4})",
    re.IGNORECASE,
)


def _dia_a_int(texto: str) -> int:
    """Número de día desde lo que captura `_FECHA_SPAN`: "1", "1°" o "primero"."""
    texto = texto.strip().rstrip("°º")
    return 1 if texto.lower() == "primero" else int(texto)


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
# El `del?` opcional sólo en la rama de día completo ("1° de julio 2023"): ahí
# el día vuelve inequívoca a la fecha. En la rama de mes suelto se mantiene
# obligatorio, porque sin día ni preposición cualquier "diciembre 2024" del
# cuerpo pasaría por plazo.
_FECHA_ALT = (
    r"((?:\d{1,2}[°º]?|primero)\s+de\s+(?:" + _MESES_ALT + r")\s+(?:del?\s+)?\d{4}"
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
# **El presente ("entra en vigor") queda deliberadamente fuera**, aunque parezca
# la omisión obvia. Se probó: capturaba 1 cláusula legítima y 2 falsas. La NCG
# 568/2026 sustituye el apartado Vigencia de *otra* norma y cita el texto nuevo
# —«La presente norma entra en vigor a partir del 1 de agosto de 2025»—, que
# habría fechado la 568 quince meses antes de su propia publicación; y la
# circular 2314/2022 dice que el ILAAP "entra en vigencia en su versión
# simplificada en abril de 2023", donde el sujeto es un proceso y no la
# circular. El futuro no tiene ese problema porque un documento que cita la
# vigencia de otro la transcribe tal cual, y ahí el presente es lo habitual.
#
# Es un respaldo deliberadamente estrecho, y sólo se usa cuando no hay sección.
# La fecha tiene que colgar de un verbo de aplicación y estar en la misma
# oración (`[^.]`): así no se repite el bug de tomar la primera fecha del
# documento, que es la del encabezado. Ante la duda, no hay fecha.
_CLAUSULA_APLICACION = re.compile(
    r"(?:se\s+exigir[áa]n?|regir[áa]?n?|rige[n]?|ser[áa]n?\s+aplicables?|ser[áa]n?\s+requerid\w*|"
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
# El sufijo "y aplicación" es la otra forma en que la CMF titula la sección: la
# NCG 573/2026 abre "VI. Vigencia y aplicación" y la circular 2360/2024 usa
# "Vigencia y aplicación" a secas. Se enumera la variante en vez de admitir
# cualquier cola (`.*`) por lo mismo que el patrón exige la línea completa: una
# cola libre haría calzar "durante la vigencia de la póliza" y cortaría ahí el
# cuerpo del documento.
_VIGENCIA_HEADING = re.compile(
    r"^[ \t]*(?:[IVXLC]+|[A-Za-z]|\d{1,2})?[.\-)]?[ \t]*"
    r"VIGENCIA(?:[ \t]+Y[ \t]+APLICACI[ÓO]N)?[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Algunos documentos no titulan "Vigencia" y ponen la fecha bajo "Disposiciones
# transitorias" (la circular 2373/2026). Se usa **sólo como respaldo**, porque
# hay documentos con las dos secciones —la NCG 562/2026 tiene "IV. DISPOSICIONES
# TRANSITORIAS" y "V. VIGENCIA"— y ahí manda la de vigencia.
# Singular además de plural: las circulares que agregan un solo artículo
# titulan "Disposición transitoria" (2366/2025, 2371/2026) y el plural sólo
# aparece en las normas largas. Y se admite la comilla de apertura, porque
# cuando la circular *inserta* la disposición en otro cuerpo normativo el
# encabezado queda dentro del texto citado.
_TRANSITORIAS_HEADING = re.compile(
    r"^[ \t]*[\"“«]?[ \t]*(?:[IVXLC]+|[A-Za-z]|\d{1,2})?[.\-)]?[ \t]*"
    r"DISPOSICI(?:[ÓO]N|ONES)[ \t]+TRANSITORIAS?[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Excepción a la regla general de vigencia, redactada en prosa: "…entrarán en
# vigencia a contar del 1 de enero de 2028, con excepción de los ajustes
# estipulados en el Capítulo 1-13 de la RAN, los cuales tienen vigencia
# inmediata". Sin desdoblarla, el documento queda fechado sólo por la regla
# general y el grupo que rige de inmediato desaparece.
# "no obstante" introduce la misma estructura que "salvo": regla general y
# después el grupo que se aparta de ella. La NCG 520/2024 la usa sin punto de
# por medio —"rige a contar de 01 de junio de 2025, no obstante, las
# modificaciones establecidas en los N°s 1 y 7 rigen a contar de la fecha de
# emisión"—, así que `_parse_tramos_prosa` no la puede partir y sin este
# conector el documento quedaba descrito sólo por su excepción: la sección
# entera daba "inmediata" y el 1 de junio de 2025, que es la regla general,
# desaparecía.
_EXCEPCION = re.compile(
    r"[,;]?\s*(?:con\s+excepci[óo]n\s+de|excepto|salvo|no\s+obstante)[,;]?\s+",
    re.IGNORECASE,
)
# Punto que cierra oración. El lookahead de espacio evita partir los números con
# separador de miles ("N°2.373").
_FIN_ORACION = re.compile(r"\.(?=\s|$)")

# Valores de `inicio` que significan "no se pudo determinar". Se nombran una
# sola vez porque hay varios puntos que deben distinguir un dato resuelto de uno
# que no lo está, y tratarlos como equivalentes a una fecha es justamente el
# error que produce vigencias falsas.
_INICIO_DEGRADADO = ("ver texto", "no especificado", None)

# Tope de tramos que se aceptan al desdoblar una sección de vigencia escrita en
# prosa. Ver `_parse_tramos_prosa`.
_MAX_TRAMOS_PROSA = 4

# "a contar del 12 de julio del presente año": día y mes declarados, año dicho
# por referencia al propio documento. Es la forma de la NCG 472/2022. El año
# sale de la fecha de publicación, que es lo que "el presente año" significa.
_FECHA_PRESENTE_ANIO = re.compile(
    r"(\d{1,2})[°º]?\s+de\s+(" + _MESES_ALT + r")\s+"
    r"del?\s+(?:presente|actual)\s+a[ñn]o",
    re.IGNORECASE,
)

# ── Plazos relativos: el documento declara una regla, no una fecha ─────────
#
# La circular 2376/2026 dice "entrará en vigor en el plazo de un mes contado
# desde su publicación". Es una vigencia perfectamente declarada, pero como no
# hay ninguna fecha escrita en el texto, el parser la mandaba a "ver texto" y
# el documento desaparecía del Calendario de modificaciones, que es justamente
# donde un plazo futuro tiene que estar.
#
# Calcular esa fecha NO contradice la regla de no inventar vigencias. La regla
# prohíbe suponer una fecha que el documento no da; acá el documento da la
# regla completa —cuánto y desde cuándo— y la base del cálculo es su propia
# fecha de publicación, que el parser ya extrajo del encabezado. Es derivación,
# no suposición. Aun así el resultado se marca con `calculo` (ver
# `_parse_vigencia_global`) para que nunca se confunda con una fecha escrita.
#
# Dos condiciones que no se pueden relajar:
#
#  1. **Anclaje a un verbo de entrada en vigor.** El plazo tiene que colgar de
#     "entrará en vigor", "rige", "comenzarán a regir"… dentro de la misma
#     oración (`[^.]`). Sin esto, un "las entidades tendrán seis meses para
#     adecuarse" —que es un plazo de adecuación, no la vigencia— pasaría por
#     fecha de entrada en vigor.
#  2. **Los días hábiles quedan fuera.** Contarlos exige el calendario de
#     feriados de Chile, que este proyecto no tiene y no va a adivinar. Ante un
#     plazo en días hábiles el resultado correcto sigue siendo "ver texto".
# `entrar?[áa]?n?` cubre de una vez el presente y el futuro —"entra en vigor",
# "entran en vigor", "entrará en vigencia", "entrarán a regir"—, que es como la
# CMF alterna sin criterio dentro del mismo documento. Escribirlas como formas
# separadas ya dejó fuera dos veces la conjugación en presente.
_VERBO_VIGENCIA = (
    r"(?:entrar?[áa]?n?\s+(?:en\s+(?:plena\s+)?(?:vigor|vigencia)|a\s+regir)|"
    r"rige[nr]?|regir[áa]?n?|"
    r"comenzar[áa]?n?\s+a\s+regir|empezar[áa]?n?\s+a\s+regir|"
    r"ser[áa]n?\s+exigibles?|se\s+aplicar[áa]n?|aplicaci[óo]n\s+de\s+las?)"
)

# Cantidades escritas con palabras. La CMF alterna "treinta días" con "120
# días" en documentos del mismo año, así que hay que leer las dos formas.
_CARDINALES = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "quince": 15, "veinte": 20, "treinta": 30, "sesenta": 60,
    "noventa": 90, "ciento veinte": 120, "ciento ochenta": 180,
}
_ORDINALES = {
    "primer": 1, "primero": 1, "segundo": 2, "tercer": 3, "tercero": 3,
    "cuarto": 4, "quinto": 5, "sexto": 6, "séptimo": 7, "septimo": 7,
    "octavo": 8, "noveno": 9, "décimo": 10, "decimo": 10,
    "undécimo": 11, "undecimo": 11, "duodécimo": 12, "duodecimo": 12,
}

# Las alternativas se ordenan de más larga a más corta: sin eso "ciento veinte"
# nunca calzaría, porque la rama "veinte" consume primero.
_CANTIDAD_ALT = r"\d{1,3}|" + "|".join(
    _frase_flex(p) for p in sorted(_CARDINALES, key=len, reverse=True)
)
_ORDINAL_ALT = "|".join(
    _frase_flex(p) for p in sorted(_ORDINALES, key=len, reverse=True)
)
# La base del conteo. "su publicación", "su emisión" y "su dictación" son la
# misma fecha para estos efectos: la del propio documento.
_BASE_PLAZO = (
    r"(?:su|la|el|de)\s+(?:fecha\s+de\s+)?"
    r"(?:publicaci[óo]n|emisi[óo]n|dictaci[óo]n)"
)

# Forma 1: "en el plazo de un mes contado desde su publicación",
#          "en treinta días, contados desde la emisión de la presente norma",
#          "120 días después de su emisión".
_PLAZO_RELATIVO = re.compile(
    _VERBO_VIGENCIA + r"[^.]{0,60}?"
    r"(?:en\s+(?:el\s+|un\s+)?(?:plazo\s+de\s+)?|a\s+los\s+|dentro\s+de\s+(?:los\s+)?)?"
    r"(" + _CANTIDAD_ALT + r")\s+"
    r"(d[íi]as?|meses|mes|años?)"
    r"(?:\s*,?\s*(h[áa]biles?|corridos?|calendario))?"
    r"[^.]{0,40}?"
    r"(?:contad[oa]s?\s+)?(?:desde|despu[ée]s\s+de|a\s+contar\s+de|a\s+partir\s+de)\s+"
    + _BASE_PLAZO,
    re.IGNORECASE,
)

# Forma 2: "a contar del primer día del sexto mes siguiente a su emisión",
#          "a contar del primer lunes del tercer mes siguiente al de su emisión".
# Se resuelve al mes calendario N posiciones después del de publicación, y
# dentro de ese mes al primer día o al primer lunes según lo que pida el texto.
_PLAZO_MES_SIGUIENTE = re.compile(
    _VERBO_VIGENCIA + r"[^.]{0,60}?"
    r"(?:primer|primero)\s+(d[íi]a|lunes|martes|mi[ée]rcoles|jueves|viernes)\s+"
    r"del?\s+(" + _ORDINAL_ALT + r")\s+mes\s+siguiente"
    r"[^.]{0,40}?" + _BASE_PLAZO,
    re.IGNORECASE,
)

_DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4,
}


def _suma_meses(base: date, meses: int) -> date:
    """Suma meses calendario recortando al último día válido del mes destino.

    Un 31 de enero más un mes es el 28 (o 29) de febrero, no un 31 inexistente.
    """
    indice = base.year * 12 + (base.month - 1) + meses
    anio, mes = divmod(indice, 12)
    ultimo = monthrange(anio, mes + 1)[1]
    return date(anio, mes + 1, min(base.day, ultimo))


def _cantidad(texto: str) -> int | None:
    """Número desde su forma en dígitos o en palabras."""
    texto = " ".join(texto.lower().split())
    if texto.isdigit():
        return int(texto)
    return _CARDINALES.get(texto)


# Comillas tipográficas, que son las que la CMF usa para citar el texto que
# inserta en otro cuerpo normativo. La comilla recta queda fuera a propósito:
# sirve de apertura y de cierre, así que no permite saber de qué lado se está.
_CITA_ABRE = "«“"
_CITA_CIERRA = "»”"


def _dentro_de_cita(texto: str, pos: int) -> bool:
    """Si `pos` cae dentro de un fragmento citado del documento."""
    abiertas = 0
    for c in texto[:pos]:
        if c in _CITA_ABRE:
            abiertas += 1
        elif c in _CITA_CIERRA:
            abiertas = max(0, abiertas - 1)
    return abiertas > 0


# Sujeto autorreferente de una cláusula de vigencia: "la presente norma rige a
# contar del...". Dentro de una cita **ese "presente" no es este documento**,
# sino la norma que recibe el texto insertado.
_AUTORREFERENCIA = re.compile(
    r"(?:" + _frase_flex("la presente norma") + r"|" + _frase_flex("esta norma")
    + r"|" + _frase_flex("la presente circular") + r"|" + _frase_flex("esta circular")
    + r"|" + _frase_flex("las presentes instrucciones")
    + r"|" + _frase_flex("el presente oficio") + r")",
    re.IGNORECASE,
)


def _clausula_aplicacion(text: str) -> re.Match | None:
    """La primera cláusula de aplicación que hable de la vigencia *propia*.

    Descarta las que están dentro de una cita **y** tienen sujeto
    autorreferente, que es la combinación en que la fecha pertenece a otra
    norma. La NCG 448/2020 reemplaza el numeral "II. Vigencia" de la NCG 445 y
    transcribe el texto nuevo —"II. Vigencia. La presente norma rige a contar
    del 1° de enero de 2021"—: ese "la presente norma" es la 445, y sin el
    filtro la 448 se quedaba con una fecha que no es la suya, que es
    exactamente lo que `_vigencias_impuestas` existe para evitar.

    **La cita por sí sola no basta para descartar, y ahí está el matiz.** Un
    documento también usa comillas para *insertar* una disposición transitoria
    propia, y entonces la fecha citada sí es la obligación que él crea: la
    circular 2317/2022 no declara vigencia en ninguna otra parte y su único
    plazo vive dentro de la cita —"Las instrucciones contenidas en el numeral 6
    ... regirán desde el primero de julio de 2023"—. Rechazar toda cita la
    dejaba sin fecha y la mandaba a revisión manual. Lo que distingue los dos
    casos es el sujeto: el que dice "la presente norma" habla de la norma que
    recibe el texto; el que nombra qué es lo que rige, habla de este documento.
    """
    for m in _CLAUSULA_APLICACION.finditer(text):
        if not _dentro_de_cita(text, m.start()):
            return m
        # Ventana hacia atrás acotada a la oración: un "la presente norma" de
        # dos párrafos antes no dice nada sobre el sujeto de *esta* cláusula.
        ini = max(0, m.start() - 160)
        antes = text[ini:m.start()]
        corte = antes.rfind(".")
        if not _AUTORREFERENCIA.search(antes[corte + 1:] if corte != -1 else antes):
            return m
    return None


def _resolver_plazo_relativo(texto: str, fecha_base: str | None) -> dict | None:
    """Fecha de entrada en vigor declarada como plazo contado desde el documento.

    Devuelve `None` —y el llamador cae en "ver texto"— cuando el plazo existe
    pero no se puede resolver con honestidad: en días hábiles, o sin fecha base.

    La fecha base tiene que venir del PDF, nunca del listado de la CMF: el
    listado trae la fecha de publicación *original* de la norma y el pipeline la
    rellena como placeholder `YYYY-01-01`. Contar un mes desde un placeholder
    daría una fecha con toda la apariencia de un dato y ningún respaldo, que es
    exactamente el modo de falla que este proyecto ya conoce.
    """
    if not texto:
        return None

    def base_valida() -> date | None:
        if not fecha_base:
            return None
        try:
            return date.fromisoformat(fecha_base)
        except ValueError:
            return None

    m = _PLAZO_RELATIVO.search(texto)
    if m:
        calificador = (m.group(3) or "").lower()
        if calificador.startswith(("hábil", "habil")):
            return None
        cantidad = _cantidad(m.group(1))
        base = base_valida()
        if not cantidad or not base:
            return None
        unidad = m.group(2).lower()
        if unidad.startswith("d"):
            destino = base + timedelta(days=cantidad)
            expresion = f"{cantidad} días"
        elif unidad.startswith("me"):
            destino = _suma_meses(base, cantidad)
            expresion = f"{cantidad} mes" + ("es" if cantidad > 1 else "")
        else:
            destino = _suma_meses(base, cantidad * 12)
            expresion = f"{cantidad} año" + ("s" if cantidad > 1 else "")
        return _vigencia_calculada(destino, fecha_base, f"{expresion} desde la publicación", m.group(0))

    m = _PLAZO_MES_SIGUIENTE.search(texto)
    if m:
        base = base_valida()
        meses = _ORDINALES.get(" ".join(m.group(2).lower().split()))
        if not base or not meses:
            return None
        dia_pedido = m.group(1).lower()
        destino = _suma_meses(base.replace(day=1), meses)
        ordinal = m.group(2).lower()
        if dia_pedido.startswith("d"):
            expresion = f"primer día del {ordinal} mes siguiente"
        else:
            objetivo = _DIAS_SEMANA.get(dia_pedido)
            if objetivo is None:
                return None
            destino += timedelta(days=(objetivo - destino.weekday()) % 7)
            expresion = f"primer {dia_pedido} del {ordinal} mes siguiente"
        return _vigencia_calculada(destino, fecha_base, expresion, m.group(0))

    return None


def _vigencia_calculada(destino: date, fecha_base: str, expresion: str, texto: str) -> dict:
    """Vigencia derivada de una regla, con el rastro de cómo se obtuvo.

    `calculo` no es decorativo: es lo que permite que el dashboard la muestre
    como fecha calculada y no como fecha declarada, y lo que deja auditar el
    resultado sin volver al PDF.
    """
    return {
        "inicio": destino.isoformat(),
        "precision": "dia",
        "calculo": {
            "base": "publicacion",
            "fecha_base": fecha_base,
            "expresion": expresion,
            "texto": _normaliza_frase(texto, maxlen=200),
        },
    }


# Vigencia inmediata: el documento rige desde su propia publicación.
#
# Esto era una tupla de frases comparadas con `in` sobre el texto en minúsculas,
# y por eso **no reconocía casi ningún caso real**: los PDF de la CMF cortan la
# frase donde termina el renglón, y la sección de vigencia es justo donde más
# pasa. El histórico trae "regirán a contar" / "de esta fecha" y "rigen a" /
# "partir de esta fecha" partidas en dos renglones, y un espacio literal no
# cruza ese corte. La lección ya estaba aprendida y documentada en `_frase()`,
# pero nunca había llegado hasta acá: de las 22 entradas que el histórico tenía
# en "ver texto", 11 eran vigencias inmediatas perfectamente declaradas que se
# perdían por un salto de línea.
#
# Se agregan además las variantes que la CMF usa de verdad y que la lista no
# contemplaba: "de la presente fecha" y las que nombran la publicación en vez
# de "esta fecha".
#
# Ojo con el alcance de las formas con "publicación": van ancladas a "a contar
# de" / "a partir de", nunca como un "desde su publicación" suelto. Ese giro
# también aparece dentro de los plazos relativos ("en el plazo de un mes
# contado desde su publicación"), y capturarlo acá convertiría un plazo futuro
# en vigencia inmediata: el mismo error de fechar un documento con su propia
# fecha que este proyecto ya pagó caro.
_INMEDIATA = re.compile(
    "|".join(_frase_flex(f) for f in (
        "a partir de esta fecha",
        "a contar de esta fecha",
        "a contar de la fecha",
        "a contar de la presente fecha",
        "a partir de la presente fecha",
        "a contar de su publicación",
        "a partir de su publicación",
        "a contar de su fecha de publicación",
        "a partir de su fecha de publicación",
        "aplicación inmediata",
        "vigencia inmediata",
        "será inmediata",
    )),
    re.IGNORECASE,
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

    # La fecha de publicación del documento, que es la base desde la cual se
    # cuentan los plazos relativos ("en el plazo de un mes contado desde su
    # publicación"). Se calcula acá, una sola vez y antes de la vigencia, con el
    # mismo criterio que usa `store.ensamblar_entrada` para elegir la fecha de
    # la entrada: primero la de la resolución, si el PDF declara una.
    #
    # Este dato ya existía en el parser desde siempre, pero no llegaba a las
    # funciones de vigencia, que eran las que lo necesitaban para resolver un
    # plazo relativo. Ese hueco entre dos partes del mismo módulo es la razón de
    # que la circular 2376/2026 no tuviera fecha en el Calendario pese a
    # declararla sin ambigüedad.
    fecha_base = (result.get("resolucion") or {}).get("fecha") or result.get("fecha_documento")

    # ── Modificaciones ───────────────────────────────────────────────────────
    result["modifica"] = _parse_modificaciones(text, fecha_base)

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
    result["vigencia"] = _parse_vigencia_global(seccion_vig, fecha_base)
    result["vigencia"]["fuente"] = "seccion" if seccion_vig else "ninguna"

    # Las tres formas de escalonar una vigencia, de la más explícita a la más
    # difusa: viñetas, una excepción introducida por "salvo", y varias oraciones
    # seguidas que reparten la entrada en vigor entre grupos de secciones. Se
    # prueban en ese orden y gana la primera que reconozca tramos.
    plazos = []
    if seccion_vig:
        for extraer in (_parse_plazos, _parse_excepciones, _parse_tramos_prosa):
            plazos = extraer(seccion_vig, fecha_base)
            if plazos:
                break

    if plazos:
        result["vigencia"]["plazos"] = plazos
        # El `inicio` global lo da el primer tramo —la regla general—, no el
        # barrido de toda la sección: `_parse_vigencia_global` evalúa las frases
        # de inmediatez antes que las fechas, así que sin esto una excepción
        # "…tienen vigencia inmediata" se imponía sobre el 1 de enero de 2028 de
        # la regla, y un documento cuya primera oración dice "rige a contar de
        # esta fecha" quedaba descrito sólo como inmediato aunque su segunda
        # oración fijara una fecha futura.
        #
        # Y sólo si ese tramo aporta algo: uno sin fecha no puede degradar una
        # vigencia global que sí se había resuelto.
        if plazos[0]["inicio"] not in _INICIO_DEGRADADO:
            result["vigencia"]["inicio"] = plazos[0]["inicio"]
            # El rastro del cálculo describe una fecha concreta. Si el `inicio`
            # pasa a ser el del primer tramo, hay que reemplazarlo por el de ese
            # tramo —o borrarlo—: un `calculo` que explica una fecha distinta de
            # la que acompaña es peor que no tener ninguno, porque el dashboard
            # lo muestra como la justificación del dato.
            result["vigencia"].pop("calculo", None)
            if plazos[0].get("calculo"):
                result["vigencia"]["calculo"] = plazos[0]["calculo"]

    # Sin sección, buscar una cláusula de aplicación explícita en el cuerpo.
    # `fuente` queda registrada para que se pueda auditar de dónde salió cada
    # fecha y para distinguir lo extraído de lo que de verdad falta.
    if not seccion_vig:
        clausula = _clausula_aplicacion(text)
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
    return f"{y}-{num_mes:02d}-{_dia_a_int(d):02d}"


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


def _parse_modificaciones(text: str, fecha_base: str | None = None) -> list[dict]:
    """Detecta secciones de modificación a normas anteriores."""
    modificaciones = []

    # Vigencias que este documento le fija a otras normas. Pisan a la vigencia
    # de la sección, porque son la fecha de la norma modificada y no la del
    # documento que la modifica: la NCG 564/2026 rige de inmediato, pero lo que
    # hace es que la NCG 550 empiece a regir el 1 de marzo de 2027.
    impuestas = _vigencias_impuestas(text, fecha_base)

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

            acciones = _acciones_unicas(_ACCION.findall(segmento))
            vigencia_sec = _parse_vigencia_seccion(segmento, num_rom, text[cuerpo_fin:],
                                                   fecha_base)

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
        acciones = _acciones_unicas(_ACCION.findall(text[:cuerpo_fin]))
        vigencia_global = _parse_vigencia_global(text[cuerpo_fin:], fecha_base)
        for norma_num in normas:
            modificaciones.append({
                "norma": f"NCG N°{norma_num}",
                "numero_norma": int(norma_num),
                "seccion_romana": None,
                "acciones": acciones,
                "vigencia": impuestas.get(int(norma_num), vigencia_global),
            })

    return modificaciones


def _parse_vigencia_seccion(segmento: str, num_rom: str, seccion_vigencia: str,
                            fecha_base: str | None = None) -> dict:
    """Extrae vigencia para una sección romana específica."""
    # Busca referencias a la sección en el texto de vigencia
    patron_sec = re.compile(
        rf"[Ss]ección\s+{re.escape(num_rom)}[^.]*?([^.]+\.)", re.DOTALL
    )
    m = patron_sec.search(seccion_vigencia)
    if m:
        return _parse_vigencia_global(m.group(1), fecha_base)
    return _parse_vigencia_global(seccion_vigencia, fecha_base)


def _seccion_vigencia(text: str) -> str | None:
    """Texto que sigue al encabezado de vigencia, o None si el documento no tiene.

    Devolver None —y no el documento entero— es lo que impide que
    `_parse_vigencia_global` invente una fecha. Ver el comentario en
    `_parse_text`.
    """
    m = _VIGENCIA_HEADING.search(text) or _TRANSITORIAS_HEADING.search(text)
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


def _parse_plazos(seccion_vigencia: str, fecha_base: str | None = None) -> list[dict]:
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
        plazos.append(_plazo(texto, fecha_base))
    return plazos


def _plazo(texto: str, fecha_base: str | None = None) -> dict:
    """Un tramo de vigencia con su texto, su fecha y la precisión de esa fecha.

    `precision` viaja con el plazo por lo mismo que con la vigencia global: un
    tramo fijado sólo por mes ("a contar de diciembre de 2025") se normaliza al
    día 1 y mostrarlo como "2025-12-01" afirmaría una exactitud que el documento
    no da.
    """
    v = _parse_vigencia_global(texto, fecha_base)
    plazo = {"texto": texto, "inicio": v["inicio"]}
    if v.get("precision"):
        plazo["precision"] = v["precision"]
    # Una viñeta también puede fijar su plazo en forma relativa, y el rastro del
    # cálculo tiene que viajar con ella: es lo que distingue en el dashboard una
    # fecha derivada de una escrita en el documento.
    if v.get("calculo"):
        plazo["calculo"] = v["calculo"]
    return plazo


def _parse_excepciones(seccion_vigencia: str, fecha_base: str | None = None) -> list[dict]:
    """Regla general y su excepción, cuando la vigencia se redacta en prosa.

    La circular 2373/2026 fija el 1 de enero de 2028 "con excepción de los
    ajustes estipulados en el Capítulo 1-13 de la RAN, los cuales tienen
    vigencia inmediata": dos grupos con fechas distintas en una sola oración.
    Sin esto la entrada queda fechada sólo por la regla general y el grupo que
    rige de inmediato —el accionable hoy— no existe en los datos.

    Devuelve la regla general primero, que es la que da el `inicio` del
    documento.
    """
    m = _EXCEPCION.search(seccion_vigencia)
    if not m:
        return []

    # La oración en curso, no la sección entera: si hay párrafos previos, sus
    # fechas no describen la regla general.
    previo = seccion_vigencia[:m.start()]
    cortes = list(_FIN_ORACION.finditer(previo))
    principal = previo[cortes[-1].end():] if cortes else previo

    resto = seccion_vigencia[m.end():]
    fin = _FIN_ORACION.search(resto)
    excepcion = resto[:fin.start()] if fin else resto

    plazos = []
    for texto in (principal, excepcion):
        texto = texto.strip()
        if len(texto) < 15:
            continue
        plazos.append(_plazo(_normaliza_frase(texto, maxlen=400), fecha_base))
    # Con un solo tramo no hay nada que desdoblar: la vigencia global ya lo dice.
    if len(plazos) != 2:
        return []
    # Y si ningún tramo aportó una fecha, el desdoblamiento tampoco aporta nada.
    #
    # No es una optimización: "salvo" y "excepto" también encabezan salvedades
    # que no hablan de vigencia. La NCG 477/2022 declara vigencia inmediata y
    # después dice que las instrucciones se aplican a las solicitudes en curso
    # "salvo que el solicitante manifieste lo contrario". Ese "salvo" partía la
    # sección en dos tramos sin fecha, y el primero —que el llamador usa como
    # regla general— pisaba con "ver texto" una vigencia inmediata correcta.
    if all(pz.get("inicio") in _INICIO_DEGRADADO for pz in plazos):
        return []
    return plazos


def _fecha_presente_anio(texto: str, fecha_base: str | None) -> dict | None:
    """Fecha cuyo año el documento expresa como "el presente año".

    La NCG 472/2022 dice "entra en vigencia a contar del 12 de julio del
    presente año". Día y mes están declarados; el año es el del propio
    documento, así que sin la fecha de publicación no hay nada que resolver y
    se devuelve `None` para que el llamador caiga en "ver texto".
    """
    if not fecha_base:
        return None
    m = _FECHA_PRESENTE_ANIO.search(texto)
    if not m:
        return None
    mes = MESES.get(m.group(2).lower())
    if not mes:
        return None
    try:
        anio = date.fromisoformat(fecha_base).year
        destino = date(anio, mes, int(m.group(1)))
    except ValueError:
        return None
    return _vigencia_calculada(
        destino, fecha_base, "año tomado de la fecha del documento", m.group(0)
    )


def _parse_tramos_prosa(seccion_vigencia: str, fecha_base: str | None = None) -> list[dict]:
    """Tramos de vigencia redactados como oraciones seguidas, sin viñetas.

    Es la tercera forma en que la CMF escalona una vigencia, y la más común en
    las circulares: ni viñetas (`_parse_plazos`) ni un "salvo" que introduzca la
    excepción (`_parse_excepciones`), sino dos o tres oraciones que reparten la
    entrada en vigor entre grupos de secciones. La circular 2356/2024 dice que
    el número 1 "rige a contar de esta fecha" y los números 2 al 11 "comenzarán
    a regir el 1 de diciembre del 2024"; la NCG 519/2024 hace lo mismo entre sus
    secciones I-II y su sección III.

    Sin esto, el documento queda descrito por una sola de sus dos fechas y la
    otra no existe en los datos. Y con la vigencia inmediata reconocida como
    corresponde, la que se pierde es siempre la futura —la que obliga a hacer
    algo— porque `_parse_vigencia_global` evalúa la inmediatez antes que las
    fechas.

    Se exige que al menos dos oraciones resuelvan a valores **distintos**: una
    sección de un solo tramo ya queda descrita por la vigencia global, y
    desdoblarla sólo duplicaría el dato.
    """
    tramos: list[dict] = []
    for oracion in _oraciones(seccion_vigencia):
        if len(oracion) < 25:
            continue
        # La oración tiene que decir que algo *entra a regir*. Sin esta
        # exigencia la partición captura los plazos de trámite que conviven en
        # la misma sección —la NCG 524/2024 da a los prestadores "hasta el 3 de
        # febrero de 2025" para presentar su solicitud— y esas fechas entrarían
        # al Calendario como si fueran obligaciones de vigencia. Eran 7 de 9
        # tramos en ese documento.
        if not re.search(_VERBO_VIGENCIA, oracion, re.IGNORECASE):
            continue
        plazo = _plazo(oracion, fecha_base)
        # Una oración que no fija cuándo rige nada no es un tramo: en esta
        # sección abundan las que explican a qué reporte aplica el cambio o qué
        # pueden hacer las entidades voluntariamente.
        if plazo["inicio"] in _INICIO_DEGRADADO:
            continue
        tramos.append(plazo)

    if len({t["inicio"] for t in tramos}) < 2:
        return []
    # Muchos tramos significa que la partición está leyendo prosa que no reparte
    # vigencias. Ante la duda no se desdobla: la vigencia global sigue ahí, y un
    # Calendario con fechas de más es peor que uno con una fecha de menos.
    return tramos if len(tramos) <= _MAX_TRAMOS_PROSA else []


def _oraciones(texto: str) -> list[str]:
    """La sección partida en oraciones, normalizadas y sin las vacías."""
    partes, previo = [], 0
    for corte in _FIN_ORACION.finditer(texto):
        partes.append(texto[previo:corte.end()])
        previo = corte.end()
    partes.append(texto[previo:])
    return [t for t in (_normaliza_frase(p, maxlen=400) for p in partes) if t]


def _parse_vigencia_global(texto_vigencia: str | None, fecha_base: str | None = None) -> dict:
    """Clasifica el texto de vigencia en un dict estructurado.

    `fecha_base` es la fecha de publicación del documento, y sólo se usa para
    resolver los plazos declarados en forma relativa ("en el plazo de un mes
    contado desde su publicación"). No se usa para nada más: en particular, no
    rellena la vigencia cuando el texto no la declara. Esa distinción es la
    línea entre derivar y suponer, y es lo que separa este parámetro del bug
    histórico en que las 126 entradas con vigencia fechada repetían la fecha de
    su propio documento.
    """
    if not texto_vigencia:
        return {"inicio": "no especificado"}

    texto = texto_vigencia.lower()
    resultado: dict[str, Any] = {}

    # El plazo relativo se evalúa primero porque su redacción contiene giros que
    # las otras ramas leerían mal: "contado desde su publicación" se parece a
    # una vigencia inmediata, y el documento no trae ninguna fecha escrita que
    # las ramas siguientes pudieran encontrar. Sólo gana cuando se pudo
    # resolver de verdad; si no, sigue el orden de siempre.
    relativa = _resolver_plazo_relativo(texto_vigencia, fecha_base)
    if relativa:
        resultado.update(relativa)
    elif _INMEDIATA.search(texto_vigencia):
        resultado["inicio"] = "inmediata"
    else:
        fechas = _FECHA_SPAN.findall(texto_vigencia)
        if fechas:
            d, mes, y = fechas[0]
            resultado["inicio"] = f"{y}-{MESES.get(mes.lower(), 1):02d}-{_dia_a_int(d):02d}"
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
                relativa = _fecha_presente_anio(texto_vigencia, fecha_base)
                resultado.update(relativa or {"inicio": "ver texto"})

    # Detectar cláusula de transición
    m_trans = re.search(r"a más tardar el\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})", texto_vigencia, re.IGNORECASE)
    if m_trans:
        resultado["plazo_transicion"] = _fecha_str_to_iso(m_trans.group(1))

    # Detectar "cierre del mes siguiente"
    if "cierre del mes siguiente" in texto:
        resultado["inicio"] = "cierre_mes_siguiente"

    return resultado



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


def _acciones_unicas(crudas: list[str]) -> list[str]:
    """Acciones sin repetir, en el orden en que aparecen en el documento.

    Se hacía con `list({...})`, y el orden de un set de strings cambia entre
    procesos: la misma entrada reparseada dos veces producía JSON distinto y
    cada corrida de `reparse.py` ensuciaba el diff con permutaciones de
    `acciones` que no eran ningún cambio de dato. El orden del texto además es
    el útil: dice en qué orden el documento hace las cosas.
    """
    vistas: dict[str, None] = {}
    for a in crudas:
        vistas.setdefault(a.capitalize(), None)
    return list(vistas)


def _vigencias_impuestas(text: str, fecha_base: str | None = None) -> dict[int, dict]:
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
        impuestas[numero] = _parse_vigencia_global(_acotar_cita(m.group(2)), fecha_base)
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
    return f"{y}-{MESES.get(mes.lower(), 1):02d}-{_dia_a_int(d):02d}"


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
