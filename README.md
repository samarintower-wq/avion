# SkyWorld Simulator — starter project

Компактная стартовая версия веб-игры: `main.py`, `requirements.txt`, `index.html`, `static/style.css`, `static/app.js`, `render.yaml`.

## Что уже реализовано

- Адаптивный интерфейс для ПК и мобильных экранов.
- Регистрация и вход с никнеймом, email и паролем.
- MongoDB Atlas для профилей, выбранного самолёта, режима Земли и маршрута.
- Каталог из 10 типов самолётов.
- CesiumJS глобус и выбор координат кликом.
- Поиск места и координат.
- Сохранение точек вылета/прилёта и режима Земли.
- OpenWeather current weather и погодные тайлы (требуют API key и подходящий тариф).
- Groq описание местности.
- Cloudflare Workers AI генерация изображения.
- Базовая демонстрация движения маркера самолёта по маршруту.

## Чего пока нет

Это **рабочая стартовая основа, не готовый авиасимулятор**. Для реальной игры необходимо дополнительно:
- подключить проверенные лицензированные 3D-модели `.glb/.gltf` для всех 10 самолётов;
- сделать полноценную физику полёта, виртуальный джойстик и управление клавиатурой;
- добавить реальный маршрут с корректной геодезической интерполяцией и камеру сопровождения;
- протестировать Cesium/OpenWeather тарифы, производительность и ограничения;
- реализовать отдельный безопасный OAuth/token workflow для личных Cesium ion аккаунтов игроков.

### Cesium token safety

`CESIUM_ION_TOKEN` — **публичный клиентский токен для конкретного приложения**, не секретный master token. Создай отдельный token в Cesium ion с минимальными публичными scopes, обычно `assets:read` и при необходимости `geocode`, и ограничь Allowed URLs доменом Render. Не используй токен с `tokens:write` в браузере и не раздавай игрокам свой административный токен. Документация: https://cesium.com/learn/ion/cesium-ion-access-tokens/

Сохранение личного токена игрока через `/api/cesium-token` хранит его на сервере и не возвращает в `/api/me`; это **не** создаёт токен от имени игрока. Для этого лучше сделать отдельный Cesium ion OAuth-поток с согласием пользователя.

## Развёртывание на Render

1. Загрузить файлы в пустой репозиторий GitHub.
2. В Render создать **New → Web Service** и подключить репозиторий.
3. Runtime: Python.
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. В Environment добавить:

| Key | Value |
|---|---|
| `DATABASE_URL` | MongoDB Atlas connection string |
| `SECRET_KEY` | случайная длинная строка (не менее 32 символов) |
| `MONGODB_DB_NAME` | `skyworld` |
| `CESIUM_ION_TOKEN` | отдельный публичный токен Cesium для приложения |
| `OPENWEATHER_API_KEY` | OpenWeather API key |
| `GROQ_API_KEY` | Groq API key |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token с доступом к Workers AI |

7. Дождаться deploy и проверить `/health`.

`render.yaml` включён как пример Blueprint. Если создаёшь сервис вручную, используй команды выше.

## MongoDB Atlas

- Создай database user с минимальными нужными правами.
- Добавь IP access rule, подходящее для Render. Если нужен `0.0.0.0/0`, используй сильный пароль и минимальные права пользователя БД.
- Скопируй URI в Render как `DATABASE_URL`, не в GitHub.

## Примечания

- Секреты не должны находиться в HTML/JS или GitHub.
- OpenWeather tile layers могут требовать подходящий план/API-доступ.
- Cloudflare FLUX endpoint возвращает base64 изображения; расходы/лимиты зависят от аккаунта.
- В этой стартовой версии пользовательский Cesium token, если сохранён, не подменяет токен глобуса автоматически.
