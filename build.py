#!/usr/bin/env python3
"""Excel -> ETL (validación y limpieza) -> dist/index.html.

Uso:  python build.py [--resumen RUTA.md]
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from pipeline import etl
from pipeline.adapter import a_acts

ROOT = Path(__file__).parent
os.chdir(ROOT)
CFG = json.loads(Path("config.json").read_text(encoding="utf8"))
TEMPLATE = Path("src/template.html").read_text(encoding="utf8")
TEMAS = set(re.findall(r"^ (\w+):\['", TEMPLATE, re.M))  # temas definidos en la plantilla


def js(obj):  # JSON seguro dentro de <script>
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def resumen_md(final, rechazados, bitacora, avisos):
    L = ["## Pipeline Semana de Ingeniería", "",
         f"- Actividades publicadas: **{len(final)}**",
         f"- Registros rechazados: **{len(rechazados)}**",
         f"- Correcciones automáticas: **{len(bitacora)}**", ""]
    for a in avisos:
        L.append(f"> ⚠️ {a}")
    if len(rechazados):
        L += ["", "### Registros rechazados (no aparecen en el sitio)", "",
              "| fila_origen | Título | Motivo |", "|---|---|---|"]
        for _, r in rechazados.head(40).iterrows():
            L.append(f"| {r['fila_origen']} | {str(r['Título'])[:60]} | {r['motivo_rechazo']} |")
        if len(rechazados) > 40:
            L.append(f"\n…y {len(rechazados) - 40} más en `registros_rechazados.csv` (artefacto *reportes*).")
        L.append("\n`fila_origen` cuenta los registros desde la primera fila debajo del encabezado del Excel.")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resumen", help="ruta donde escribir el resumen en Markdown (p. ej. $GITHUB_STEP_SUMMARY)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        final, rechazados, bitacora = etl.ejecutar_pipeline(CFG, "salida")
    except etl.ErrorCritico as e:
        print(f"ERROR CRÍTICO DEL PIPELINE: {e}", file=sys.stderr)
        sys.exit(1)

    acts, feat, avisos, errores = a_acts(final, CFG, TEMAS)
    if errores:
        print("Errores en los datos de apoyo:", *errores, sep="\n  - ", file=sys.stderr)
        sys.exit(1)
    invalidos = sorted({a["theme"] for a in acts} - TEMAS)
    if invalidos:
        print(f"Temas que no existen en la plantilla: {invalidos}", file=sys.stderr)
        sys.exit(1)

    d0 = date.fromisoformat(min(a["d1"] for a in acts))
    d1 = date.fromisoformat(max(a["d2"] for a in acts))
    days = [(d0 + timedelta(i)).isoformat() for i in range((d1 - d0).days + 1)]
    demo = os.environ.get("DEMO_NOW") or CFG.get("demo_now")
    now = f"new Date('{demo}')" if demo else "new Date()"

    html = (TEMPLATE
            .replace("/*__ACTS__*/[]", js(acts))
            .replace("/*__NOW__*/new Date()", now)
            .replace("/*__ALLDAYS__*/[]", js(days))
            .replace("/*__FEAT__*/[]", js(feat))
            .replace("<!--__FOOT__-->", CFG["footer"])
            .replace("<!--__TITLE__-->", CFG["title"]))
    Path("dist").mkdir(exist_ok=True)
    Path("dist/index.html").write_text(html, encoding="utf8")
    Path("dist/.nojekyll").write_text("")

    print(f"OK: {len(acts)} actividades, {len(days)} días, {len(feat)} destacadas, "
          f"{len(rechazados)} rechazadas -> dist/index.html")
    for a in avisos:
        print("Aviso:", a)
    if args.resumen:
        with open(args.resumen, "a", encoding="utf8") as f:
            f.write(resumen_md(final, rechazados, bitacora, avisos))
    if CFG.get("fallar_si_hay_rechazados") and len(rechazados):
        print("fallar_si_hay_rechazados=true y hay registros rechazados.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
