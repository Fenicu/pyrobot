#!/usr/bin/env bash
# Выкатка на apps (запускает CI по ssh): pull → дамп базы → миграции → up → ожидание /readyz
# (процесс готов: база отвечает, соединение блокировок хоста живо; вход аккаунтов в Telegram
# не проверяется).
# Каталог сервиса — DEPLOY_DIR (по умолчанию ~/pyrobot), рядом compose.yml и .env.
set -euo pipefail

cd "${DEPLOY_DIR:-$HOME/pyrobot}"
chmod 600 .env

docker compose pull pyrobot

# Дамп перед миграцией: она может быть необратимой. Не получился — выкат отменяется, старый бот
# работает дальше. Хранятся три последних pre-deploy-*.dump; pyrobot-*.dump старого сервиса не трогаем.
running=$(docker compose ps --status running --services)
if grep -qx postgres <<<"$running"; then
    umask 077
    mkdir -p backups
    dump="backups/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ).dump"
    if ! docker compose exec -T postgres pg_dump -U pyrobot --format=custom pyrobot > "$dump.tmp"; then
        rm -f "$dump.tmp"
        echo "database dump failed, deploy aborted" >&2
        exit 1
    fi
    mv "$dump.tmp" "$dump"
    echo "database dump written: $dump"
    ls -1 backups/pre-deploy-*.dump | head -n -3 | xargs -r rm -f --
else
    echo "postgres is not running yet, dump skipped"
fi

# Миграция до перезапуска: при ошибке старый бот продолжает работать на старой схеме.
docker compose run --rm migrate
docker compose up -d --remove-orphans

if [ "${SKIP_READY:-false}" = "true" ]; then
    # SKIP_READY=true: ждать только живость. Раньше это нужно было на первый выкат без входа в
    # Telegram; теперь /readyz от Telegram не зависит.
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
