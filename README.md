# Freedom Broker CQA — Submission Edition 1.2

УВАЖАЕМЫЕ ЖЮРИ! ПРОШУ ОБРАТИТЬ ВНИМАНИЕ НА .ZIP FILE FreedomCQA_FINAL. ТАМ НАХОДИТСЯ ВСЯ РАБОТА. Я ОЧЕНЬ СТАРАЛСЯ, ПРАВДА. ДЛЯ УДОСТВА Я УЖЕ ЗАКИНУЛ results.json НА МЕЙН ВЕТКУ.


Hackathon scoring system for RU/KZ/EN with an editable eight-criterion rubric, operator-only LLM grading, immutable original scores, appeals, evidence links, archive/delete, and organizer-format **JSON 1.0** export.



## Windows quick start / обновление

**Если v1.1 уже работает с Groq/Qwen**, используйте только обновление `Freedom_CQA_v1_2_Update_Patch.zip` и **сохраните существующие `.env` и `cqa.sqlite3`**. После установки удалите устаревший `tests/test_gemini_setup.py`, если убираете Gemini-диагностику. Не вставляйте API-ключ в Git.

1. Остановите `run_windows.bat` через `Ctrl+C`.
2. После остановки сервера скопируйте `.env` и `cqa.sqlite3` в надёжное место.
3. Распакуйте патч с заменой `app.py`, `static/` и добавлением `submission.py`, `scripts/` и тестов.
4. Запустите `run_windows.bat` и откройте `http://127.0.0.1:8000`. Нажмите `Ctrl+F5`.
5. Откройте новую вкладку **Сдача JSON**.

Для новой установки установите Python 3.11+, распакуйте полный архив, запустите `run_windows.bat`, затем настройте `.env` по примеру `.env.example`. Для внешнего Groq/OpenAI-compatible API укажите `CQA_LLM_PROVIDER=openai_compatible` и действительные URL, ключ и ID модели.

**Обратите внимание:** в полном архиве нет вашего старого `.env`, базы данных и оценок. Если нужна история, используйте патч.

## Official outputs: results.json schema 1.0

Согласно приложению организаторов нужно сдать два файла в **корне вашего открытого Git-репозитория**:

```text
results/
  dev/
    results.json     # 65 conversations: 60 chats + 5 calls
  prod/
    results.json     # 10 control chats released in the last hour
```

Сейчас папки `results/dev` и `results/prod` содержат только `.gitkeep`: **настоящие `results.json` создаются из локальной SQLite после оценки**, не подставляются примерные значения.

### Через интерфейс

- **Диалоги** → загрузить DEV JSON / указать официальную GET-ссылку.
- **Диалоги** → выбрать набор `DEV`, оценить новые. Подождать, пока batch job полностью завершится.
- **Сдача JSON** → указать **настоящее имя команды**, набор, версию чек-листа и нажать **Сформировать DEV JSON**.
- Проверить `total=65`, `error=0` (если есть `error`, это честная незавершённая оценка; исправьте где возможно), модель `external_api`, фактическое имя Qwen; сохранить и выполнить `git push`.
- В последний час аналогично импортировать 10 **новых** чатов, оценить их, сформировать **PROD JSON** и сделать **commit + push** до стоп-кода.

### Без браузера

```powershell
.\.venv\Scripts\python.exe scripts\create_results.py --kind dev --group DEV --team "Реальное имя команды" --checklist-id checklist-v1
.\.venv\Scripts\python.exe scripts\validate_results.py --kind dev --group DEV --checklist-id checklist-v1
```

Для контрольного набора укажите **его фактический `dataset_group_id`** (организаторы сообщат способ получения), `--kind prod` и соответствующую версию чек-листа:

```powershell
.\.venv\Scripts\python.exe scripts\create_results.py --kind prod --group CONTROL_ID --team "Реальное имя команды" --checklist-id checklist-v2
.\.venv\Scripts\python.exe scripts\validate_results.py --kind prod --group CONTROL_ID --checklist-id checklist-v2
```

`checklist-v2` здесь пример, используйте реальный ID из интерфейса. Система **не подменяет** оценки новой версии старыми: если диалог не оценён по выбранному чек-листу, сохраняет `status="error"` и причину.

### Конкретные правила формата

- Ровно 8 `criterion_id`: `contact`, `discovery`, `identification`, `consultation`, `empathy`, `dialogue`, `closure`, `critical`.
- `contact`, `empathy`, `critical` — только `true/false` **без `null`**; `discovery`, `identification`, `closure` допускают `null` (= N/A); `consultation` только `0/5/10/15/20/null`; `dialogue` только `0/4/7/10`.
- `evidence_turns` нумеруются **с 1 по ИСХОДНОМУ transcript**, включая client/bot/system. Внутри приложения они 0-based; конвертация происходит автоматически.
- `final_score` пересчитывается из оценок с перераспределением весов N/A, `critical_flag` отдельно не обнуляет балл.
- Если запись не оценена, сохраняются исходный ID, `status:"error"`, `criteria:[]`, `score:null`, `error:"..."` — ни один ID не исчезает.
- `model.name` извлекается из **фактически сохранённых AI-ответов**; при смешанной ручной и AI-оценке указывается `hybrid`.
- Архивные ID включены в официальный файл: архив влияет только на интерфейс.

## GitHub — ФИНАЛЬНАЯ СДАЧА

```powershell
git status
git add .
git commit -m "Submit Freedom CQA DEV and PROD evaluations"
git push
```

Если репозиторий ещё не подключён к remote, его нужно создать и настроить заранее, **не в последний час**. После `git push` откройте GitHub по публичной ссылке и проверьте наличие **обоих** `results/dev/results.json` и `results/prod/results.json`. Commit без push не считается сдачей. Никаких `.env`, базы или ключей в открытом репозитории быть не должно.

## Security and runtime

`.env`, `.env.*` и локальная SQLite-база в `.gitignore` (шаблон `.env.example` разрешён). Организаторский GET URL или токен доступа к контрольному набору не встраивать в JavaScript / публичный репозиторий. Сведения о реальных клиентах нельзя отправлять сторонним LLM без разрешения. UI построен на plain HTML/CSS/JS (можно переносить в Dynamics 365). API backend — FastAPI, БД — SQLite. Тесты полностью офлайн:

```powershell
run_tests_windows.bat
```

См. также [`LAST_HOUR_RUNBOOK_RU.md`](LAST_HOUR_RUNBOOK_RU.md).

## Последний час / новые контрольные наборы

Смотрите [`CONTROL_DATASET_URGENT_RU.md`](CONTROL_DATASET_URGENT_RU.md): оценивать и сдавать в PROD нужно только **FBroker (10)**. **Swap (3)** сохраняется отдельно и не оценивается. Для пакетного AI-анализа интерфейс требует явный выбор набора и запрещает `Swap`; сервер также блокирует пакетную оценку `Swap`.

Дополнительная автономная проверка итогового PROD (не вызывает LLM):

```powershell
py scripts/last_hour_preflight.py --dataset FBroker_10_Import.json --results results/prod/results.json
```
