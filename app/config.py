import os

def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
WEB_THREADS = int(os.getenv("WEB_THREADS", "2"))
LISTENER_WORKERS = int(os.getenv("LISTENER_WORKERS", "2"))
LOG_VERBOSE = os.getenv("LOG_VERBOSE", "0") == "1"
SESSION_PATH = os.getenv("SESSION_PATH", "/app/sessions/slowlink")
MESSAGE_MAX_AGE_SECONDS = _env_int("MESSAGE_MAX_AGE_SECONDS", 1800, 0)
FORWARD_SOURCE_MAX_AGE_SECONDS = _env_int(
    "FORWARD_SOURCE_MAX_AGE_SECONDS",
    7200,
    0,
)
QUEUE_MAX_WAIT_SECONDS = _env_int("QUEUE_MAX_WAIT_SECONDS", 300, 0)

APP_VERSION = "1.1"
