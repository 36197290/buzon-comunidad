"""Rutas web. Solo traducen HTTP <-> reglas de negocio (servicios.py)."""
import secrets
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from . import servicios as sv
from .repositorio import ConflictoVersion

web = Blueprint("web", __name__)

COLOR_ESTADO = {"RECIBIDO": "secondary", "REVISADO": "info", "ASIGNADO": "primary",
                "EN_PROCESO": "warning", "SOLUCIONADO": "success", "RECHAZADO": "danger",
                "RETIRADO": "dark"}
COLOR_NIVEL = {"ALTA": "danger", "MEDIA": "warning", "BAJA": "info"}  # el verde se reserva para lo SOLUCIONADO


def repo():
    return current_app.repo


# ---------- seguridad y contexto ----------
@web.before_app_request
def antes_de_cada_peticion():
    g.usuario = None
    if session.get("uid"):
        u = repo().usuario_por_id(session["uid"])
        if u and u["activo"]:
            g.usuario = u
        else:
            session.clear()
    if request.method == "POST":  # proteccion CSRF
        esperado = session.get("csrf", "")
        if not esperado or not secrets.compare_digest(esperado, request.form.get("csrf", "")):
            abort(400, "Formulario invalido o expirado. Recarga la pagina e intenta de nuevo.")


@web.app_context_processor
def contexto():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"usuario": g.get("usuario"), "csrf_token": session["csrf"],
            "COLOR_ESTADO": COLOR_ESTADO, "COLOR_NIVEL": COLOR_NIVEL, "MAXIMOS": sv.MAXIMOS}


@web.app_template_filter("fecha")
def filtro_fecha(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else ""


def rol_requerido(*roles):
    def decorador(vista):
        @wraps(vista)
        def envoltura(*a, **kw):
            if not g.usuario:
                return redirect(url_for("web.login"))
            if not sv.tiene_rol(g.usuario, *roles):
                abort(403)
            return vista(*a, **kw)
        return envoltura
    return decorador


def _fila(r):
    """Reporte + datos de catalogo y ultima evaluacion, listo para mostrar."""
    return dict(r, comunidad=repo().comunidad(r["id_comunidad"])["nombre"],
                categoria=repo().categoria(r["id_categoria"])["nombre"],
                evaluacion=repo().ultima_evaluacion(r["id_reporte"]))


# ---------- sesion ----------
@web.route("/")
def inicio():
    if not g.usuario:
        return redirect(url_for("web.login"))
    destino = "web.mis_reportes" if sv.tiene_rol(g.usuario, "COCODE") else \
        "web.alcalde" if sv.tiene_rol(g.usuario, "ALCALDE") else "web.panel"
    return redirect(url_for(destino))


@web.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = repo().usuario_por_correo(request.form.get("correo", ""))
        if u and u["activo"] and check_password_hash(u["hash_contrasena"], request.form.get("password", "")):
            session.clear()                      # evita fijacion de sesion
            session["uid"] = u["id_usuario"]
            return redirect(url_for("web.inicio"))
        flash("Correo o contraseña incorrectos.", "danger")  # mensaje generico a proposito
    return render_template("login.html")


@web.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("web.login"))


# ---------- COCODE ----------
@web.route("/reportes")
@rol_requerido("COCODE")
def mis_reportes():
    filas = [_fila(r) for r in repo().listar_reportes(id_usuario=g.usuario["id_usuario"])]
    filas.sort(key=lambda f: f["fecha_creacion"], reverse=True)
    return render_template("lista.html", titulo="Mis reportes", filas=filas, es_panel=False)


@web.route("/reportes/nuevo", methods=["GET", "POST"])
@rol_requerido("COCODE")
def nuevo_reporte():
    if request.method == "POST":
        try:
            r = sv.crear_reporte(repo(), g.usuario, request.form)
            flash(f"Reporte {r['codigo']} registrado correctamente.", "success")
            return redirect(url_for("web.detalle", id_reporte=r["id_reporte"]))
        except sv.ErrorNegocio as e:
            flash(str(e), "danger")
            return render_template("nuevo.html", categorias=repo().categorias(), form=request.form), 400
    return render_template("nuevo.html", categorias=repo().categorias(), form={})


# ---------- personal / alcalde ----------
@web.route("/panel")
@rol_requerido("PERSONAL", "ALCALDE", "ADMIN")
def panel():
    estado = request.args.get("estado") or None
    if estado not in sv.ESTADOS:
        estado = None
    filas = [_fila(r) for r in repo().listar_reportes(estado=estado)]
    # urgentes primero, luego mayor puntaje (panel del alcalde)
    filas.sort(key=lambda f: (not f["es_urgente"], -(f["evaluacion"]["total"] if f["evaluacion"] else 0)))
    return render_template("lista.html", titulo="Panel de reportes", filas=filas, es_panel=True,
                           estados=sv.ESTADOS, estado_actual=estado, stats=sv.estadisticas(repo()))


@web.route("/alcalde")
@rol_requerido("ALCALDE")
def alcalde():
    """Apartado del alcalde: decide sobre los reportes ya revisados y sigue la ejecucion."""
    filas = []
    for r in repo().listar_reportes():
        f = _fila(r)
        f["asignacion"] = repo().asignacion_activa(r["id_reporte"])
        f["pospuesto"] = sv.esta_pospuesto(repo(), r)
        f["decision"] = repo().ultima_decision(r["id_reporte"])
        f["dias"] = (r["fecha_cierre"] - r["fecha_creacion"]).days if r["fecha_cierre"] else None
        filas.append(f)
    por_prioridad = lambda f: (not f["es_urgente"], -(f["evaluacion"]["total"] if f["evaluacion"] else 0))
    grupos = {
        "decidir": sorted([f for f in filas if f["estado"] == "REVISADO" and not f["pospuesto"]], key=por_prioridad),
        "pospuestos": sorted([f for f in filas if f["pospuesto"]], key=por_prioridad),
        "ejecucion": sorted([f for f in filas if f["estado"] in ("ASIGNADO", "EN_PROCESO")], key=por_prioridad),
        "resueltos": sorted([f for f in filas if f["estado"] == "SOLUCIONADO"],
                            key=lambda f: f["fecha_cierre"], reverse=True),
    }
    vista = request.args.get("vista", "decidir")
    if vista not in grupos:
        vista = "decidir"
    return render_template("alcalde.html", stats=sv.estadisticas(repo()), grupos=grupos, vista=vista,
                           responsables=repo().responsables(),
                           departamentos={d["id_departamento"]: d["nombre"] for d in repo().departamentos()},
                           usuario_de=repo().usuario_por_id)


# ---------- detalle y acciones ----------
@web.route("/reportes/<int:id_reporte>")
def detalle(id_reporte):
    if not g.usuario:
        return redirect(url_for("web.login"))
    r = repo().obtener_reporte(id_reporte)
    if r is None or not sv.puede_ver(g.usuario, r):
        abort(404)
    es_staff = sv.tiene_rol(g.usuario, "PERSONAL", "ALCALDE", "ADMIN")
    return render_template(
        "detalle.html", r=_fila(r),
        eventos=repo().eventos(id_reporte, solo_visibles=not es_staff),
        asignacion=repo().asignacion_activa(id_reporte),
        acciones=sv.transiciones_disponibles(g.usuario, r),
        puede_asignar=sv.tiene_rol(g.usuario, "PERSONAL", "ALCALDE") and r["estado"] == "REVISADO",
        puede_priorizar=sv.tiene_rol(g.usuario, "PERSONAL") and r["estado"] not in sv.ESTADOS_FINALES,
        responsables=repo().responsables(),
        departamentos={d["id_departamento"]: d["nombre"] for d in repo().departamentos()},
        usuario_de=repo().usuario_por_id)


def _ejecutar(accion, id_reporte, mensaje_ok, volver=None):
    try:
        accion()
        flash(mensaje_ok, "success")
    except sv.ErrorNegocio as e:
        flash(str(e), "danger")
    except ConflictoVersion:
        flash("Otra persona modifico este reporte mientras lo editabas. Se recargo la informacion; revisa e intenta de nuevo.", "warning")
    except ValueError:
        flash("Datos invalidos en el formulario.", "danger")
    return redirect(volver or url_for("web.detalle", id_reporte=id_reporte))


@web.route("/reportes/<int:id_reporte>/estado", methods=["POST"])
def cambiar_estado(id_reporte):
    if not g.usuario:
        return redirect(url_for("web.login"))
    f = request.form
    return _ejecutar(lambda: sv.cambiar_estado(repo(), g.usuario, id_reporte, f.get("nuevo", ""),
                                               f.get("motivo", ""), int(f.get("version", ""))),
                     id_reporte, "Estado actualizado.")


@web.route("/reportes/<int:id_reporte>/asignar", methods=["POST"])
def asignar(id_reporte):
    if not g.usuario:
        return redirect(url_for("web.login"))
    f = request.form
    return _ejecutar(lambda: sv.asignar(repo(), g.usuario, id_reporte, int(f.get("id_responsable", "")),
                                        int(f.get("version", ""))),
                     id_reporte, "Reporte asignado.")


MENSAJE_DECISION = {"APROBADA": "Reporte aprobado y asignado.", "POSPUESTA": "Reporte pospuesto.",
                    "RECHAZADA": "Reporte rechazado."}


@web.route("/reportes/<int:id_reporte>/decision", methods=["POST"])
def decision(id_reporte):
    if not g.usuario:
        return redirect(url_for("web.login"))
    f = request.form
    tipo = f.get("decision", "")
    return _ejecutar(lambda: sv.decidir(repo(), g.usuario, id_reporte, tipo, f.get("motivo", ""),
                                        int(f.get("id_responsable") or 0), int(f.get("version", ""))),
                     id_reporte, MENSAJE_DECISION.get(tipo, "Decision registrada."),
                     volver=url_for("web.alcalde"))


@web.route("/reportes/<int:id_reporte>/prioridad", methods=["POST"])
def prioridad(id_reporte):
    if not g.usuario:
        return redirect(url_for("web.login"))
    return _ejecutar(lambda: sv.revisar_prioridad(repo(), g.usuario, id_reporte, request.form),
                     id_reporte, "Prioridad actualizada.")


@web.errorhandler(403)
def prohibido(_):
    return render_template("error.html", codigo=403, mensaje="No tienes permiso para ver esta pagina."), 403


@web.errorhandler(404)
def no_encontrado(_):
    return render_template("error.html", codigo=404, mensaje="No encontramos lo que buscas."), 404
