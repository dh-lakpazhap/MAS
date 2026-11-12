#!/bin/zsh
# Запуск Streamlit-панели ВСЕГДА из виртуального окружения проекта (.venv),
# чтобы не подхватывался системный streamlit без sumy/apscheduler.
cd "$(dirname "$0")"
exec .venv/bin/streamlit run streamlit_app.py "$@"
