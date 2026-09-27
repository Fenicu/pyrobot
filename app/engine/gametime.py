from datetime import UTC, date, datetime, timedelta, timezone

# Москва без перехода на летнее время с 2014 года: фиксированный UTC+3.
MSK = timezone(timedelta(hours=3), "MSK")


def to_msk(moment: datetime) -> datetime:
    return moment.astimezone(MSK)


def from_msk_naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=MSK).astimezone(UTC)


def tasks_day(moment: datetime) -> date:
    """Игровой день ежедневных заданий: дата по Москве, сброс в 00:00 (не смузи в 03:00)."""
    return to_msk(moment).date()


def day_start(day: date) -> datetime:
    """Начало дня заданий `day` (00:00 по Москве) в UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=MSK).astimezone(UTC)
