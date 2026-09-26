from datetime import UTC, datetime, timedelta, timezone

# Москва без перехода на летнее время с 2014 года: фиксированный UTC+3.
MSK = timezone(timedelta(hours=3), "MSK")


def to_msk(moment: datetime) -> datetime:
    return moment.astimezone(MSK)


def from_msk_naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=MSK).astimezone(UTC)
