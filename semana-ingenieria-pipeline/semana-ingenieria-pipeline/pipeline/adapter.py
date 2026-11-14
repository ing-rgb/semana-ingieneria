"""Convierte datos_procesados (salida del ETL) en el arreglo ACTS que consume la plantilla HTML."""
import csv
import hashlib
import re
import unicodedata
from pathlib import Path

PLACEHOLDERS = {"por definir", "por confirmar", "plataforma virtual (teams/zoom)", ""}
BOOL_SI = {"si", "sí", "yes", "true", "1", "x"}


def norm(s):
    """Minúsculas, sin acentos ni espacios repetidos (para comparar títulos y buscar palabras clave)."""
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _limpio(v):
    v = "" if v is None else str(v).strip()
    return "" if norm(v) in PLACEHOLDERS else v


def cargar_extras(ruta, temas_validos):
    """data/extras.csv (opcional): titulo, descripcion, tema, destacado. Se une por título."""
    extras, errores = {}, []
    p = Path(ruta or "")
    if not p.exists():
        return extras, errores
    with open(p, newline="", encoding="utf-8-sig") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            t = (r.get("titulo") or "").strip()
            if not t:
                continue
            tema = (r.get("tema") or "").strip()
            if tema and tema not in temas_validos:
                errores.append(f"extras.csv fila {n}: tema '{tema}' no existe")
            extras[norm(t)] = {"desc": (r.get("descripcion") or "").strip(), "theme": tema,
                               "destacado": (r.get("destacado") or "").strip().lower() in BOOL_SI}
    return extras, errores


def inferir_tema(titulo, actividad, temas_por_palabra):
    t = norm(titulo) + " " + norm(actividad)
    for tema, palabras in temas_por_palabra.items():
        for kw in palabras:
            if re.search(r"\b" + re.escape(norm(kw)), t):
                return tema
    return "general"


def a_acts(df, cfg, temas_validos):
    """DataFrame -> (lista ACTS, lista FEAT, avisos, errores)."""
    extras, errores = cargar_extras(cfg.get("extras"), temas_validos)
    avisos, usados = [], set()
    acts, vistos = [], {}
    for r in df.sort_values(["Fecha inicio", "Hora inicio"]).to_dict("records"):
        titulo = str(r["Título"]).strip()
        d1, d2 = str(r["Fecha inicio"])[:10], str(r["Fecha fin"])[:10]
        virtual = str(r["Tipo"]).strip().lower() == "virtual"
        # id estable: no depende del orden de las filas, así la agenda guardada por cada visitante no se rompe
        base = "a" + hashlib.sha1(f"{norm(titulo)}|{d1}|{r['Hora inicio']}".encode()).hexdigest()[:8]
        vistos[base] = vistos.get(base, 0) + 1
        aid = base if vistos[base] == 1 else f"{base}-{vistos[base]}"

        ex = extras.get(norm(titulo), {})
        if ex:
            usados.add(norm(titulo))
        ponente = _limpio(r.get("Ponente"))
        inst = _limpio(r.get("Empresa / Institución"))
        desc = ex.get("desc") or (f"{r['Actividad']} a cargo de {ponente}." if ponente
                                  else f"{r['Actividad']} de la Semana de Ingeniería.")
        acts.append({
            "id": aid, "title": titulo, "type": r["Actividad"],
            "cat": cfg["cat_por_actividad"].get(r["Actividad"], "otras"),
            "theme": ex.get("theme") or inferir_tema(titulo, r["Actividad"], cfg["temas_por_palabra"]),
            "d1": d1, "d2": d2, "start": str(r["Hora inicio"]), "end": str(r["Hora fin"]),
            "virtual": virtual, "room": "" if virtual else _limpio(r.get("Salón")),
            "speaker": ponente, "inst": inst, "mc": _limpio(r.get("Maestro de ceremonias")),
            "link": str(r["Enlace"]).strip() if virtual and not _vacio(r.get("Enlace")) else "",
            "desc": desc, "tbd": False,
        })
    for t in set(extras) - usados:
        avisos.append(f"extras.csv: el título '{t}' no coincide con ninguna actividad")

    # destacados: los pedidos en extras.csv / config; si no hay, la primera actividad de cada día (máx. 5)
    pedidos = {norm(x) for x in cfg.get("destacados", [])}
    feat = [a["id"] for a in acts
            if extras.get(norm(a["title"]), {}).get("destacado") or any(p in norm(a["title"]) for p in pedidos)]
    if not feat:
        por_dia = {}
        for a in acts:
            por_dia.setdefault(a["d1"], a["id"])
        feat = list(por_dia.values())[:5]
        avisos.append("Sin destacados definidos; se usa la primera actividad de cada día (máx. 5).")
    return acts, feat[:8], avisos, errores


def _vacio(v):
    return v is None or str(v).strip() in ("", "nan", "None")
