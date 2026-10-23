# -*- coding: utf-8 -*-
"""Единый клиент LLM (OpenAI-совместимый API) для скоринга и генерации писем.

Модуль берёт на себя всё сетевое взаимодействие с моделью:
- определяет, настроен ли доступ (ключ + модель + адрес);
- подставляет адрес провайдера, если `LLM_BASE_URL` не задан;
- повторяет запрос при 429/5xx и сетевых сбоях;
- достаёт JSON из ответа модели, даже если она добавила текст вокруг.

Ключ API никогда не попадает в сообщения об ошибках и в вывод приложения.
"""
import json
import re
import socket
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 90
DEFAULT_RETRIES = 2
RETRY_DELAY = 1.5
RETRY_CODES = (408, 409, 425, 429, 500, 502, 503, 504)

# Адреса по умолчанию: если указан LLM_PROVIDER, LLM_BASE_URL можно не заполнять.
PROVIDERS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
    "yandex": "https://llm.api.cloud.yandex.net/v1",
}


class LLMError(Exception):
    """Ошибка обращения к LLM: короткое описание, без ключа и прочих секретов."""


def resolve_base_url(settings):
    """Адрес API: из настроек, иначе — по имени провайдера, иначе пустая строка."""
    base = (getattr(settings, "llm_base_url", "") or "").strip()
    if base:
        return base.rstrip("/")
    provider = (getattr(settings, "llm_provider", "") or "").strip().lower()
    return PROVIDERS.get(provider, "")


def is_enabled(settings):
    """LLM готов к работе: есть ключ, модель и адрес (свой или по провайдеру)."""
    return bool((getattr(settings, "llm_api_key", "") or "").strip()
                and (getattr(settings, "llm_model", "") or "").strip()
                and resolve_base_url(settings))


def describe(settings):
    """Строка состояния для консоли и веб-интерфейса. Ключ не раскрывается."""
    if is_enabled(settings):
        return "LLM: %s @ %s" % (settings.llm_model, resolve_base_url(settings))
    missing = []
    if not (getattr(settings, "llm_api_key", "") or "").strip():
        missing.append("нет LLM_API_KEY")
    if not (getattr(settings, "llm_model", "") or "").strip():
        missing.append("нет LLM_MODEL")
    if not resolve_base_url(settings):
        missing.append("нет LLM_BASE_URL или LLM_PROVIDER")
    return "LLM не настроен (%s) — офлайн-режим" % ", ".join(missing)


def _settings_int(settings, name, default):
    try:
        value = int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def chat(settings, messages, temperature=0.3, json_mode=False, timeout=None, retries=None):
    """Запрос к модели. Возвращает текст ответа, при сбое поднимает LLMError.

    Повторы делаются только для временных ошибок (429/5xx, таймауты, обрыв сети).
    Ошибки ключа и адреса сообщаются сразу — с подсказкой, что проверить.
    """
    if not is_enabled(settings):
        raise LLMError("LLM не настроен: заполните LLM_API_KEY, LLM_MODEL и "
                       "LLM_BASE_URL (или LLM_PROVIDER) в файле .env")
    url = resolve_base_url(settings) + "/chat/completions"
    payload = {"model": settings.llm_model, "messages": messages, "temperature": temperature}
    if json_mode or getattr(settings, "llm_json_mode", False):
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": "Bearer " + settings.llm_api_key,
               "Content-Type": "application/json"}
    wait = _settings_int(settings, "llm_timeout", DEFAULT_TIMEOUT) if timeout is None else timeout
    attempts = _settings_int(settings, "llm_retries", DEFAULT_RETRIES) if retries is None else retries
    last = "неизвестная ошибка"
    for attempt in range(attempts + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=wait) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            return data["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LLMError("LLM отклонил ключ (HTTP %s): проверьте LLM_API_KEY" % exc.code)
            if exc.code == 404:
                raise LLMError("LLM не нашёл адрес или модель (HTTP 404): проверьте "
                               "LLM_BASE_URL и LLM_MODEL")
            if exc.code == 400:
                raise LLMError("LLM отклонил запрос (HTTP 400): проверьте название модели "
                               "LLM_MODEL у выбранного провайдера")
            last = "HTTP %s" % exc.code
            if exc.code not in RETRY_CODES:
                raise LLMError("LLM вернул ошибку %s" % exc.code)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            last = type(exc).__name__
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("неожиданный формат ответа LLM: %s" % type(exc).__name__)
        if attempt < attempts:
            time.sleep(RETRY_DELAY * (attempt + 1))
    raise LLMError("LLM недоступен после %d попыток (%s)" % (attempts + 1, last))


def extract_json(text):
    """Достаёт JSON-объект из ответа модели. None, если объекта нет."""
    if not text:
        return None
    body = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def chat_json(settings, messages, temperature=0.2):
    """Ответ модели в виде dict. Если JSON не разобрался — LLMError."""
    data = extract_json(chat(settings, messages, temperature=temperature, json_mode=True))
    if data is None:
        raise LLMError("модель вернула ответ без JSON")
    return data
