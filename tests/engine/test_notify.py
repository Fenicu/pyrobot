from app.engine.notify import LogNotifier


async def test_log_notifier_invalid_level_does_not_raise() -> None:
    """LogNotifier.notify must not raise even for invalid levels."""
    notifier = LogNotifier()
    await notifier.notify("bogus", "test_code", "test message")  # type: ignore[arg-type]
