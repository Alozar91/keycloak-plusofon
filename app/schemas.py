from pydantic import BaseModel, Field


class StartRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=32, description="номер пользователя в любом формате")
    session_ref: str | None = Field(
        default=None, max_length=128,
        description="опциональная ссылка на сессию (для SPI: authSessionId)",
    )


class StartResponse(BaseModel):
    verification_id: str
    call_number: str  # номер, который показать пользователю для звонка
    ttl: int
    mock: bool


class StatusResponse(BaseModel):
    verification_id: str
    status: str  # pending | verified | expired
    phone_masked: str
    ttl_left: int


class WebhookBody(BaseModel):
    model_config = {"extra": "ignore"}

    phone: str
    key: str


class SimulateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    verification_id: str | None = None
    phone: str | None = None
