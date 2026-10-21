import os

from flask import Flask

from .repositorio import RepositorioMemoria


def create_app(repositorio=None):
    """Fabrica de la aplicacion.

    `repositorio` es el objeto que accede a los datos. Hoy se usa
    RepositorioMemoria (datos de demostracion); en la etapa del puente se
    reemplaza por uno que hable con Oracle XE y PostgreSQL, sin tocar rutas
    ni plantillas.
    """
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambiar-esta-clave-en-produccion")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.repo = repositorio or RepositorioMemoria()

    from .rutas import web
    app.register_blueprint(web)
    return app
