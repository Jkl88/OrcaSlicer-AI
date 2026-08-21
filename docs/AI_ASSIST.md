# AI Assist (Gemini + Telegram + Webcam)

Форк [OrcaSlicer](https://github.com/SoftFever/OrcaSlicer) с модулем **AI Assist**.

## Возможности

1. **Анализ настроек** — Gemini смотрит текущие пресеты печати / пластика / принтера и предлагает правки.
2. **По описанию дефекта** — текст вроде «слоновая нога», «задирает углы» → конкретные изменения ключей Orca.
3. **Монитор печати** — периодический кадр с веб-камеры (прямой URL или через Moonraker) → Gemini Vision → при аномалии сообщение в Telegram (фото, описание, кнопка Stop).
4. **Telegram API base** — в настройках можно заменить `https://api.telegram.org` на свой прокси (proxyTelega, Cloudflare Worker и т.п.).

## Где в UI

- **Preferences → AI Assist** — ключи Gemini, Telegram (token, chat id, **API base URL**), webcam / Moonraker.
- **Help → AI Assist** — диалог анализа, правки по проблеме, тест камеры/TG, старт/стоп монитора.

## Настройка

| Параметр | Описание |
|----------|----------|
| Gemini API key | Ключ из Google AI Studio |
| Model | По умолчанию `gemini-2.0-flash` |
| Telegram Bot token / Chat ID | Бот и чат для алертов |
| Telegram API base URL | Например `https://your-proxy.example.com` без `/bot…` |
| Webcam source | `moonraker` или `direct` |
| Webcam snapshot URL | `http://host/webcam/?action=snapshot` |
| Moonraker URL | `http://192.168.x.x:7125` — для snapshot и Stop |
| Monitor interval | Секунды между кадрами (15–600) |

Секреты пишутся только в локальный `OrcaSlicer.conf` (секция `[ai_assist]`), не в пресеты и не в git.

## Сборка

Как у upstream OrcaSlicer: см. [How to build](https://www.orcaslicer.com/wiki/how_to_build) и скрипты `build_linux.sh` / `build_release*.bat`.

Исходники модуля: `src/slic3r/GUI/AIAssist/`.

## Telegram Stop

Кнопка **Stop print** шлёт `callback_data=orca_ai_stop`. Монитор делает long-poll `getUpdates` и вызывает Moonraker:

1. `POST /printer/print/pause`
2. `POST /printer/print/cancel`

Нужен корректный **Moonraker URL** (и API key, если включена авторизация).
