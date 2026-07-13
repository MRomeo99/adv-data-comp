from __future__ import annotations

from fastapi import FastAPI

from adv_data_comp.serve.routes import router

app = FastAPI(
    title="adv-data-comp",
    description="Universal data file comparison and anomaly detection API",
)
app.include_router(router)
