# Usage Guide

## Шаг 1. Установка зависимостей

```bash
cd /Users/edwfa/Documents/antigravity/brave-mendeleev/multi_agent_archiv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Шаг 2. Настройка конфигурации

Отредактируйте файл `config.yaml`:
- Укажите **topics** – список тем, которые хотите отслеживать.
- Укажите **check_interval_seconds** – как часто проверять новые публикации (по умолчанию 3600 сек). 
- В разделе **email** укажите свои данные SMTP‑сервера, логин и пароль, а также получателя (`to`). Пример:
```yaml
email:
  smtp_server: smtp.gmail.com
  smtp_port: 587
  username: your.email@example.com
  password: your_password
  to: your.email@example.com
```

> **Важно:** Не храните реальные пароли в репозитории. Вы можете использовать переменные окружения и изменить код `notifier.py` для их подгрузки.

## Шаг 3. Запуск системы

```bash
source .venv/bin/activate
python main.py
```

Программа будет работать в режиме постоянного сервиса, проверять `archive.org` каждые `check_interval_seconds` и отправлять вам email‑сообщения с краткими резюме новых публикаций.

## Как остановить

Нажмите `Ctrl+C` в терминале, где запущен `main.py`.

---
Если захотите добавить другие каналы уведомления (Telegram, Slack и т.п.), реализуйте новый класс‑нотификатор, удовлетворяющий интерфейсу `Notifier.send(message: str)` и укажите его в `main.py`.
