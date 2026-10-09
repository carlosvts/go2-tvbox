"""Entrada da TV Box: `python -m sense`. O modo vem de SENSE_MODE no `.env`."""

from sense import boot

config = boot.start()
# Import tardio: cada modo só carrega as dependências que usa.
if config.role == "edge":
    from sense import edge

    edge.run(config)
else:
    from sense import thin

    thin.run(config)
