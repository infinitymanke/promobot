# PromoBot (TikTok, без Playwright)

Промо-бот: оставляет комментарии на TikTok через внутренний API (без браузера).
Работает 24/7 на бесплатном Render, присылает статистику в Telegram,
держится в памяти автоматическим пингом (cron-job.org).

## Возможности
- Комментирует видео из ленты «Рекомендации» + свои видео (videos.txt).
- Тексты комментариев — по строкам из `comment.txt` (случайный выбор).
- Расписание (часы работы) в `config.json`.
- Не повторяется: история в `state.json`.
- Telegram-уведомления: старт, сводка за цикл, авто-перезапуск, предупреждение когда нужен вход.
- Безопасные паузы между комментариями (20–90 c), лимиты за проход.

## Локальный запуск
```bash
pip install -r requirements.txt
python selftest.py            # проверка подписей без интернета
python selftest.py --online   # живая проверка (нужны валидные куки)
python main.py                # запуск с Flask /ping + рабочим циклом
```

Для локального прогона в один проход (без Flask):
```bash
python worker.py
```

## Куки / вход
Куки лежат в `sessions/acc1.json` (формат Playwright storage_state). Обновляются локально:
```bash
pip install playwright pyperclip
playwright install chromium
python login_capture.py
```
Бот предупредит в Telegram, когда куки протухнут (обычно 1–2 месяца).

## Деплой на Render (бесплатно)
1. Приватный GitHub-репозиторий с содержимым этой папки (без `sessions/`, `state.json`, `logs/`).
2. `render.yaml` уже есть — на дашборде Render: «New → Blueprint», выберите репозиторий.
3. Задайте секреты/переменные:
   - `TELEGRAM_TOKEN` — токен бота от @BotFather
   - `TELEGRAM_CHAT_ID` — ваш chat_id
   - `TIKTOK_COOKIES_B64` — `base64` от содержимого `sessions/acc1.json`
4. Render запустит сервис. Пришли /ping из браузера — получите «ok».

## Не засыпать (keep-alive)
Render free засыпает без трафика. На cron-job.org создайте задание,
каждые 10 минут открывающее `https://<ваш-сервис>.onrender.com/ping`.

## Защита и риск
- Аккаунты — собственные; активность похожа на бота → риск бана.
- Не выкладывайте `sessions/`, `state.json`, `api.txt`, пароли в открытый репозиторий.
- Токен Telegram и куки храните только в переменных Render.