"""Entrada da TV Box: `python -m sense`. O modo vem de SENSE_MODE no `.env`."""

from sense import boot

config = boot.start()
# Import tardio: o thin não carrega nada do Vosk.
if config.mode == "thin":
    from sense import thin

    thin.run(config)
else:
    from sense import edge

    edge.run(config)
