# Gemini HTTP 400: диагностика

1. В папке проекта откройте `.env` и проверьте `CQA_LLM_BASE_URL`, `CQA_LLM_MODEL`, `CQA_LLM_API_KEY`.
2. Запустите `check_gemini_windows.bat`. Новая версия сначала отправляет обычный запрос, затем запрос с `response_format: json_object`.
3. Если **STEP 1** возвращает HTTP 400, прочитайте точное сообщение `Gemini error`. Обычно оно указывает на недоступную модель, неподдерживаемые параметры или ограничения проекта. Проверьте модель в https://aistudio.google.com/ и доступный список https://ai.google.dev/gemini-api/docs/models.
4. Если STEP 1 OK, а **STEP 2** возвращает HTTP 400, установите `CQA_LLM_JSON_MODE=0` в `.env`, сохраните, перезапустите `run_windows.bat`, снова запустите проверку. Без принудительного JSON-режима проект всё равно требует и проверяет JSON-ответ из текста модели.
5. Не присылайте API-ключ. Если ошибка остаётся, пришлите **STEP 1 / STEP 2**, код HTTP и строку `Gemini error` (без секретов).

**Пример настроек:**

```ini
CQA_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
CQA_LLM_API_KEY=YOUR_PRIVATE_KEY
CQA_LLM_MODEL=gemini-2.5-flash
CQA_LLM_JSON_MODE=1
```

Важно: HTTP 400 не подтверждает, что ключ неверный. 401/403, 404, 429 означают другие классы проблем, но всегда ориентируйтесь на конкретный ответ сервера.
