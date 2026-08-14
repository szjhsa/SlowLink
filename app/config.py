import os

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
WEB_THREADS = int(os.getenv("WEB_THREADS", "2"))
LISTENER_WORKERS = int(os.getenv("LISTENER_WORKERS", "2"))
LOG_VERBOSE = os.getenv("LOG_VERBOSE", "0") == "1"
SESSION_PATH = os.getenv("SESSION_PATH", "/app/sessions/slowlink")

APP_VERSION = "1.1.7"
