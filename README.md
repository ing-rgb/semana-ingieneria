# Semana de Ingeniería: Excel → pipeline → GitHub Pages

```
data/raw/Programa_32IIT.xlsx ──► pipeline/etl.py ──► pipeline/adapter.py ──► src/template.html ──► dist/index.html ──► GitHub Pages
                                 (carga, layout,       (ACTS: tema, cat,
                                  validación, limpieza) id estable, desc)
                                        └─► salida/ (datos procesados, rechazados, bitácora)
```

## Uso diario
1. Reemplaza `data/raw/Programa_32IIT.xlsx` (encabezados en la fila 9; si cambia, ajusta `fila_encabezado_excel` en `config.json`).
2. Commit a `main`. GitHub Actions corre `build.py` y publica.
3. En la pestaña **Actions** → ejecución → *Summary* ves cuántas actividades se publicaron y cuáles se rechazaron y por qué. El artefacto **reportes** trae `registros_rechazados`, `bitacora_correcciones` y `datos_procesados`.

## Activar GitHub Pages (una vez)
Settings → Pages → Source: **GitHub Actions**.

## Qué se publica y qué no
Solo los registros que pasan la limpieza. Se rechazan duplicados, actividades fuera de catálogo, fechas/horas inválidas, virtuales sin enlace, etc. Para que un rechazo detenga el despliegue, pon `"fallar_si_hay_rechazados": true`.

**Catálogo de actividades**: `cat_por_actividad` en `config.json` (clave = valor válido de la columna *Actividad*, valor = pestaña del sitio: `conf`, `taller`, `concurso`, `depcult`, `otras`). Si tu Excel usa otros tipos (Curso, Concurso, Deportiva…), agrégalos ahí o se rechazarán.

## Lo que el Excel no trae
- **Tema** (imagen de la tarjeta): se infiere por palabras del título (`temas_por_palabra`); si no hay coincidencia, `general`.
- **Descripción** y **destacados**: opcionales en `data/extras.csv` (`titulo, descripcion, tema, destacado`), unido por título. Sin descripción se usa una frase genérica; sin destacados, la primera actividad de cada día.
- **id** de cada actividad: hash de título + fecha + hora de inicio, así la agenda que guarda cada visitante no se rompe si reordenas el Excel (pero sí si cambias el título, la fecha o la hora de inicio).

## Local
    pip install -r requirements.txt
    python build.py                                  # genera dist/index.html y salida/
    DEMO_NOW=2026-09-21T10:00:00 python build.py     # congela el reloj para probar

El archivo `Programa_32IIT.xlsx` incluido es de **ejemplo** (con 3 filas de prueba que se rechazan a propósito). Sustitúyelo por el real.
