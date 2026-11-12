#!/bin/zsh
# Запуск планировщика агентов (main.py) из виртуального окружения проекта (.venv).
cd "$(dirname "$0")"
exec .venv/bin/python main.py "$@"
