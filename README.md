# Hack It
This repository contains our autonomous AI buyer agent developed for the Tacto Challenge Industrial Agents Hackathon 2026. The core problem is building an agent that negotiates industrial procurement contracts via email with zero human intervention.

Operating within the Tacto Negotiation Arena track, our solution is evaluated on a multidimensional cost function. It must mathematically optimize unit price, payment terms, lead times, and quality against multi-agent supplier departments. Leveraging 33,000 rows of market data, our agent generates strategic arguments to capture the maximum possible savings score across the five-wave Live Final.

## Our approach: a purchasing committee that mirrors the supplier

The supplier is a team of departments (sales, finance, logistics, quality, management). So is our buyer:

| Agent | Lens | Proposes |
|---|---|---|
| **Finance** | payment terms, cash needs, whether we pay it on time | payment days as a currency |
| **Logistics** | capacity, lead times, volume, commitment | commitment, volume, delivery time |
| **Quality** | on-time delivery, defects, complaints | quality commitments, chargebacks |
| **Market** | cost indices, should-cost, rival offers | the price argument |
| **Director** | hears the four | picks package, evidence, pace and words |

**Code owns every number.** The committee only chooses from what code prepared:

- **Dossier** (`hackit/dossier.py`): who the supplier is and what it *needs* (13 needs such as cash,
  idle or full capacity, frozen prices, an approval ladder, stale costing), read from notes, letters,
  news and quote remarks, and live from its own e-mails.
- **Evidence locker** (`hackit/evidence.py`): arguments the arena can verify, each with its exact figure.
- **Iso-cost menu** (`hackit/menu.py`): every allowed package priced so that it costs us the same,
  ranked by what it is worth to the supplier. Free trades, such as paying a cash-short supplier
  sooner for a lower price.
- **Compass and response sensor** (`hackit/buyer.py`): how far we go, and a measure of how many euros
  the supplier concedes per euro we move. When it stops answering our moves, it is at its floor.
- **Minutes** (`hackit/minutes.py`): every decision and its reason, per case, in `logs/hackit/`.

A full explanation (in Spanish) is in [docs/EXPLICACION_AGENTE.md](docs/EXPLICACION_AGENTE.md).

## Run it

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and git.

```bash
uv sync --all-extras
cp .env.example .env            # fill in the values below; never commit .env
uv run arena data               # Tacto's data package (rerun Sunday after 08:00 for the Sunday release)
uv run python -m hackit dossiers                      # phase 0: supplier dossiers
uv run python -m hackit -v play case:D01              # one sandbox case (free)
uv run python -m hackit run --set practice --parallel 10
uv run python -m hackit run --set final --parallel 10 # the Live Final: start before 13:30, leave it running
```

Environment variables (`.env`, values not included):

| Variable | |
|---|---|
| `ARENA_URL` | `https://arena.tacto.ai` |
| `ARENA_API_KEY` | the team's arena key |
| `ARENA_DATA` | `data/package` |
| `ANTHROPIC_API_KEY` | Claude API key for the committee |
| `HACKIT_MODEL` | optional, default `claude-opus-5-5` |
| `HACKIT_EFFORT` | optional, default `low` |

`--no-model` plays with code only (no LLM).

## Repository layout

| Path | What |
|---|---|
| `hackit/` | **our agent** |
| `docs/` | how it works and how to pitch it |
| `arena_kit/`, `tests/`, `KIT_README.md` | Tacto's starter kit (MIT, see `LICENSE-kit`): API client, cost function, simulator |

Team: Burak, Andrey, Jose, Mateo.
