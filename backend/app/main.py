from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="IncidentLens API")


class HealthResponse(BaseModel):
    status: str
    service: str


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="incidentlens-api")
