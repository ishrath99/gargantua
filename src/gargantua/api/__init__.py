"""HTTP API routers (auth, admin, /me, etc.).

Each module under this package exposes a FastAPI ``APIRouter`` (named
``router``) that ``gargantua.main`` mounts onto the app.  Routers stay free
of business logic — they translate HTTP <-> service-layer calls and own
the request/response Pydantic models.
"""
