# Buzón ciudadano · PriorizaComunidad — Sitio web (v0.2)

Sitio web + backend en **Python / Flask**. Hoy funciona con **datos en memoria** (para poder
presentar y probar sin bases de datos); la siguiente etapa es el **puente** hacia Oracle XE y PostgreSQL.

## Cómo ejecutarlo
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                      # abre http://127.0.0.1:5000
python -m unittest discover -s tests -v   # pruebas (21)
```
Usuarios de demostración (contraseña `demo1234`): `cocode1@demo.gt`, `cocode2@demo.gt`,
`personal@demo.gt`, `personal2@demo.gt`, `alcalde@demo.gt`, `admin@demo.gt`.
En producción define `SECRET_KEY` como variable de entorno.

## Novedades de la v0.2
- **Diseño moderno** (`app/static/estilo.css`): barra con degradado, tarjetas de reporte, etiquetas de color,
  línea de tiempo y tarjetas de cifras. **Verde = solucionado** (se reserva solo para eso).
- **Apartado del alcalde** (`/alcalde`): pestañas *Por decidir*, *Pospuestos*, *En ejecución* y *Resueltos*;
  por cada reporte puede **aprobar y asignar**, **posponer** o **rechazar** (posponer y rechazar exigen motivo).
- Datos de demostración ampliados a 7 reportes en distintos estados.
- Nuevos métodos del repositorio para el puente: `registrar_decision`, `ultima_decision`.
- Tabla nueva en Oracle: `DECISION_ALCALDE` (y vista `v_por_decidir`). Nuevo evento en PostgreSQL: `DECISION_ALCALDE`.
- El alcalde también puede rechazar un reporte REVISADO (antes solo el personal).

## Estructura
```
run.py                  punto de entrada
app/__init__.py         fábrica de la app (aquí se inyecta el repositorio)
app/rutas.py            rutas HTTP, sesión, CSRF, permisos por rol
app/servicios.py        REGLAS DE NEGOCIO (RN-xx): estados, prioridad, permisos
app/repositorio.py      acceso a datos (hoy en memoria) <- lo que se reemplaza en el puente
app/templates/          pantallas (Bootstrap 5)
tests/test_app.py       pruebas automáticas
```
Regla de oro: **`rutas.py` y `servicios.py` no cambian** al conectar las bases de datos; solo se
escribe una clase nueva (por ejemplo `RepositorioOraclePostgres`) con los mismos métodos que
`RepositorioMemoria` y se pasa en `create_app(RepositorioOraclePostgres(...))`.

## Guía para el puente (qué hace cada método en la base real)
| Método del repositorio | Oracle XE | PostgreSQL |
|---|---|---|
| `usuario_por_correo`, `usuario_por_id`, `responsables` | `USUARIO` + `USUARIO_ROL` + `ROL` | — |
| `comunidades`, `categorias`, `departamentos` (y sus `comunidad/categoria/departamento`) | catálogos | — |
| `crear_reporte` | `INSERT REPORTE` (el trigger genera código, estado y versión) + `INSERT EVALUACION_PRIORIDAD` + evento `CREADO` en `COLA_EVENTO` | luego, evento en `EVENTO_SEGUIMIENTO` |
| `obtener_reporte`, `listar_reportes` | `SELECT` sobre `REPORTE` (+ `ESTADO`); el campo `estado` del dict es `ESTADO.codigo` | — |
| `cambiar_estado` | `UPDATE REPORTE ... WHERE id_reporte=:id AND version=:v` (0 filas ⇒ `ConflictoVersion`) + `COLA_EVENTO`, **en la misma transacción** | evento en `EVENTO_SEGUIMIENTO` |
| `asignar` | cerrar `ASIGNACION` activa, `INSERT ASIGNACION`, `UPDATE REPORTE` a ASIGNADO (misma transacción) + `COLA_EVENTO` | evento |
| `guardar_evaluacion`, `ultima_evaluacion` | `EVALUACION_PRIORIDAD` (la vigente es la última por fecha) | evento interno |
| `registrar_decision`, `ultima_decision` | `INSERT/SELECT` en `DECISION_ALCALDE` (en la misma transacción que el cambio de estado; posponer genera evento) | evento `DECISION_ALCALDE` |
| `asignacion_activa` | `ASIGNACION WHERE activa = 1` | — |
| `eventos(id, solo_visibles)` | — | `EVENTO_SEGUIMIENTO` ordenado por `fecha_evento, id_evento` (`v_linea_tiempo_ciudadano` para el ciudadano) |

Pendiente para la etapa del puente: tarea en segundo plano que lea `COLA_EVENTO` (PENDIENTE) y
la envíe a PostgreSQL con `ON CONFLICT (id_evento) DO NOTHING`, reintentando hasta confirmar.
Drivers: `oracledb` (modo thin) y `psycopg`; usar siempre parámetros (`:id`, `%s`), nunca concatenar SQL.

## Decisiones tomadas (revisar con el grupo)
- El COCODE ve **solo los reportes que creó** (RN-08).
- El departamento de una asignación se toma del **responsable elegido** (así siempre pertenece a él).
- Puntaje sugerido: gravedad leve/moderada/grave (10/22/33), personas por rangos, riesgo (0/14/24),
  tiempo 1 punto por día hasta 15. Los valores finales los define la municipalidad (RN-18).
- Contraseñas con hash de Werkzeug; protección CSRF propia; mensajes de login genéricos.

## Siguiente etapa (paso 3 del plan)
Confirmación del COCODE ("estoy conforme / no está resuelto") y página pública de obras realizadas.
