from sense.stats import STATS_INTERVAL_S, Stats


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_loga_os_contadores_so_depois_do_intervalo(caplog):
    caplog.set_level("INFO")
    clock = Clock()
    stats = Stats(clock)
    stats.counters["no_match"] += 2
    stats.counters["commands_sent"] += 1

    clock.now = STATS_INTERVAL_S - 1
    stats.maybe_log()
    assert caplog.text == ""

    clock.now = STATS_INTERVAL_S
    stats.maybe_log()
    assert "commands_sent=1 no_match=2" in caplog.text
    assert "cpu=" in caplog.text and "ram_pico=" in caplog.text

    caplog.clear()
    stats.maybe_log()  # o intervalo recomeçou
    assert caplog.text == ""
