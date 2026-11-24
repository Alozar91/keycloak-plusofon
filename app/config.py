import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- режим работы ---
    MOCK: bool = True  # True: работаем без Plusofon, звонок имитируется

    # --- сервис ---
    API_KEY: str = ""  # если задан, /api/verify/* требуют заголовок X-Api-Key
    DB_PATH: str = "data/app.db"
    CALL_TTL: int = 180  # время жизни верификации, сек
    RATE_LIMIT_N: int = 5  # максимум start() на один номер...
    RATE_LIMIT_WINDOW: int = 600  # ...за это количество секунд
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # --- публичный адрес (для hook_url в call-to-auth) ---
    PUBLIC_BASE_URL: str = "http://localhost:3000"

    # --- Plusofon ---
    PLUSOFON_API_BASE: str = "https://restapi.plusofon.ru"
    # access_token аккаунта Flash Call (получается методом POST /api/v1/flash-call
    # с основным токеном ЛК; нужен только при MOCK=0)
    PLUSOFON_FC_ACCOUNT_TOKEN: str = ""
    # allowlist IP, с которых принимаем вебхук (по доке — 185.54.48.1);
    # пустая строка = проверка выключена
    WEBHOOK_IPS: str = "185.54.48.1"

    # --- mock ---
    MOCK_CALL_NUMBER: str = "88003330999"

    # --- синхронизация пользователей в Keycloak ---
    KC_SYNC: bool = True  # создавать пользователя в realm после verified
    KC_BASE_URL: str = "http://keycloak:8080"  # из docker-сети
    KC_REALM: str = "plusofon-test"
    KC_ADMIN_USER: str = "admin"
    KC_ADMIN_PASSWORD: str = "admin"


settings = Settings()

if settings.CORS_ORIGINS:
    CORS_ORIGINS = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
else:
    CORS_ORIGINS = []

WEBHOOK_IPS = [ip.strip() for ip in settings.WEBHOOK_IPS.split(",") if ip.strip()]

os.makedirs(os.path.dirname(settings.DB_PATH) or ".", exist_ok=True)
