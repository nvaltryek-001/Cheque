"""Blueprint registration (each blueprint is registered exactly once)."""
from __future__ import annotations


def register_blueprints(app) -> None:
    from . import auth_routes, cheque_routes, signer_routes, system_routes, verification_routes
    for module in (auth_routes, cheque_routes, signer_routes, verification_routes, system_routes):
        if module.bp.name not in app.blueprints:
            app.register_blueprint(module.bp)
