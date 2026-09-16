# repo-evaluation-kit

Первая рабочая версия методики исследования репозиториев относительно цели.
Комплект помогает понять, что использовать, адаптировать или создавать самим,
какие кандидаты конкурируют, какие дополняют друг друга и что проверять дальше.
Для начала достаточно описания результата и ссылок.

## Первый запуск из этой папки

Открой каталог комплекта в coding-агенте и дай запрос:

> Прочитай .agents/skills/repo-evaluation/SKILL.md. Хочу получить локальный
> инструмент проверки правил оформления файлов, не писать сопоставление ignore-правил
> и разбор .editorconfig с нуля. Исследуй https://github.com/cpburnz/python-pathspec и
> https://github.com/editorconfig/editorconfig-core-py. Начни со статического
> обзора, сохрани отдельный запуск в evaluations/file-policy/first-real-run.

Для другого проекта замени цель, ссылки и каталог. Можно добавить ограничения,
глубину или разрешения; длинная анкета не нужна. Если skill уже виден в интерфейсе,
можно упомянуть `$repo-evaluation`. Явное чтение указанного SKILL.md работает как
инструкция даже без обнаружения skill в селекторе.

Основная [методика](.agents/skills/repo-evaluation/references/methodology.md)
понятна и агентам без поддержки skills. [START-AUDIT](prompts/START-AUDIT.md)
содержит готовую инструкцию запуска; [минимальный JSON](.agents/skills/repo-evaluation/assets/request.minimal.json)
и [форматы](.agents/skills/repo-evaluation/references/formats.md) — вариант для файлового входа.

## Результат исследования

Каждый запуск хранит вход и критерии, реестр версий, карточки, доказательства,
матрицу возможностей, общий отчёт, план эксперимента и `STATE.md`.
Начинай чтение с `REPORT.md`; ограничения находятся в начале отчёта.
Пример результата — [демонстрация вне исходной предметной области](examples/file-policy/2026-09-16-overview/REPORT.md).
Это первичный статический обзор двух библиотек, не глубокий аудит и не внедрение.

[Шаблоны](.agents/skills/repo-evaluation/assets/) задают форму будущего результата.
После копирования они остаются заготовками; выводы появляются только после
реального чтения источников и соответствующих проверок.

## Продолжение и смена цели

> CONTINUE-AUDIT. Прочитай .agents/skills/repo-evaluation/SKILL.md и STATE.md
> в evaluations/file-policy/first-real-run, продолжи с первого незакрытого шага.

Для изменения результата:

> REASSESS-GOAL. Используй тот же skill и каталог исследования. Новая цель: …
> Сохрани прежние решения и пересмотри роли кандидатов по новым критериям.

Готовые инструкции: [CONTINUE-AUDIT](prompts/CONTINUE-AUDIT.md),
[REASSESS-GOAL](prompts/REASSESS-GOAL.md). История чата не является единственным
носителем состояния; переход описан в [правилах продолжения](.agents/skills/repo-evaluation/references/continuation.md).

## Необязательная механика

Python 3.10+ и стандартная библиотека; установка пакетов не нужна.
Из корня комплекта (в Windows можно заменить `python` на рабочий `py -3`):

```sh
python .agents/skills/repo-evaluation/scripts/kit.py validate-request .agents/skills/repo-evaluation/assets/request.minimal.json
python .agents/skills/repo-evaluation/scripts/kit.py init --request .agents/skills/repo-evaluation/assets/request.minimal.json --project my-project --run first-overview
python .agents/skills/repo-evaluation/scripts/kit.py check-run evaluations/my-project/first-overview
python .agents/skills/repo-evaluation/scripts/kit.py check-kit .
python -m unittest discover -s tests -v
```

Вместо `--project/--run` можно указать `--output` с отдельным каталогом, в том
числе вне комплекта в пределах разрешений. Существующий каталог не перезаписывается.
Без Python агент создаёт файлы по тем же шаблонам вручную.
Валидатор проверяет структуру, ссылки и часть противоречий; достоверность
источников, смысл выводов и полезность требует содержательного ревью.

## Перенос

Копируй **весь** каталог `.agents/skills/repo-evaluation/` в
`.agents/skills/` другого проекта, сохраняя внутренние пути. Для явного чтения
можно хранить его в любом доступном каталоге и передать агенту путь к `SKILL.md`.
Все нужные ресурсы включены внутрь skill; старый аудит не требуется.
По [официальной документации OpenAI](https://learn.chatgpt.com/docs/build-skills)
(проверена 16.09.2026), skill содержит `SKILL.md` с `name`/`description`,
а Codex поддерживает локальные `.agents/skills`. Обнаружение в новой сессии
конкретного приложения здесь не проверялось; при отсутствии в селекторе используй
явное чтение. Глобальная установка и изменение настроек не выполнялись.

Минимальная поставка — только каталог skill. Для полного комплекта добавь README,
AGENTS, `.gitignore`, EXTRACTION-NOTES, IMPLEMENTATION-REPORT, `prompts/`,
`examples/` и `tests/` (кроме временных файлов/логов). Исходный образец,
`evaluations/` и скачанные кодовые базы не включать. `.gitignore` помогает
избежать случайного добавления, но не управляет составом произвольного ZIP:
для архива выбирай перечисленные пути, а не весь корень.

`1c-migration-audit/` — неизменяемый исторический материал, не часть переносимого
ядра. [EXTRACTION-NOTES.md](EXTRACTION-NOTES.md) объясняет происхождение правил;
для минимальной поставки этот исторический документ не нужен.

Результаты проверки и ограничения первой версии:
[IMPLEMENTATION-REPORT.md](IMPLEMENTATION-REPORT.md).
