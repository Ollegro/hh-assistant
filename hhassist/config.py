# -*- coding: utf-8 -*-
"""Конфигурация приложения: профиль соискателя, фильтры, лимиты, ключи LLM."""
import json
import os
from dataclasses import dataclass, field

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data")
PROFILE_PATH = os.path.join(BASE, "config", "profile.json")
ENV_PATH = os.path.join(BASE, ".env")

DEFAULT_PROFILE = {
    "name": "Олег Гуцев",
    "headline": "AI-инженер / промпт-инженер",
    "salary_from": 180000,
    "salary_to": 250000,
    "formats": ["удалённо", "гибрид"],
    "stop_words": ["продажи", "холодные звонки", "стажёр", "стажер"],
    "must_any": ["ai", "llm", "rag", "промпт", "агент", "python", "fastapi"],
    "target_roles": ["AI-инженер", "промпт-инженер", "LLM-инженер", "RAG-разработчик", "AI-агенты"],
    "seniority": "middle+",
    "queries": ["AI инженер", "промпт-инженер", "LLM инженер", "RAG разработчик", "AI агенты разработчик"],
    "work_history": [
        {
            "company": "фриланс, собственные проекты",
            "role": "AI-разработчик / Prompt-инженер",
            "period": "2024 — н.в.",
            "domain": ["RAG", "LLM API", "автоматизация процессов"],
        },
    ],
    "stack": {
        "must": ["python", "llm", "rag", "промпт", "prompt", "fastapi", "postgresql", "openai", "yandexgpt"],
        "nice": ["langchain", "pgvector", "faiss", "aiogram", "n8n", "albato", "docker", "ollama",
                 "embeddings", "агент"],
        "avoid_in_title": ["1с", "1c", "c++", "c#", "sap", "verilog", "продаж", "менеджер по продажам"],
    },
    "education": [
        "РЭУ им. Г.В. Плеханова, 2007 — экономика и управление на предприятии",
        "MBA Executive, AIBEc, 2007",
    ],
    "languages": {"русский": "родной", "английский": "B1"},
    "locations": {"city": "Москва", "area_id": 1, "relocation": False},
    "exclude_companies": [],
    "links": {
        "github": "https://github.com/Ollegro",
        "site": "https://aquacleantech.ru",
        "demo": "https://ollegro.github.io/room-planner-demo/",
        "telegram": "@lego_1",
        "email": "o.gutsev@mail.ru",
        "phone": "+7 903 841-70-00",
    },
    "facts": [
        "RAG-бот технической поддержки «Матвей» (AquaCleanTech, два канала входа: сайт aquacleantech.ru и мессенджер MAX): "
        "167 источников, 4293 чанка, FAISS, top_k = 6, ответы со ссылками, 828 автотестов, интерфейс на Next.js 15",
        "Ассистент «Знайка» в мессенджере MAX: алерты по критичным показателям, интеграция с Nightscout, "
        "маскирование персональных данных перед отправкой в LLM, fallback-сценарии",
        "Визуальный конвейер: генерация изображений (Flux-3) с контролем качества через перцептивные хеши и журнал аудита",
        "Прототип планировщика помещений на Three.js: 19 тестов, открытое демо",
    ],
    "gaps": [
        "fine-tuning моделей (обучение весов)",
        "Kubernetes и Go в продакшене",
        "B2B-продажи и аккаунтинг в IT",
    ],
}


@dataclass
class Settings:
    """Настройки, которые можно менять в .env."""
    user_agent: str = "hh-assistant/0.1 (o.gutsev@mail.ru)"
    daily_limit: int = 200
    delay_seconds: int = 0
    score_threshold: int = 55
    collect_limit: int = 20
    collect_pages: int = 1
    collect_queries: int = 0
    llm_provider: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_timeout: int = 90
    llm_retries: int = 2
    llm_json_mode: bool = False
    hh_client_id: str = ""
    hh_client_secret: str = ""
    hh_app_token: str = ""
    hh_source: str = "auto"
    hh_api_timeout: int = 30
    hh_api_retries: int = 3
    profile: dict = field(default_factory=dict)

    def has_app_token(self):
        """True, если задан токен приложения или пара client_id/secret."""
        return bool(self.hh_app_token or (self.hh_client_id and self.hh_client_secret))


def load_env(path=ENV_PATH):
    env = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_profile(path=PROFILE_PATH):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_PROFILE)
        merged.update(data)
        return merged
    return dict(DEFAULT_PROFILE)


def get_settings():
    env = load_env()
    s = Settings(
        user_agent=env.get("HH_USER_AGENT", Settings.user_agent),
        daily_limit=int(env.get("DAILY_LIMIT", 200)),
        delay_seconds=int(env.get("DELAY_SECONDS", 0)),
        score_threshold=int(env.get("SCORE_THRESHOLD", 55)),
        collect_limit=int(env.get("COLLECT_LIMIT", 20)),
        collect_pages=int(env.get("COLLECT_PAGES", 1)),
        collect_queries=int(env.get("COLLECT_QUERIES", 0)),
        llm_provider=env.get("LLM_PROVIDER", ""),
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=env.get("LLM_MODEL", ""),
        llm_base_url=env.get("LLM_BASE_URL", ""),
        llm_timeout=int(env.get("LLM_TIMEOUT", 90)),
        llm_retries=int(env.get("LLM_RETRIES", 2)),
        llm_json_mode=env.get("LLM_JSON_MODE", "").strip().lower() in ("1", "true", "yes", "on"),
        hh_client_id=env.get("HH_CLIENT_ID", ""),
        hh_client_secret=env.get("HH_CLIENT_SECRET", ""),
        hh_app_token=env.get("HH_APP_TOKEN", ""),
        hh_source=env.get("HH_SOURCE", "auto").strip().lower() or "auto",
        hh_api_timeout=int(env.get("HH_API_TIMEOUT", 30)),
        hh_api_retries=int(env.get("HH_API_RETRIES", 3)),
    )
    s.profile = load_profile()
    return s


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(BASE, "config"), exist_ok=True)
    os.makedirs(os.path.join(BASE, "out"), exist_ok=True)
