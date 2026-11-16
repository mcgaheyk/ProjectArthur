# ProjectArthur

A small always-on display that shows what Arthur's Litter-Robot 4 is doing: whether he is in
it, whether it is waiting or cleaning, how full the drawer is, and whether the data can be
trusted.

Hardware: a Ulanzi TC002 pixel clock (52x16) running AWTRIX NG 1.2.3, fed over the home network
by a Raspberry Pi 5.

- **What it shows and why:** [docs/states.md](docs/states.md)
- **Where the project stands:** [docs/STATUS.md](docs/STATUS.md)
- **Why things are the way they are:** [docs/DECISIONS.md](docs/DECISIONS.md)

## Quick start

```bash
uv sync
```

```bash
uv run pytest
```

Open the design studio (a simulated clock in the browser):

```bash
uv run python -m arthur.sim
```

Record what the real robot does (copy `.env.example` to `.env` and fill it in first):

```bash
uv run --env-file .env python scripts/spike_log.py
```
