#!/usr/bin/env bash
# Выкатка на apps (запускает CI по ssh): pull → миграции → up → ожидание /readyz.
# Каталог сервиса — DEPLOY_DIR (по умолчанию ~/pyrobot), рядом compose.yml и .env.
set -euo pipefail

cd "${DEPLOY_DIR:-$HOME/pyrobot}"
chmod 600 .env

docker compose pull pyrobot
# Миграция до перезапуска: при ошибке старый бот продолжает работать на старой схеме.
docker compose run --rm migrate
docker compose up -d --remove-orphans

if [ "${SKIP_READY:-false}" = "true" ]; then
    # Первый выкат: без входа в Telegram /readyz не станет 200, достаточно живости.
    for _ in $(seq 1 24); do
        if docker compose exec -T pyrobot python -m app.healthcheck /healthz; then
            echo "pyrobot alive (readiness check skipped)"
            exit 0
        fi
        sleep 5
    done
else
    for _ in $(seq 1 "${READY_ATTEMPTS:-60}"); do
        if docker compose exec -T pyrobot python -m app.healthcheck /readyz; then
            echo "pyrobot ready"
            docker image prune -f >/dev/null
            exit 0
        fi
        sleep 5
    done
fi

echo "pyrobot not ready" >&2
docker compose ps
docker compose logs --tail 100 pyrobot
exit 1
