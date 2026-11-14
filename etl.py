"""ETL del programa (Excel -> datos limpios). Es el pipeline del notebook, sin dependencias de Colab.

Etapas: 2 carga · 3 layout · 4 validación de información · 5 limpieza · 6 selección
        7 transformación · 8 almacenamiento.
"""
import datetime
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger("pipeline")

REGEX_HORA = re.compile(r"^\d{2}:\d{2}$")
REGEX_URL = re.compile(r"^https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&\/=]*)$")
FORMATOS = {"date": re.compile(r"^\d{4}-\d{2}-\d{2}$"), "time": re.compile(r"^\d{2}:\d{2}$")}

CAMPOS = ["Actividad", "Fecha inicio", "Fecha fin", "Hora inicio", "Hora fin", "Tipo", "Salón",
          "Título", "Ponente", "Empresa / Institución", "Maestro de ceremonias", "Enlace"]
TIPOS = ["str", "date", "date", "time", "time", "str", "str", "str", "str", "str", "str", "str"]
OBLIGATORIOS = ["Actividad", "Fecha inicio", "Fecha fin", "Hora inicio", "Hora fin", "Tipo", "Título"]
DEFAULTS = {"Salón": "Por definir", "Enlace": None, "Ponente": "Por confirmar",
            "Empresa / Institución": "Por confirmar", "Maestro de ceremonias": "Por confirmar"}
SALON_VIRTUAL = "Plataforma Virtual (Teams/Zoom)"


class ErrorCritico(Exception):
    """Error que obliga a detener el pipeline."""


def generar_diccionario(ruta_destino):
    pd.DataFrame({"Campo": CAMPOS, "Tipo esperado": TIPOS}).to_excel(
        ruta_destino, sheet_name="Diccionario", index=False)


def construir_diccionario(cfg):
    """Arma las reglas a partir de config.json (catálogo de actividades, fechas del evento)."""
    return {
        "columnas_requeridas": list(CAMPOS),
        "obligatorios": list(OBLIGATORIOS),
        "catalogos": {"Actividad": list(cfg["cat_por_actividad"]), "Tipo": ["presencial", "virtual"]},
        "defaults": DEFAULTS,
        "salon_virtual": SALON_VIRTUAL,
        "fecha_min": cfg["fecha_min"],
        "fecha_max": cfg["fecha_max"],
    }


# ── Etapa 2: carga ───────────────────────────────────────────────────────────
def cargar_excel(ruta, fila_encabezado=9):
    return pd.read_excel(ruta, header=fila_encabezado - 1)


def validar_carga(ruta, fila_encabezado=9):
    p = Path(ruta)
    if not p.exists():
        raise ErrorCritico(f"El archivo no existe: {ruta}")
    if p.suffix.lower() not in (".xlsx", ".xls"):
        raise ErrorCritico(f"Formato no soportado: {p.suffix}")
    try:
        df = cargar_excel(ruta, fila_encabezado)
    except Exception as e:
        raise ErrorCritico(f"Error al leer el archivo: {e}") from e
    df = df.dropna(how="all").reset_index(drop=True)
    if df.empty:
        raise ErrorCritico("El conjunto de datos está vacío.")
    log.info("Carga validada: %d registros, %d columnas.", len(df), df.shape[1])
    return df


# ── Etapa 3: layout ──────────────────────────────────────────────────────────
def cargar_columnas_esperadas(ruta_diccionario):
    try:
        dic = pd.read_excel(ruta_diccionario, sheet_name="Diccionario").dropna(subset=["Campo"])
        return dict(zip(dic["Campo"].str.strip(), dic["Tipo esperado"].str.strip()))
    except Exception as e:
        raise ErrorCritico(f"No se pudo leer el diccionario de datos: {e}") from e


def _tipo_ok(tipo, valor):
    if tipo == "str":
        return isinstance(valor, str)
    if tipo == "date" and isinstance(valor, datetime.date):   # incluye Timestamp (celda de Excel con fecha real)
        return True
    if tipo == "time" and isinstance(valor, datetime.time):   # celda de Excel con hora real
        return True
    return bool(FORMATOS[tipo].match(str(valor)))


def validar_layout(df, ruta_diccionario, fila_base=10):
    """Devuelve (df con solo las columnas del diccionario, lista de errores). Faltar una columna detiene todo."""
    errores = []
    esperadas = cargar_columnas_esperadas(ruta_diccionario)
    df.columns = [str(c).strip() for c in df.columns]
    for col in df.columns:
        if col not in esperadas:
            errores.append({"columna": col, "fila": None, "motivo": "Columna no definida en el diccionario"})
    faltantes = [c for c in esperadas if c not in df.columns]
    if faltantes:
        raise ErrorCritico(f"Faltan columnas requeridas: {faltantes}")
    for col, tipo in esperadas.items():
        if tipo != "str" and tipo not in FORMATOS:
            errores.append({"columna": col, "fila": None, "motivo": f"Tipo '{tipo}' sin regla de validación"})
            continue
        for i, v in df[col].dropna().items():
            if not _tipo_ok(tipo, v):
                errores.append({"columna": col, "fila": i + fila_base, "motivo": f"'{v}' no cumple el tipo {tipo}"})
    return df[list(esperadas)], errores


# ── Etapa 4: validación de información (informativa, no bloquea) ─────────────
def validar_informacion(df, catalogo_actividades):
    errores = []
    vacio = lambda s: s.isnull() | (s.astype(str).str.strip() == "")
    for c in OBLIGATORIOS:
        if c in df.columns:
            errores += [{"fila": i, "error": f"Campo obligatorio vacío: {c}"} for i in df.index[vacio(df[c])]]
    errores += [{"fila": i, "error": "Registro duplicado"} for i in df.index[df.duplicated()]]
    errores += [{"fila": i, "error": "Actividad no autorizada"}
                for i in df.index[~df["Actividad"].isin(catalogo_actividades)]]
    errores += [{"fila": i, "error": "Tipo no válido"}
                for i in df.index[~df["Tipo"].astype(str).str.strip().str.lower().isin(["presencial", "virtual"])]]
    return pd.DataFrame(errores, columns=["fila", "error"])


# ── Etapa 5: limpieza ────────────────────────────────────────────────────────
def _norm_texto(v):
    if pd.isna(v):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s if s else None


def _corregir_catalogo(valor, catalogo):
    if valor is None:
        return None
    return {c.casefold(): c for c in catalogo}.get(valor.casefold())


def _corregir_hora(valor):
    if valor is None:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", str(valor).strip())
    if not m:
        return None
    hora = f"{int(m.group(1)):02d}:{m.group(2)}"
    return hora if REGEX_HORA.match(hora) else None


def _parsear_fecha(valor):
    if valor is None:
        return None
    f = pd.to_datetime(valor, errors="coerce")
    return None if pd.isna(f) else f.date()


def limpiar(df, dic):
    """Recuperable -> se corrige. No recuperable -> `rechazados` con motivo. Devuelve (validos, rechazados, bitacora)."""
    try:
        faltantes = [c for c in dic["columnas_requeridas"] if c not in df.columns]
        if faltantes:
            raise ErrorCritico(f"Faltan columnas requeridas: {faltantes}")

        cols = dic["columnas_requeridas"]
        df = df[cols].copy().reset_index(drop=True)
        df.insert(0, "fila_origen", range(1, len(df) + 1))
        correcciones = []

        def anotar(fila, campo, antes, despues, accion):
            correcciones.append({"fila_origen": fila, "campo": campo, "valor_original": antes,
                                 "valor_nuevo": despues, "accion": accion})

        # 1) Normalizar texto y duplicados exactos
        for c in cols:
            antes = df[c].copy()
            df[c] = df[c].map(_norm_texto).astype(object)
            df[c] = df[c].where(df[c].notna(), None)          # NaN -> None (igual en pandas 2 y 3)
            for i in df.index[antes.map(lambda v: isinstance(v, str)) & (antes.astype(str) != df[c].astype(str))]:
                anotar(df.at[i, "fila_origen"], c, antes[i], df.at[i, c], "Espacios normalizados")

        dup = df.duplicated(subset=cols, keep="first")
        rechazados = []
        for i in df.index[dup]:
            r = df.loc[i].copy()
            r["motivo_rechazo"] = "Registro duplicado"
            rechazados.append(r)
        df = df[~dup]

        # 2) Catálogos
        for campo, cat in dic["catalogos"].items():
            for i in df.index:
                v = df.at[i, campo]
                nuevo = _corregir_catalogo(v, cat)
                if nuevo is not None and nuevo != v:
                    anotar(df.at[i, "fila_origen"], campo, v, nuevo, "Corregido a catálogo")
                    df.at[i, campo] = nuevo

        # 3) Horas y fechas
        for c in ("Hora inicio", "Hora fin"):
            for i in df.index:
                v = df.at[i, c]
                n = _corregir_hora(v)
                if n is not None and n != v and str(v).strip() != n + ":00":
                    anotar(df.at[i, "fila_origen"], c, v, n, "Formato de hora corregido")
                df.at[i, c] = n if n is not None else v
        for c in ("Fecha inicio", "Fecha fin"):
            df[c + "_dt"] = df[c].map(_parsear_fecha)

        # 4) Salón de eventos virtuales (antes de los valores por defecto; en el notebook iba después y nunca se aplicaba)
        for i in df.index:
            if df.at[i, "Tipo"] == "virtual" and pd.isna(df.at[i, "Salón"]):
                anotar(df.at[i, "fila_origen"], "Salón", None, dic["salon_virtual"], "Nulo -> valor por defecto")
                df.at[i, "Salón"] = dic["salon_virtual"]

        # 5) Valores por defecto para opcionales
        for c, d in dic["defaults"].items():
            if d is not None:
                for i in df.index[df[c].isna()]:
                    anotar(df.at[i, "fila_origen"], c, None, d, "Nulo -> valor por defecto")
                df[c] = df[c].fillna(d)

        # 6) Reglas que determinan rechazo
        fmin = pd.to_datetime(dic["fecha_min"]).date()
        fmax = pd.to_datetime(dic["fecha_max"]).date()

        def motivos(r):
            m = []
            for c in dic["obligatorios"]:
                if r[c] is None or pd.isna(r[c]):
                    m.append(f"Campo obligatorio vacío: {c}")
            for campo, cat in dic["catalogos"].items():
                if r[campo] is not None and r[campo] not in cat:
                    m.append(f"Valor fuera de catálogo en {campo}: '{r[campo]}'")
            for c in ("Hora inicio", "Hora fin"):
                if r[c] is not None and not REGEX_HORA.match(str(r[c])):
                    m.append(f"Formato de hora inválido en {c}: '{r[c]}'")
            fi, ff = r["Fecha inicio_dt"], r["Fecha fin_dt"]
            if r["Fecha inicio"] is not None and fi is None:
                m.append(f"Fecha inválida en Fecha inicio: '{r['Fecha inicio']}'")
            if r["Fecha fin"] is not None and ff is None:
                m.append(f"Fecha inválida en Fecha fin: '{r['Fecha fin']}'")
            if fi and ff:
                if ff < fi:
                    m.append("Fecha fin anterior a Fecha inicio")
                if fi < fmin or ff > fmax:
                    m.append(f"Fecha fuera del periodo del evento ({fmin} a {fmax})")
                hi, hf = r["Hora inicio"], r["Hora fin"]
                if fi == ff and hi and hf and REGEX_HORA.match(str(hi)) and REGEX_HORA.match(str(hf)) and hf <= hi:
                    m.append("Hora fin no es posterior a Hora inicio")
            if r["Tipo"] == "virtual":
                if not r["Enlace"] or not REGEX_URL.match(str(r["Enlace"])):
                    m.append("Actividad virtual sin enlace válido")
            elif r["Tipo"] == "presencial" and r["Salón"] is None:
                m.append("Actividad presencial sin salón")
            return m

        validos = []
        for _, r in df.iterrows():
            mot = motivos(r)
            if mot:
                r = r.copy()
                r["motivo_rechazo"] = "; ".join(mot)
                rechazados.append(r)
            else:
                validos.append(r)

        cols_validos = ["fila_origen"] + cols
        validos = pd.DataFrame([r.to_dict() for r in validos], columns=cols_validos + ["Fecha inicio_dt", "Fecha fin_dt"])
        if not validos.empty:
            validos["Fecha inicio"] = validos["Fecha inicio_dt"]
            validos["Fecha fin"] = validos["Fecha fin_dt"]
        validos = validos[cols_validos]
        rechazados = pd.DataFrame([r.to_dict() for r in rechazados], columns=cols_validos + ["motivo_rechazo"])
        bitacora = pd.DataFrame(correcciones, columns=["fila_origen", "campo", "valor_original", "valor_nuevo", "accion"])
        log.info("Limpieza: %d válidos | %d rechazados | %d correcciones", len(validos), len(rechazados), len(bitacora))
        return validos.reset_index(drop=True), rechazados.reset_index(drop=True), bitacora
    except ErrorCritico:
        raise
    except Exception as e:
        raise ErrorCritico(f"Fallo inesperado en la limpieza: {e}") from e


# ── Etapa 6: selección ───────────────────────────────────────────────────────
def seleccion(df):
    if df is None or df.empty:
        raise ErrorCritico("No hay registros para seleccionar")
    faltantes = [c for c in OBLIGATORIOS if c not in df.columns]
    if faltantes:
        raise ErrorCritico(f"Faltan columnas obligatorias {faltantes}")
    return df[[c for c in CAMPOS if c in df.columns]].copy()


# ── Etapa 7: transformación ──────────────────────────────────────────────────
def transformacion(df):
    t = df.copy()
    t["Fecha_Hora_Inicio"] = pd.to_datetime(t["Fecha inicio"].astype(str) + " " + t["Hora inicio"].astype(str), errors="coerce")
    t["Fecha_Hora_Fin"] = pd.to_datetime(t["Fecha fin"].astype(str) + " " + t["Hora fin"].astype(str), errors="coerce")

    def ponente(r):
        p, e = r.get("Ponente"), r.get("Empresa / Institución")
        if pd.isna(p) or str(p).strip() in ("", "nan", "None"):
            return "Por confirmar"
        if not pd.isna(e) and str(e).strip() not in ("", "nan", "None"):
            return f"{str(p).strip()} ({str(e).strip()})"
        return str(p).strip()

    t["Presentacion_Ponente"] = t.apply(ponente, axis=1)
    t["Duracion_Minutos"] = (t["Fecha_Hora_Fin"] - t["Fecha_Hora_Inicio"]).dt.total_seconds() / 60
    return t


# ── Etapa 8: almacenamiento ──────────────────────────────────────────────────
def almacenar(validos, rechazados, bitacora, carpeta="salida"):
    out = Path(carpeta)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ErrorCritico(f"No se pudo crear la carpeta de salida '{carpeta}': {e}") from e
    destinos = {"datos_procesados.xlsx": validos, "datos_procesados.csv": validos,
                "registros_rechazados.xlsx": rechazados, "registros_rechazados.csv": rechazados,
                "bitacora_correcciones.csv": bitacora}
    for nombre, datos in destinos.items():
        ruta = out / nombre
        try:
            if nombre.endswith(".xlsx"):
                datos.to_excel(ruta, index=False)
                leidos = len(pd.read_excel(ruta))
            else:
                datos.to_csv(ruta, index=False, encoding="utf-8-sig")
                leidos = len(pd.read_csv(ruta, encoding="utf-8-sig"))
        except Exception as e:
            raise ErrorCritico(f"Error al escribir '{ruta}': {e}") from e
        if leidos != len(datos):
            raise ErrorCritico(f"Verificación fallida en '{nombre}': {len(datos)} filas escritas, {leidos} leídas.")


def ejecutar_pipeline(cfg, carpeta_salida="salida"):
    """Corre todas las etapas. Devuelve (validos_transformados, rechazados, bitacora)."""
    dic = construir_diccionario(cfg)
    fila_enc = cfg.get("fila_encabezado_excel", 9)
    df = validar_carga(cfg["excel"], fila_enc)

    ruta_dic = Path(cfg.get("diccionario", ""))
    if ruta_dic.exists():
        df, errores_layout = validar_layout(df, ruta_dic, fila_base=fila_enc + 1)
        log.info("Layout: %d observaciones", len(errores_layout))
        for e in errores_layout[:10]:
            log.warning("  layout: %s", e)
    else:
        log.warning("Diccionario no encontrado (%s); se omite la validación de layout.", ruta_dic)

    err_info = validar_informacion(df, dic["catalogos"]["Actividad"])
    log.info("Validación de información: %d observaciones (la limpieza decide qué se rechaza)", len(err_info))

    validos, rechazados, bitacora = limpiar(df, dic)
    if validos.empty:
        raise ErrorCritico("Ningún registro pasó la limpieza; revisa registros_rechazados.csv")
    final = transformacion(seleccion(validos))
    almacenar(final, rechazados, bitacora, carpeta_salida)
    return final, rechazados, bitacora
