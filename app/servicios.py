"""Reglas de negocio (RN-xx del documento). No sabe nada de HTTP ni de bases
de datos: recibe un `repo` y aplica las reglas antes de pedirle que guarde."""


class ErrorNegocio(Exception):
    """Una regla de negocio no se cumple; el mensaje se muestra al usuario."""


ESTADOS = ["RECIBIDO", "REVISADO", "ASIGNADO", "EN_PROCESO", "SOLUCIONADO", "RECHAZADO", "RETIRADO"]
ESTADOS_FINALES = {"SOLUCIONADO", "RECHAZADO", "RETIRADO"}

# (origen, destino): (roles permitidos, requiere motivo)   -> RN-11, RN-12, RN-13
TRANSICIONES = {
    ("RECIBIDO", "REVISADO"): ({"PERSONAL"}, False),
    ("RECIBIDO", "RECHAZADO"): ({"PERSONAL"}, True),
    ("RECIBIDO", "RETIRADO"): ({"COCODE"}, True),
    ("REVISADO", "ASIGNADO"): ({"PERSONAL", "ALCALDE"}, False),
    ("REVISADO", "RECHAZADO"): ({"PERSONAL", "ALCALDE"}, True),
    ("REVISADO", "RETIRADO"): ({"COCODE"}, True),
    ("ASIGNADO", "EN_PROCESO"): ({"PERSONAL"}, False),
    ("EN_PROCESO", "SOLUCIONADO"): ({"PERSONAL"}, True),
    ("SOLUCIONADO", "EN_PROCESO"): ({"ALCALDE", "ADMIN"}, True),
}

TIPO_EVENTO = {"REVISADO": "REVISADO", "SOLUCIONADO": "SOLUCIONADO",
               "RECHAZADO": "RECHAZADO", "RETIRADO": "RETIRADO"}

# Prioridad (RN-14, RN-15): gravedad max 35, personas 25, riesgo 25, tiempo 15
GRAVEDAD = {"leve": 10, "moderada": 22, "grave": 33}
RIESGO = {"ninguno": 0, "moderado": 14, "alto": 24}
MAXIMOS = {"pts_gravedad": 35, "pts_personas": 25, "pts_riesgo": 25, "pts_tiempo": 15}


def tiene_rol(usuario, *roles):
    return bool(usuario) and any(r in usuario["roles"] for r in roles)


def nivel_prioridad(total):
    return "ALTA" if total >= 75 else "MEDIA" if total >= 40 else "BAJA"


def _pts_personas(n):
    return 5 if n <= 10 else 12 if n <= 50 else 18 if n <= 150 else 22 if n <= 400 else 25


def sugerir_prioridad(gravedad, personas, riesgo, dias_sin_resolver=0):
    """Puntaje sugerido por el sistema (RN-16)."""
    ev = {
        "pts_gravedad": GRAVEDAD[gravedad],
        "pts_personas": _pts_personas(personas),
        "pts_riesgo": RIESGO[riesgo],
        "pts_tiempo": min(15, max(0, dias_sin_resolver)),
    }
    ev["total"] = sum(ev.values())
    ev.update(nivel=nivel_prioridad(ev["total"]), es_sugerida=True, id_usuario=None, motivo_revision=None)
    return ev


def puede_ver(usuario, reporte):
    """RN-08: el COCODE solo ve sus reportes; alcalde/personal/admin ven todos."""
    if tiene_rol(usuario, "PERSONAL", "ALCALDE", "ADMIN"):
        return True
    return reporte["id_usuario"] == usuario["id_usuario"]


def transiciones_disponibles(usuario, reporte):
    """Destinos que este usuario puede aplicar ahora (ASIGNADO va por su propio formulario)."""
    salida = []
    for (origen, destino), (roles, requiere) in TRANSICIONES.items():
        if origen != reporte["estado"] or destino == "ASIGNADO":
            continue
        if not tiene_rol(usuario, *roles):
            continue
        if destino == "RETIRADO" and reporte["id_usuario"] != usuario["id_usuario"]:
            continue
        salida.append((destino, requiere))
    return salida


def _obtener_visible(repo, usuario, id_reporte):
    r = repo.obtener_reporte(id_reporte)
    if r is None or not puede_ver(usuario, r):
        raise ErrorNegocio("Reporte no encontrado.")
    return r


def crear_reporte(repo, usuario, form):
    if not tiene_rol(usuario, "COCODE") or not usuario.get("id_comunidad"):
        raise ErrorNegocio("Solo un COCODE asignado a una comunidad puede crear reportes.")
    titulo = form.get("titulo", "").strip()
    descripcion = form.get("descripcion", "").strip()
    ubicacion = form.get("ubicacion", "").strip()
    if not (titulo and descripcion and ubicacion):
        raise ErrorNegocio("Titulo, descripcion y ubicacion son obligatorios.")
    if len(titulo) > 150 or len(descripcion) > 2000 or len(ubicacion) > 300:
        raise ErrorNegocio("Alguno de los textos supera el largo permitido.")
    try:
        personas = int(form.get("personas_afectadas", ""))
        id_categoria = int(form.get("id_categoria", ""))
    except ValueError:
        raise ErrorNegocio("Personas afectadas y categoria deben ser numeros validos.")
    if personas < 1:
        raise ErrorNegocio("Las personas afectadas deben ser al menos 1.")
    if repo.categoria(id_categoria) is None:
        raise ErrorNegocio("La categoria elegida no existe.")
    gravedad, riesgo = form.get("gravedad"), form.get("riesgo")
    if gravedad not in GRAVEDAD or riesgo not in RIESGO:
        raise ErrorNegocio("Indica la gravedad y el riesgo del problema.")
    datos = {
        "id_usuario": usuario["id_usuario"], "id_comunidad": usuario["id_comunidad"],
        "id_categoria": id_categoria, "titulo": titulo, "descripcion": descripcion,
        "ubicacion": ubicacion, "personas_afectadas": personas,
        "es_urgente": form.get("es_urgente") == "on",
    }
    return repo.crear_reporte(datos, sugerir_prioridad(gravedad, personas, riesgo))


def cambiar_estado(repo, usuario, id_reporte, nuevo, motivo, version):
    r = _obtener_visible(repo, usuario, id_reporte)
    regla = TRANSICIONES.get((r["estado"], nuevo))
    if regla is None or nuevo == "ASIGNADO":
        raise ErrorNegocio("Transicion de estado no permitida.")
    roles, requiere_motivo = regla
    if not tiene_rol(usuario, *roles):
        raise ErrorNegocio("Tu rol no puede realizar este cambio.")
    if nuevo == "RETIRADO" and r["id_usuario"] != usuario["id_usuario"]:
        raise ErrorNegocio("Solo quien creo el reporte puede retirarlo.")
    motivo = (motivo or "").strip()
    if requiere_motivo and not motivo:
        raise ErrorNegocio("Este cambio requiere un motivo o descripcion.")
    tipo = "REABIERTO" if r["estado"] == "SOLUCIONADO" else TIPO_EVENTO.get(nuevo, "CAMBIO_ESTADO")
    repo.cambiar_estado(id_reporte, nuevo, version, usuario["id_usuario"], motivo or None, tipo)


def asignar(repo, usuario, id_reporte, id_responsable, version):
    """RN-19, RN-20: solo REVISADO; el departamento sale del responsable elegido."""
    r = _obtener_visible(repo, usuario, id_reporte)
    if not tiene_rol(usuario, "PERSONAL", "ALCALDE"):
        raise ErrorNegocio("Tu rol no puede asignar reportes.")
    if r["estado"] != "REVISADO":
        raise ErrorNegocio("Solo se puede asignar un reporte en estado REVISADO.")
    resp = repo.usuario_por_id(id_responsable)
    if not resp or not resp["activo"] or not tiene_rol(resp, "PERSONAL") or not resp.get("id_departamento"):
        raise ErrorNegocio("El responsable elegido no es valido.")
    repo.asignar(id_reporte, resp["id_departamento"], resp["id_usuario"], usuario["id_usuario"], version)


def revisar_prioridad(repo, usuario, id_reporte, form):
    """RN-16: el personal corrige el puntaje sugerido, con motivo."""
    r = _obtener_visible(repo, usuario, id_reporte)
    if not tiene_rol(usuario, "PERSONAL"):
        raise ErrorNegocio("Tu rol no puede revisar la prioridad.")
    if r["estado"] in ESTADOS_FINALES:
        raise ErrorNegocio("El reporte ya esta cerrado.")
    try:
        ev = {campo: int(form.get(campo, "")) for campo in MAXIMOS}
    except ValueError:
        raise ErrorNegocio("Los puntajes deben ser numeros enteros.")
    for campo, maximo in MAXIMOS.items():
        if not 0 <= ev[campo] <= maximo:
            raise ErrorNegocio(f"{campo} debe estar entre 0 y {maximo}.")
    motivo = form.get("motivo_revision", "").strip()
    if not motivo:
        raise ErrorNegocio("La revision de prioridad requiere un motivo.")
    ev["total"] = sum(ev.values())
    ev.update(nivel=nivel_prioridad(ev["total"]), es_sugerida=False,
              id_usuario=usuario["id_usuario"], motivo_revision=motivo)
    repo.guardar_evaluacion(id_reporte, ev)


# ---------------- Decisiones del alcalde (RN-29 a RN-31) ----------------
def esta_pospuesto(repo, reporte):
    d = repo.ultima_decision(reporte["id_reporte"])
    return reporte["estado"] == "REVISADO" and bool(d) and d["tipo"] == "POSPUESTA"


def decidir(repo, usuario, id_reporte, decision, motivo, id_responsable, version):
    """El alcalde decide sobre un reporte REVISADO: aprobar y asignar, posponer o rechazar.
    Posponer y rechazar exigen motivo. En la base real, el cambio de estado y el
    registro de la decision deben ir en UNA sola transaccion."""
    r = _obtener_visible(repo, usuario, id_reporte)
    if not tiene_rol(usuario, "ALCALDE"):
        raise ErrorNegocio("Solo el alcalde puede tomar esta decision.")
    if r["estado"] != "REVISADO":
        raise ErrorNegocio("Solo se decide sobre reportes en estado REVISADO.")
    motivo = (motivo or "").strip()
    if decision == "APROBADA":
        asignar(repo, usuario, id_reporte, id_responsable, version)
        repo.registrar_decision(id_reporte, "APROBADA", motivo or None, usuario["id_usuario"])
    elif decision in ("POSPUESTA", "RECHAZADA"):
        if not motivo:
            raise ErrorNegocio("Posponer o rechazar un reporte requiere un motivo.")
        if decision == "RECHAZADA":
            cambiar_estado(repo, usuario, id_reporte, "RECHAZADO", motivo, version)
            repo.registrar_decision(id_reporte, "RECHAZADA", motivo, usuario["id_usuario"])
        else:
            repo.registrar_decision(id_reporte, "POSPUESTA", motivo, usuario["id_usuario"], version)
    else:
        raise ErrorNegocio("Decision no valida.")


def estadisticas(repo):
    """Cifras para las tarjetas del alcalde (en la base real: consultas agregadas)."""
    reportes = repo.listar_reportes()
    por_estado = {e: 0 for e in ESTADOS}
    por_comunidad = {}
    for r in reportes:
        por_estado[r["estado"]] += 1
        n = repo.comunidad(r["id_comunidad"])["nombre"]
        por_comunidad[n] = por_comunidad.get(n, 0) + 1
    dias = [(r["fecha_cierre"] - r["fecha_creacion"]).total_seconds() / 86400
            for r in reportes if r["estado"] == "SOLUCIONADO" and r["fecha_cierre"]]
    total = len(reportes)
    return {
        "total": total, "por_estado": por_estado,
        "por_revisar": por_estado["RECIBIDO"],
        "por_decidir": sum(1 for r in reportes if r["estado"] == "REVISADO" and not esta_pospuesto(repo, r)),
        "pospuestos": sum(1 for r in reportes if esta_pospuesto(repo, r)),
        "en_ejecucion": por_estado["ASIGNADO"] + por_estado["EN_PROCESO"],
        "resueltos": por_estado["SOLUCIONADO"],
        "pct_resueltos": round(100 * por_estado["SOLUCIONADO"] / total) if total else 0,
        "dias_promedio": round(sum(dias) / len(dias), 1) if dias else None,
        "por_comunidad": sorted(por_comunidad.items(), key=lambda kv: -kv[1]),
    }
