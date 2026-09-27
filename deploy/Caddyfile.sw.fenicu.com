# Блок для /home/fenicu/caddy/Caddyfile на web (10.10.40.3). Без Authelia: у админки своя
# авторизация (логин, сессия, CSRF) — решение спеки.
# access-log и robots-block — сниппеты, уже объявленные в начале того Caddyfile.
sw.fenicu.com {
	import access-log sw
	import robots-block
	# Самые большие запросы API — килобайты; всё, что больше, отбрасывается до бота.
	request_body {
		max_size 1MB
	}
	# Пробы живости и готовности наружу не отдаются: деплой и HEALTHCHECK проверяют их изнутри.
	@probes path /healthz /readyz
	respond @probes 404
	reverse_proxy http://10.10.40.20:8089 {
		# SSE (/api/v1/events): каждое событие уходит клиенту сразу, без буферизации.
		flush_interval -1
	}
}
