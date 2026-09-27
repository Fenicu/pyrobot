#!/usr/bin/env bash
# Печатает .env для compose.yml из переменных окружения, имена которых переданы аргументами.
# Значения — в литеральной форме Compose: в одинарных кавычках, кавычка внутри как \'. Так `$`
# в секрете не интерполируется. Обратный слэш перед кавычкой или в конце и переводы строк
# Compose так не прочитает — такие значения отклоняются (без вывода самого значения).
set -euo pipefail

for name in "$@"; do
    if [[ ! -v $name ]]; then
        echo "render-env: $name is not set" >&2
        exit 1
    fi
    value="${!name}"
    if [[ $value == *$'\n'* || $value == *$'\r'* ]]; then
        echo "render-env: $name contains a line break" >&2
        exit 1
    fi
    if [[ $value == *"\\'"* || $value == *\\ ]]; then
        echo "render-env: $name has a backslash before a quote or at the end" >&2
        exit 1
    fi
    printf "%s='%s'\n" "$name" "${value//\'/\\\'}"
done
