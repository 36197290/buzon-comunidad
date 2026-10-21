import re
import unittest

from app import create_app
from app.repositorio import ConflictoVersion
from app import servicios as sv


class Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()

    def token(self, ruta="/login"):
        html = self.c.get(ruta).get_data(as_text=True)
        return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)

    def entrar(self, correo, clave="demo1234"):
        t = self.token()
        return self.c.post("/login", data={"csrf": t, "correo": correo, "password": clave})

    def post(self, ruta, datos, desde):
        datos = dict(datos, csrf=self.token(desde))
        return self.c.post(ruta, data=datos, follow_redirects=True)


class TestAcceso(Base):
    def test_sin_sesion_redirige_a_login(self):
        self.assertEqual(self.c.get("/panel").status_code, 302)
        self.assertEqual(self.c.get("/reportes/1").status_code, 302)

    def test_login_incorrecto_y_correcto(self):
        r = self.entrar("cocode1@demo.gt", "mala")
        self.assertIn("incorrectos", r.get_data(as_text=True))
        r = self.entrar("cocode1@demo.gt")
        self.assertEqual(r.status_code, 302)

    def test_post_sin_csrf_se_rechaza(self):
        self.assertEqual(self.c.post("/login", data={"correo": "x", "password": "y"}).status_code, 400)

    def test_cocode_solo_ve_sus_reportes(self):
        self.entrar("cocode1@demo.gt")
        self.assertEqual(self.c.get("/reportes/1").status_code, 200)   # propio
        self.assertEqual(self.c.get("/reportes/2").status_code, 404)   # de otra comunidad
        self.assertEqual(self.c.get("/panel").status_code, 403)        # no es personal


class TestReportes(Base):
    FORM = {"titulo": "Poste caido", "id_categoria": "4", "personas_afectadas": "30",
            "descripcion": "Un poste bloquea la calle.", "ubicacion": "Calle 2",
            "gravedad": "moderada", "riesgo": "moderado"}

    def test_crear_reporte_genera_codigo_estado_y_prioridad(self):
        self.entrar("cocode1@demo.gt")
        r = self.post("/reportes/nuevo", self.FORM, "/reportes/nuevo")
        html = r.get_data(as_text=True)
        self.assertIn("RPT-", html)
        self.assertIn("Recibido", html)
        rep = self.app.repo.listar_reportes()[-1]
        self.assertEqual(rep["estado"], "RECIBIDO")
        ev = self.app.repo.ultima_evaluacion(rep["id_reporte"])
        self.assertEqual(ev["total"], ev["pts_gravedad"] + ev["pts_personas"] + ev["pts_riesgo"] + ev["pts_tiempo"])
        self.assertEqual(ev["nivel"], "BAJA" if ev["total"] < 40 else "MEDIA")

    def test_validaciones_de_creacion(self):
        self.entrar("cocode1@demo.gt")
        antes = len(self.app.repo.listar_reportes())
        for cambio in ({"personas_afectadas": "0"}, {"titulo": " "}, {"id_categoria": "99"}, {"gravedad": "x"}):
            r = self.c.post("/reportes/nuevo", data=dict(self.FORM, **cambio, csrf=self.token("/reportes/nuevo")))
            self.assertEqual(r.status_code, 400, cambio)
        self.assertEqual(len(self.app.repo.listar_reportes()), antes)


class TestReglasDeNegocio(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.app.repo
        self.personal = self.repo.usuario_por_correo("personal@demo.gt")
        self.alcalde = self.repo.usuario_por_correo("alcalde@demo.gt")
        self.cocode = self.repo.usuario_por_correo("cocode1@demo.gt")

    def test_transicion_invalida_se_rechaza(self):
        with self.assertRaises(sv.ErrorNegocio):
            sv.cambiar_estado(self.repo, self.personal, 3, "SOLUCIONADO", "x", 1)
        self.assertEqual(self.repo.obtener_reporte(3)["estado"], "RECIBIDO")

    def test_flujo_completo_hasta_solucionado_y_reapertura(self):
        r = 1
        sv.cambiar_estado(self.repo, self.personal, r, "REVISADO", "", 1)
        sv.asignar(self.repo, self.alcalde, r, 6, 2)                      # personal2 -> Agua y Saneamiento
        self.assertEqual(self.repo.asignacion_activa(r)["id_departamento"], 2)
        sv.cambiar_estado(self.repo, self.personal, r, "EN_PROCESO", "", 3)
        with self.assertRaises(sv.ErrorNegocio):                          # SOLUCIONADO exige descripcion
            sv.cambiar_estado(self.repo, self.personal, r, "SOLUCIONADO", "", 4)
        sv.cambiar_estado(self.repo, self.personal, r, "SOLUCIONADO", "Se reparo la tuberia", 4)
        rep = self.repo.obtener_reporte(r)
        self.assertEqual(rep["estado"], "SOLUCIONADO")
        self.assertIsNotNone(rep["fecha_cierre"])
        with self.assertRaises(sv.ErrorNegocio):                          # el personal no puede reabrir
            sv.cambiar_estado(self.repo, self.personal, r, "EN_PROCESO", "x", 5)
        sv.cambiar_estado(self.repo, self.alcalde, r, "EN_PROCESO", "Vuelve a fallar", 5)
        self.assertIsNone(self.repo.obtener_reporte(r)["fecha_cierre"])
        tipos = [e["tipo_evento"] for e in self.repo.eventos(r)]
        self.assertEqual(tipos[0], "CREADO")
        self.assertIn("REABIERTO", tipos)

    def test_conflicto_de_version(self):
        sv.cambiar_estado(self.repo, self.personal, 3, "REVISADO", "", 1)
        with self.assertRaises(ConflictoVersion):                         # version vieja
            sv.cambiar_estado(self.repo, self.personal, 3, "RECHAZADO", "dup", 1)

    def test_asignar_exige_estado_revisado(self):
        with self.assertRaises(sv.ErrorNegocio):
            sv.asignar(self.repo, self.personal, 3, 6, 1)

    def test_solo_el_creador_puede_retirar(self):
        otro = self.repo.usuario_por_correo("cocode2@demo.gt")
        with self.assertRaises(sv.ErrorNegocio):
            sv.cambiar_estado(self.repo, otro, 3, "RETIRADO", "x", 1)
        sv.cambiar_estado(self.repo, self.cocode, 3, "RETIRADO", "Ya no hace falta", 1)
        self.assertEqual(self.repo.obtener_reporte(3)["estado"], "RETIRADO")

    def test_revisar_prioridad_valida_rangos_y_motivo(self):
        malo = {"pts_gravedad": "36", "pts_personas": "1", "pts_riesgo": "1", "pts_tiempo": "1", "motivo_revision": "x"}
        with self.assertRaises(sv.ErrorNegocio):
            sv.revisar_prioridad(self.repo, self.personal, 3, malo)
        sv.revisar_prioridad(self.repo, self.personal, 3, dict(malo, pts_gravedad="30", motivo_revision="Es mas grave"))
        ev = self.repo.ultima_evaluacion(3)
        self.assertEqual((ev["total"], ev["nivel"], ev["es_sugerida"]), (33, "BAJA", False))
        self.assertFalse(self.repo.eventos(3)[-1]["visible_ciudadano"])   # evento interno

    def test_cocode_no_ve_eventos_internos(self):
        sv.revisar_prioridad(self.repo, self.personal, 3, {"pts_gravedad": "10", "pts_personas": "1",
                             "pts_riesgo": "1", "pts_tiempo": "1", "motivo_revision": "nota interna"})
        self.entrar("cocode1@demo.gt")
        self.assertNotIn("nota interna", self.c.get("/reportes/3").get_data(as_text=True))
        self.c.post("/logout", data={"csrf": self.token("/reportes")})
        self.entrar("personal@demo.gt")
        self.assertIn("nota interna", self.c.get("/reportes/3").get_data(as_text=True))

    def test_panel_ordena_urgentes_y_mayor_puntaje(self):
        self.entrar("alcalde@demo.gt")
        html = self.c.get("/panel").get_data(as_text=True)
        orden = [html.index(c) for c in ("RPT-2026-000001", "RPT-2026-000002", "RPT-2026-000003")]
        self.assertEqual(orden, sorted(orden))


class TestApartadoAlcalde(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.app.repo
        self.alcalde = self.repo.usuario_por_correo("alcalde@demo.gt")
        self.personal = self.repo.usuario_por_correo("personal@demo.gt")

    def test_estadisticas_son_coherentes(self):
        st = sv.estadisticas(self.repo)
        self.assertEqual(st["total"], sum(st["por_estado"].values()))
        self.assertEqual((st["por_decidir"], st["pospuestos"], st["resueltos"]), (1, 1, 1))
        self.assertGreater(st["dias_promedio"], 0)

    def test_aprobar_asigna_y_registra_la_decision(self):
        sv.decidir(self.repo, self.alcalde, 5, "APROBADA", "", 6, 2)
        self.assertEqual(self.repo.obtener_reporte(5)["estado"], "ASIGNADO")
        self.assertEqual(self.repo.asignacion_activa(5)["id_responsable"], 6)
        self.assertEqual(self.repo.ultima_decision(5)["tipo"], "APROBADA")

    def test_posponer_y_rechazar_exigen_motivo(self):
        for decision in ("POSPUESTA", "RECHAZADA"):
            with self.assertRaises(sv.ErrorNegocio):
                sv.decidir(self.repo, self.alcalde, 5, decision, "  ", 0, 2)
        self.assertEqual(self.repo.obtener_reporte(5)["estado"], "REVISADO")
        sv.decidir(self.repo, self.alcalde, 5, "POSPUESTA", "Sin presupuesto este mes", 0, 2)
        self.assertTrue(sv.esta_pospuesto(self.repo, self.repo.obtener_reporte(5)))
        self.assertEqual(self.repo.eventos(5)[-1]["tipo_evento"], "DECISION_ALCALDE")
        sv.decidir(self.repo, self.alcalde, 5, "RECHAZADA", "No procede", 0, 2)
        self.assertEqual(self.repo.obtener_reporte(5)["estado"], "RECHAZADO")

    def test_solo_el_alcalde_decide_y_solo_sobre_revisados(self):
        with self.assertRaises(sv.ErrorNegocio):
            sv.decidir(self.repo, self.personal, 5, "APROBADA", "", 6, 2)
        with self.assertRaises(sv.ErrorNegocio):               # el reporte 3 sigue en RECIBIDO
            sv.decidir(self.repo, self.alcalde, 3, "RECHAZADA", "x", 0, 1)

    def test_conflicto_de_version_al_decidir(self):
        with self.assertRaises(ConflictoVersion):
            sv.decidir(self.repo, self.alcalde, 5, "POSPUESTA", "x", 0, 99)

    def test_pagina_del_alcalde_y_obras_en_verde(self):
        self.entrar("alcalde@demo.gt")
        self.assertIn("Alumbrado", self.c.get("/alcalde").get_data(as_text=True))
        html = self.c.get("/alcalde?vista=resueltos").get_data(as_text=True)
        self.assertIn("estado-solucionado", html)
        self.assertIn("Fuga en tuberia", html)

    def test_decision_por_formulario_y_permisos(self):
        self.entrar("alcalde@demo.gt")
        r = self.post("/reportes/5/decision", {"decision": "POSPUESTA", "motivo": "Esperar presupuesto",
                                               "version": "2"}, "/alcalde")
        self.assertIn("Reporte pospuesto", r.get_data(as_text=True))
        self.c.post("/logout", data={"csrf": self.token("/alcalde")})
        self.entrar("personal@demo.gt")
        self.assertEqual(self.c.get("/alcalde").status_code, 403)
        r = self.post("/reportes/5/decision", {"decision": "APROBADA", "id_responsable": "6", "version": "2"}, "/panel")
        self.assertIn("Solo el alcalde", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
