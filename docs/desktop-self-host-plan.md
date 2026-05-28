# Временный план Postbridge Desktop Self-Host

> Временный рабочий документ. Нужен для обсуждения архитектуры и этапов. Перед публичным релизом его нужно либо удалить, либо переписать как нормальную английскую документацию.

## Цель

Postbridge Desktop — это полноценная локальная self-host версия Postbridge для Windows и Linux.

Это не клиент к удаленному серверу и не лаунчер для VPS. Приложение должно запускать полный self-host стек на машине пользователя:

- web UI Postbridge;
- Core API;
- worker и scheduler;
- PostgreSQL с pgvector;
- Redis-совместимую очередь;
- локальное файловое хранилище.

Desktop должен давать пользователю весь self-host функционал локально: каналы, мосты, импорты, публикации, live sync, расписание, агенты, генерация медиа, бэкапы, обновления и диагностика.

## Позиционирование

Основное сообщение:

> Open-source desktop app для локального запуска Postbridge. Переносите, синхронизируйте и публикуйте контент между Telegram, MAX, VK, RSS и другими каналами. Данные остаются на вашем компьютере.

## Редакции продукта

### Desktop Self-Host

Для авторов, небольших команд и пользователей без DevOps-навыков.

- Устанавливается как обычное desktop-приложение.
- Запускает полный стек локально.
- Использует локальный PostgreSQL и Redis-совместимую очередь.
- Открывает локальный web UI внутри приложения.
- Может опционально обращаться к внешним сервисам, например Gitsell AI.

### Server Self-Host

Уже есть через Docker Compose.

- Лучше подходит для VPS и always-on сценариев.
- Остается серверным способом установки.
- Должен максимально шарить код, API и UI с Desktop.

### SaaS

Приватный hosted-слой.

- Биллинг, hosted onboarding, lifecycle notifications и региональная продуктовая логика остаются вне публичной Desktop-сборки.

## Пользовательские сценарии

### Первый запуск

1. Пользователь устанавливает Postbridge Desktop.
2. Приложение создает локальную папку данных.
3. Приложение инициализирует PostgreSQL.
4. Приложение инициализирует Redis-совместимую очередь.
5. Приложение применяет миграции БД.
6. Приложение запускает API, worker, scheduler и web UI.
7. Приложение открывает setup wizard.
8. Пользователь создает локального администратора или входит в single-user desktop mode.
9. Пользователь попадает в локальный workspace.

### Основной продукт

После setup пользователь может:

- управлять каналами;
- создавать мосты;
- запускать batch import;
- запускать live sync;
- планировать публикации;
- использовать agents и AI workflows, если они настроены;
- управлять credentials;
- смотреть jobs и logs;
- делать backup/export;
- обновлять локальный runtime.

## Архитектура

```text
Postbridge Desktop
  Tauri shell
    Runtime supervisor
    Settings and diagnostics UI
    Embedded webview
    Native installer/update hooks

  Bundled runtime
    web build
    core-api executable
    core-worker executable
    migration runner
    PostgreSQL + pgvector
    Redis-compatible server

  User data directory
    postgres data
    queue data
    uploads
    logs
    backups
    generated .env
```

## Предлагаемая структура в репозитории

```text
desktop/
  README.md
  package.json
  src/
    App.jsx
    runtimeApi.js
  src-tauri/
    Cargo.toml
    tauri.conf.json
    src/
      main.rs
      runtime/
        config.rs
        supervisor.rs
        process.rs
        ports.rs
        health.rs
        logs.rs
        backup.rs
        update.rs
  runtime/
    README.md
    manifests/
      windows-x64.json
      linux-x64.json
    bin/
      .gitkeep
  scripts/
    build-backend-runtime.sh
    build-web-runtime.sh
    package-postgres-runtime.sh
    package-desktop.sh
```

Большие бинарники runtime не нужно хранить в git. Их нужно собирать или скачивать на этапе release build.

## Компоненты runtime

### Web UI

- Собирается из `web/`.
- Открывается внутри Tauri webview.
- По умолчанию работает в self-host mode.
- Должен уметь запускаться в Desktop first-run mode.

### Core API

- Та же FastAPI app, что и в server self-host.
- Упаковывается как локальный executable или Python runtime bundle.
- Слушает только `127.0.0.1`.
- Использует локально сгенерированные secrets.

### Worker и scheduler

- Те же Celery tasks, что и в server self-host.
- На первом этапе worker и beat можно запускать одним процессом, как сейчас в compose.
- Supervisor должен рестартовать упавший worker и показывать ошибку в diagnostics UI.

### PostgreSQL + pgvector

- Нужен, потому что self-host функционал использует PostgreSQL и pgvector.
- Desktop должен поставлять platform-specific PostgreSQL runtime с доступным pgvector.
- Приложение отвечает за `initdb`, запуск, миграции, backup и restore.
- Postgres должен слушать только localhost и использовать сгенерированные credentials.

### Redis-совместимая очередь

Варианты:

1. Redis, если лицензирование и дистрибуция подходят.
2. Valkey как Redis-compatible runtime.
3. Другой embedded Redis-compatible сервис только если доказана совместимость с Celery.

Предварительная рекомендация: рассмотреть Valkey для Desktop packaging.

## Папка данных

Дефолтные пути:

- Windows: `%APPDATA%/Postbridge`
- Linux: `~/.local/share/postbridge`

Структура:

```text
Postbridge/
  config/
    desktop.env
    runtime.json
  data/
    postgres/
    queue/
    uploads/
  logs/
    api.log
    worker.log
    postgres.log
    queue.log
    desktop.log
  backups/
```

## Локальная безопасность

- Все сервисы слушают только `127.0.0.1`.
- Генерируется сильный `CORE_SERVICE_TOKEN`.
- Генерируется сильный credentials encryption key.
- Postgres и очередь не торчат наружу.
- Secrets по возможности хранятся в OS keychain.
- Если keychain недоступен, использовать зашифрованный локальный config.
- Нельзя логировать access tokens, bot tokens, session strings и encryption keys.

## Порты

Desktop должен иметь стабильные дефолты, но уметь обходить занятые порты.

Дефолты:

- API: `127.0.0.1:8820`
- Web: embedded webview или `127.0.0.1:8821`
- Postgres: `127.0.0.1:8822`
- Queue: `127.0.0.1:8823`

Если порт занят, supervisor выбирает свободный порт и обновляет generated config до запуска процессов.

## Startup sequence

1. Взять single-instance lock.
2. Загрузить или создать runtime config.
3. Разрешить порты.
4. Создать data directories.
5. Инициализировать Postgres, если нужно.
6. Запустить Postgres.
7. Запустить очередь.
8. Применить миграции.
9. Запустить API.
10. Дождаться API health.
11. Запустить worker и scheduler.
12. Открыть web UI.
13. Продолжить мониторинг child processes.

## Shutdown sequence

1. Заблокировать UI-команды, меняющие состояние runtime.
2. Мягко остановить worker.
3. Остановить API.
4. Остановить очередь.
5. Остановить Postgres безопасным fast shutdown.
6. Освободить lock.

При закрытии приложения можно спрашивать:

- оставить Postbridge работать в фоне;
- остановить все локальные сервисы.

Дефолт должен быть явным, без сюрпризов.

## Backup и restore

Backup должен быть видимым и простым.

Состав backup:

- PostgreSQL dump;
- uploads;
- config без machine-specific transient ports;
- version metadata.

Пользовательские действия:

- `Settings -> Backups -> Create backup`;
- `Settings -> Backups -> Restore backup`.

Перед обновлением runtime нужен автоматический backup.

## Обновления

Flow обновления:

1. Скачать новый desktop package/runtime manifest.
2. Сделать automatic backup.
3. Остановить runtime.
4. Заменить application/runtime files.
5. Запустить Postgres и очередь.
6. Применить миграции.
7. Запустить API и worker.
8. Проверить health.
9. Если startup упал, показать recovery UI или откат.

Версии нужно хранить отдельно:

- desktop shell version;
- core runtime version;
- database schema revision;
- Postgres runtime version.

## AI и внешние сервисы

Desktop local-first, но может опционально обращаться к внешним AI-сервисам.

Точки входа:

- post editor;
- генерация контент-плана;
- советы по росту канала;
- settings для agents/AI.

Правила:

- Desktop должен работать без внешних AI-сервисов.
- Вызовы внешних AI-сервисов требуют явного действия пользователя или включенной настройки.
- UI должен явно говорить, когда контент отправляется во внешний AI-сервис.

## Интеграции платформ

Desktop MVP должен приоритизировать flows, которым не нужен публичный inbound webhook.

### Telegram

Хорошо подходит для Desktop, если используем:

- user session flow;
- bot token flow;
- local polling, где нужно.

### MAX

Нужно отдельно подтвердить self-host-compatible connection flow.

Предпочтительно:

- token/manual setup;
- bot-based setup;
- localhost redirect только если провайдер это разрешает.

### VK

Поддержка должна оставаться, но не должна блокировать Desktop MVP, если OAuth callback требует дополнительной работы.

### RSS

Должен хорошо работать в Desktop, потому что не требует OAuth.

## Паритет с self-host

Desktop должен поддерживать self-host поверхность:

- channels;
- channel registry;
- bridges;
- batch import;
- live sync;
- publications;
- post scheduling;
- local credentials storage;
- job status;
- worker recovery;
- agent workflows, если настроены;
- local media storage;
- backup and restore;
- diagnostics and logs.

SaaS-only функции не входят:

- SaaS billing;
- SaaS lifecycle notifications;
- hosted workspace payments;
- private regional marketing integrations.

## Diagnostics UI

Нужен отдельный экран диагностики:

- API health;
- worker health;
- database status;
- queue status;
- текущие порты;
- текущая data directory;
- logs per process;
- last migration status;
- backup status;
- copy support bundle.

Support bundle должен редактировать secrets.

## Build и release pipeline

Артефакты:

- Windows `.msi` или `.exe`;
- Linux `.AppImage`, опционально `.deb`.

CI stages:

1. Собрать web.
2. Собрать backend runtime.
3. Упаковать PostgreSQL + pgvector runtime per platform.
4. Упаковать queue runtime per platform.
5. Собрать Tauri app.
6. Smoke test startup на каждой платформе.
7. Подписать артефакты, где возможно.
8. Опубликовать GitHub release.

## Milestones

### M0: Design and runtime contract

- Создать `desktop/` scaffold.
- Описать runtime manifest format.
- Описать data directory layout.
- Описать process supervisor contract.
- Описать platform runtime dependencies.

### M1: Tauri shell and mock runtime

- Tauri app запускается.
- UI показывает runtime status.
- Supervisor запускает mock child processes.
- Logs stream попадает в UI.
- Port detection работает.
- Single-instance lock работает.

### M2: Embedded web UI

- Собрать и положить web UI.
- Открыть Postbridge UI внутри Tauri.
- Принудительно включить self-host app mode.
- Добавить Desktop first-run route/state.

### M3: Local API runtime

- Упаковать API как local executable/runtime.
- Запускать API из Tauri.
- Healthcheck проходит.
- API слушает только localhost.

### M4: Local PostgreSQL + pgvector

- Упаковать platform-specific Postgres runtime.
- Упаковать pgvector.
- Запустить `initdb`.
- Старт/стоп Postgres из Tauri.
- Запускать `alembic upgrade head`.
- Проверять доступность vector extension.

### M5: Local queue and worker

- Упаковать Redis-compatible queue.
- Запускать очередь из Tauri.
- Запускать worker и beat.
- Выполнять простой background job.
- Показывать worker health в diagnostics.

### M6: Full self-host smoke

- Создать local workspace.
- Добавить channels.
- Создать bridge.
- Запустить import.
- Запустить publication.
- Проверить job status и recovery behavior.

### M7: Desktop setup and full-product onboarding

- First-run setup wizard.
- Local workspace creation.
- Platform connection checklist.
- Worker and database health checklist.
- Entry points into channels, bridges, imports, publications, agents, backups, and diagnostics.

### M8: Backup, restore, update

- Manual backup.
- Manual restore.
- Automatic backup before update.
- Runtime version tracking.
- Recovery UI для failed migrations/startup.

### M9: Public OSS release

- README и landing docs.
- GitHub release artifacts.
- Screenshots и demo video.
- Open-source positioning.
- Submission checklist для open-source каталогов и сообществ.

## Открытые вопросы

- Какую PostgreSQL distribution бандлить под каждую платформу?
- Как собирать и проверять pgvector для Windows/Linux?
- Используем Redis или Valkey?
- Worker и beat запускаем одним процессом или раздельными supervised processes?
- Какой MAX connection flow полностью работает local-first?
- Нужен ли mandatory local admin auth или single-user desktop mode без login?
- Какие внешние AI-сервисы показывать в Desktop и на каких экранах?

## Не цели первого публичного релиза

- Mobile self-host.
- Kubernetes.
- Multi-user LAN deployment.
- SaaS billing внутри Desktop.
- Public webhook hosting.
- Fully offline AI.

## Критерии успеха

- Нетехнический пользователь устанавливает Postbridge Desktop и проходит setup без терминала.
- Полный локальный стек переживает restart приложения.
- Основные self-host flows работают внутри Desktop без терминала.
- Все существующие self-host функции доступны после setup.
- Backup и restore работают до публичного продвижения.
- Logs и diagnostics достаточны, чтобы помогать пользователям удаленно без доступа к их машине.
