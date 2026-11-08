# Обновление Freedom CQA v1.1 → v1.2 (Groq/Qwen + сдача JSON 1.0)

## Важное

- **Не удаляйте `.env`**: там находится работающий ключ Groq и модель Qwen.
- **Не удаляйте `cqa.sqlite3`**: там ваши уже оценённые диалоги, результаты и апелляции.
- При обновлении **патчем** обе записи остаются на месте. Обновление из полного ZIP в пустую папку не перенесёт вашу историю автоматически.
- Не публикуйте `.env`, копии ключей и базу в GitHub.

## По шагам (Windows)

1. Закройте окно работающего `run_windows.bat` клавишами **Ctrl+C**. Дождитесь остановки процесса.
2. Скопируйте `C:\Freedom_CQA_Gemini\.env` и `C:\Freedom_CQA_Gemini\cqa.sqlite3` в отдельную резервную папку за пределами репозитория.
3. Распакуйте **Freedom_CQA_v1_2_Update_Patch.zip**.
4. Перенесите содержимое патча в `C:\Freedom_CQA_Gemini` с заменой одноимённых файлов. **Не заменяйте `.env` и `cqa.sqlite3`.**
5. Для очистки от старой Gemini-диагностики можете удалить через Проводник:
   - `check_gemini.py`
   - `check_gemini_windows.bat`
   - `list_gemini_models.py`
   - `GEMINI_WINDOWS_GUIDE.md`, `GEMINI_503_GUIDE_RU.md`, `CONNECTION_FIX_README_RU.md`, `TROUBLESHOOT_HTTP_400.md`.
   - `tests/test_gemini_setup.py` — устаревшие тесты удалённой диагностики.
   - по желанию: `_modify.py`, `_modify_ui.py` и каталог `screenshots/` из старой сборки.
   Это старые вспомогательные файлы. Новый `tests/test_fallback.py` входит в патч. **Не удаляйте `llm.py`**: в нём находится рабочее Groq/Qwen-подключение!
6. Запустите `run_windows.bat`, затем откройте `http://127.0.0.1:8000`, нажмите **Ctrl+F5**.
7. Появилась вкладка **«Сдача JSON»**. Укажите название команды, набор и версию чек-листа.
8. Нажмите **Сформировать DEV JSON**. Проверьте `total=65` и количество `error`. Если `error>0`, это не техническая ошибка экспорта: данные по этим ID пока не оценены или не прошли проверку.
9. Откройте на диске `C:\Freedom_CQA_Gemini\results\dev\results.json` и убедитесь, что он соответствует приложению к гайду.

## Проверка перед Git

```powershell
cd C:\Freedom_CQA_Gemini
.\.venv\Scripts\python.exe scripts\validate_results.py --kind dev --group DEV --checklist-id checklist-v1
git status
```

Если используете чек-лист версии 2, укажите `--checklist-id checklist-v2`. Версия должна совпадать с той, по которой **сохранены** оценки, иначе для несовпадающих записей будет честный `status=error`.

## Контрольный набор PROD

Когда организаторы сообщат способ доступа, импортируйте именно **10 новых чатов**. В настройках экспорта выберите их `dataset_group_id` (это может быть не буквальная строка PROD), сохранённую версию актуального чек-листа и сформируйте `results/prod/results.json`.

После генерации **оба файла должны попасть в открытый репозиторий**:

```powershell
git add .
git commit -m "Submit CQA results dev and prod"
git push
```

Откройте адрес своего GitHub-репозитория и проверьте наличие `results/dev/results.json` и `results/prod/results.json` **до стоп-кода**.
