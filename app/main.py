from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routes.citas import router as citas_router
from app.routes.llamadas import router as llamadas_router
from app.routes.empresas import router as empresas_router
from app.routes.auth import router as auth_router
from app.routes.usuarios import router as usuarios_router

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(citas_router)
app.include_router(llamadas_router)
app.include_router(empresas_router)
app.include_router(auth_router)
app.include_router(usuarios_router)


@app.get("/")
def home():
    return {"status": "ok"}