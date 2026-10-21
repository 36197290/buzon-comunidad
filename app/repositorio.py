"""Acceso a datos.

RepositorioMemoria guarda todo en listas de Python con los MISMOS nombres de
campos que las tablas del modelo (reporte, usuario, evaluacion_prioridad,
asignacion, evento_seguimiento...). Asi, en la etapa del puente basta con
escribir otra clase con los mismos metodos:

  - Oracle XE  : usuarios, catalogos, reportes, evaluaciones, asignaciones
                 y COLA_EVENTO (todo dentro de UNA transaccion)
  - PostgreSQL : EVENTO_SEGUIMIENTO (historial) y AUDITORIA

Los metodos que deben cambiar estan documentados en el README.
"""
import uuid
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

FINALES = {"SOLUCIONADO", "RECHAZADO", "RETIRADO"}


class ConflictoVersion(Exception):
    """Otro usuario modifico el reporte antes (RN-24). Equivale a UPDATE ... WHERE version = :v con 0 filas."""


class RepositorioMemoria:
    def __init__(self):
        self._reportes, self._evaluaciones, self._asignaciones, self._eventos = [], [], [], []
        self._decisiones = []
        self._comunidades = [
            {"id_comunidad": 1, "nombre": "Aldea El Progreso"},
            {"id_comunidad": 2, "nombre": "Caserio Las Flores"},
            {"id_comunidad": 3, "nombre": "Colonia Nueva Esperanza"},
        ]
        self._categorias = [
            {"id_categoria": i, "nombre": n} for i, n in enumerate(
                ["Agua potable", "Caminos y calles", "Salud", "Seguridad",
                 "Limpieza y ambiente", "Educacion"], start=1)
        ]
        self._departamentos = [
            {"id_departamento": 1, "nombre": "Obras Publicas"},
            {"id_departamento": 2, "nombre": "Agua y Saneamiento"},
            {"id_departamento": 3, "nombre": "Salud y Ambiente"},
        ]
        clave = generate_password_hash("demo1234")  # solo para la demo
        def u(i, nombre, correo, rol, com=None, dep=None):
            return {"id_usuario": i, "nombre": nombre, "correo": correo, "hash_contrasena": clave,
                    "activo": True, "roles": [rol], "id_comunidad": com, "id_departamento": dep}
        self._usuarios = [
            u(1, "Administrador Demo", "admin@demo.gt", "ADMIN"),
            u(2, "Alcalde Demo", "alcalde@demo.gt", "ALCALDE"),
            u(3, "Personal Obras Demo", "personal@demo.gt", "PERSONAL", dep=1),
            u(4, "COCODE El Progreso", "cocode1@demo.gt", "COCODE", com=1),
            u(5, "COCODE Las Flores", "cocode2@demo.gt", "COCODE", com=2),
            u(6, "Personal Agua Demo", "personal2@demo.gt", "PERSONAL", dep=2),
        ]
        self._sembrar()

    # ---------------- catalogos y usuarios ----------------
    def usuario_por_correo(self, correo):
        return next((x for x in self._usuarios if x["correo"] == correo.strip().lower()), None)

    def usuario_por_id(self, id_usuario):
        return next((x for x in self._usuarios if x["id_usuario"] == id_usuario), None)

    def responsables(self):
        """Personal activo que pertenece a un departamento (candidatos a responsable)."""
        return [x for x in self._usuarios if "PERSONAL" in x["roles"] and x["activo"] and x["id_departamento"]]

    def comunidades(self): return list(self._comunidades)
    def categorias(self): return list(self._categorias)
    def departamentos(self): return list(self._departamentos)

    def comunidad(self, i): return next((x for x in self._comunidades if x["id_comunidad"] == i), None)
    def categoria(self, i): return next((x for x in self._categorias if x["id_categoria"] == i), None)
    def departamento(self, i): return next((x for x in self._departamentos if x["id_departamento"] == i), None)

    # ---------------- reportes ----------------
    def crear_reporte(self, datos, evaluacion, fecha=None):
        """Oracle: INSERT en REPORTE (el trigger genera codigo, estado RECIBIDO y version)
        + INSERT en EVALUACION_PRIORIDAD + evento CREADO en COLA_EVENTO."""
        fecha = fecha or datetime.now()
        id_reporte = len(self._reportes) + 1
        reporte = dict(datos, id_reporte=id_reporte, codigo=f"RPT-{fecha.year}-{id_reporte:06d}",
                       estado="RECIBIDO", fecha_creacion=fecha, fecha_cierre=None, version=1)
        self._reportes.append(reporte)
        self._evaluaciones.append(dict(evaluacion, id_evaluacion=len(self._evaluaciones) + 1,
                                       id_reporte=id_reporte, fecha=fecha))
        self._evento(id_reporte, "CREADO", None, "RECIBIDO", datos["id_usuario"],
                     "Reporte registrado.", True, fecha)
        return reporte

    def obtener_reporte(self, id_reporte):
        return next((r for r in self._reportes if r["id_reporte"] == id_reporte), None)

    def listar_reportes(self, id_usuario=None, estado=None):
        return [r for r in self._reportes
                if (id_usuario is None or r["id_usuario"] == id_usuario)
                and (estado is None or r["estado"] == estado)]

    def cambiar_estado(self, id_reporte, nuevo, version_esperada, id_actor, observacion, tipo_evento="CAMBIO_ESTADO", fecha=None):
        """Oracle: UPDATE reporte SET id_estado=... WHERE id_reporte=:id AND version=:v
        (0 filas => ConflictoVersion) + evento en COLA_EVENTO."""
        r = self._con_version(id_reporte, version_esperada)
        anterior, r["estado"] = r["estado"], nuevo
        r["version"] += 1
        r["fecha_cierre"] = (fecha or datetime.now()) if nuevo in FINALES else None
        self._evento(id_reporte, tipo_evento, anterior, nuevo, id_actor, observacion, True, fecha)

    def asignar(self, id_reporte, id_departamento, id_responsable, id_asignador, version_esperada, fecha=None):
        """Oracle: cierra la ASIGNACION activa, inserta la nueva y pasa el reporte a ASIGNADO."""
        r = self._con_version(id_reporte, version_esperada)
        for a in self._asignaciones:
            if a["id_reporte"] == id_reporte and a["activa"]:
                a["activa"], a["fecha_fin"] = False, fecha or datetime.now()
        self._asignaciones.append({
            "id_asignacion": len(self._asignaciones) + 1, "id_reporte": id_reporte,
            "id_departamento": id_departamento, "id_responsable": id_responsable,
            "id_asignador": id_asignador, "fecha_inicio": fecha or datetime.now(), "fecha_fin": None, "activa": True})
        anterior, r["estado"] = r["estado"], "ASIGNADO"
        r["version"] += 1
        depto = self.departamento(id_departamento)["nombre"]
        resp = self.usuario_por_id(id_responsable)["nombre"]
        self._evento(id_reporte, "ASIGNADO", anterior, "ASIGNADO", id_asignador,
                     f"Asignado a {depto} (responsable: {resp}).", True, fecha)

    def asignacion_activa(self, id_reporte):
        return next((a for a in self._asignaciones if a["id_reporte"] == id_reporte and a["activa"]), None)

    # ---------------- decisiones del alcalde ----------------
    def registrar_decision(self, id_reporte, tipo, motivo, id_alcalde, version_esperada=None, fecha=None):
        """Oracle: INSERT en DECISION_ALCALDE (misma transaccion que el cambio de estado, si lo hay).
        Posponer no cambia el estado: genera el evento DECISION_ALCALDE."""
        if version_esperada is not None:
            self._con_version(id_reporte, version_esperada)
        fecha = fecha or datetime.now()
        self._decisiones.append({"id_decision": len(self._decisiones) + 1, "id_reporte": id_reporte,
                                 "id_alcalde": id_alcalde, "tipo": tipo, "motivo": motivo, "fecha": fecha})
        if tipo == "POSPUESTA":
            self._evento(id_reporte, "DECISION_ALCALDE", None, None, id_alcalde,
                         f"El alcalde pospuso el reporte. Motivo: {motivo}", True, fecha)

    def ultima_decision(self, id_reporte):
        ds = [d for d in self._decisiones if d["id_reporte"] == id_reporte]
        return max(ds, key=lambda d: (d["fecha"], d["id_decision"])) if ds else None

    # ---------------- prioridad ----------------
    def guardar_evaluacion(self, id_reporte, evaluacion):
        self._evaluaciones.append(dict(evaluacion, id_evaluacion=len(self._evaluaciones) + 1,
                                       id_reporte=id_reporte, fecha=datetime.now()))
        self._evento(id_reporte, "PRIORIDAD_EVALUADA", None, None, evaluacion["id_usuario"],
                     f"Prioridad revisada: {evaluacion['total']}/100 ({evaluacion['nivel']}). "
                     f"Motivo: {evaluacion['motivo_revision']}", False)

    def ultima_evaluacion(self, id_reporte):
        evs = [e for e in self._evaluaciones if e["id_reporte"] == id_reporte]
        return max(evs, key=lambda e: (e["fecha"], e["id_evaluacion"])) if evs else None

    # ---------------- historial (PostgreSQL en la version real) ----------------
    def eventos(self, id_reporte, solo_visibles=False):
        evs = [e for e in self._eventos if e["id_reporte"] == id_reporte
               and (e["visible_ciudadano"] or not solo_visibles)]
        return sorted(evs, key=lambda e: (e["fecha_evento"], str(e["id_evento"])))

    # ---------------- internos ----------------
    def _con_version(self, id_reporte, version_esperada):
        r = self.obtener_reporte(id_reporte)
        if r is None or r["version"] != version_esperada:
            raise ConflictoVersion()
        return r

    def _evento(self, id_reporte, tipo, ant, nue, actor, obs, visible, fecha=None):
        self._eventos.append({
            "id_evento": uuid.uuid4(), "id_reporte": id_reporte, "tipo_evento": tipo,
            "estado_anterior": ant, "estado_nuevo": nue, "id_usuario_actor": actor,
            "observacion": obs, "visible_ciudadano": visible, "fecha_evento": fecha or datetime.now()})

    def _sembrar(self):
        """Los tres reportes de ejemplo del script de Oracle (puntajes 94, 72 y 38)."""
        hoy = datetime.now()
        def ev(g, p, r, t):
            e = {"pts_gravedad": g, "pts_personas": p, "pts_riesgo": r, "pts_tiempo": t}
            e["total"] = sum(e.values())
            e.update(nivel="ALTA" if e["total"] >= 75 else "MEDIA" if e["total"] >= 40 else "BAJA",
                     es_sugerida=True, id_usuario=None, motivo_revision=None)
            return e
        base = {"id_comunidad": 1, "es_urgente": False}
        self.crear_reporte(dict(base, id_usuario=4, id_categoria=1, titulo="Falta de agua potable",
            descripcion="Hace dos semanas no llega agua a los hogares del sector norte.",
            ubicacion="Sector norte, junto a la escuela", personas_afectadas=350, es_urgente=True),
            ev(33, 24, 24, 13), hoy - timedelta(days=5))
        self.crear_reporte(dict(base, id_usuario=5, id_comunidad=2, id_categoria=2, titulo="Camino deteriorado",
            descripcion="El camino principal tiene baches y se inunda con la lluvia.",
            ubicacion="Camino principal hacia la carretera", personas_afectadas=120),
            ev(25, 18, 14, 15), hoy - timedelta(days=2))
        self.crear_reporte(dict(base, id_usuario=4, id_categoria=5, titulo="Limpieza de parque",
            descripcion="El parque central necesita limpieza y recoleccion de basura.",
            ubicacion="Parque central", personas_afectadas=40),
            ev(12, 10, 6, 10), hoy - timedelta(days=1))

        ahora = datetime.now()
        def dias(n): return ahora - timedelta(days=n)
        # 4: obra terminada (se ve en verde)
        self.crear_reporte(dict(base, id_usuario=5, id_comunidad=2, id_categoria=1, titulo="Fuga en tuberia de la escuela",
            descripcion="Una tuberia rota inunda el patio de la escuela.", ubicacion="Escuela de Las Flores",
            personas_afectadas=200), ev(28, 22, 20, 15), dias(12))
        self.cambiar_estado(4, "REVISADO", 1, 3, None, "REVISADO", dias(11))
        self.asignar(4, 2, 6, 2, 2, dias(10)); self.registrar_decision(4, "APROBADA", None, 2, None, dias(10))
        self.cambiar_estado(4, "EN_PROCESO", 3, 6, None, "CAMBIO_ESTADO", dias(8))
        self.cambiar_estado(4, "SOLUCIONADO", 4, 6, "Se reparo la tuberia y se verifico con la comunidad.", "SOLUCIONADO", dias(3))
        # 5: pendiente de decision del alcalde
        self.crear_reporte(dict(base, id_usuario=4, id_categoria=4, titulo="Alumbrado publico deficiente",
            descripcion="Varias lamparas llevan semanas apagadas y la calle queda a oscuras.",
            ubicacion="Calle principal, sector sur", personas_afectadas=180), ev(22, 18, 14, 6), dias(6))
        self.cambiar_estado(5, "REVISADO", 1, 3, None, "REVISADO", dias(5))
        # 6: en ejecucion
        self.crear_reporte(dict(base, id_usuario=5, id_comunidad=2, id_categoria=2, titulo="Puente peatonal danado",
            descripcion="Las tablas del puente estan rotas y es peligroso para los ninos.",
            ubicacion="Puente sobre la quebrada", personas_afectadas=90), ev(24, 18, 18, 12), dias(9))
        self.cambiar_estado(6, "REVISADO", 1, 3, None, "REVISADO", dias(8))
        self.asignar(6, 1, 3, 2, 2, dias(7)); self.registrar_decision(6, "APROBADA", None, 2, None, dias(7))
        self.cambiar_estado(6, "EN_PROCESO", 3, 3, None, "CAMBIO_ESTADO", dias(4))
        # 7: pospuesto por el alcalde
        self.crear_reporte(dict(base, id_usuario=4, id_categoria=6, titulo="Techo de la escuela con goteras",
            descripcion="Cuando llueve se mojan los salones y no se puede dar clases.",
            ubicacion="Escuela El Progreso", personas_afectadas=200), ev(20, 22, 10, 8), dias(7))
        self.cambiar_estado(7, "REVISADO", 1, 3, None, "REVISADO", dias(6))
        self.registrar_decision(7, "POSPUESTA", "Se espera la aprobacion del presupuesto del proximo trimestre.", 2, 2, dias(2))
