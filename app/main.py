from fastapi import FastAPI

from app.database import engine, Base
from app.routes.citas import router as citas_router
from app.routes.llamadas import router as llamadas_router
from app.routes.empresas import router as empresas_router

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.include_router(citas_router)
app.include_router(llamadas_router)
app.include_router(empresas_router)


@app.get("/")
def home():
    return {"status": "ok"}