<div align="center">

# Second Brain

**A local question-answering layer over your own activity — commits and calendar events — with voice input and spoken answers.**

<p>
  <img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white">
  <img alt="macOS" src="https://img.shields.io/badge/macOS-menubar-000000?logo=apple&logoColor=white">
  <img alt="Local" src="https://img.shields.io/badge/data-stays%20local-2ea44f">
  <img alt="LLM" src="https://img.shields.io/badge/llm-groq%20%C2%B7%20gpt--oss--120b-9d7bff">
</p>

</div>

---

## What it does

Second Brain ingests your GitHub commits (across repos and branches) and your
Google Calendar events, embeds them locally, and answers natural-language
questions about them. Hold <kbd>F9</kbd> and ask, or type into a local window.
Each answer comes back in two forms: a short one spoken aloud, and a written one
copied to the clipboard.

| Feature | Description |
|---|---|
| Voice input | Hold <kbd>F9</kbd> anywhere to record a question; the answer is spoken back |
| Typed input | A local window for when speech isn't practical — text only, no speech output |
| Retrieval | *"What was my latest commit on `<repo>`?"* resolves to the right commit, from the right branch |
| Dual output | A concise spoken answer plus a written one with dates and bullets |
| Branch awareness | Work on a topic branch is shown as `<repo> [fix/logstore…]`, so unmerged work is distinguishable |
| Local by default | The database, the embeddings and the speech-to-text all run on the machine |

## How it works

```mermaid
flowchart LR
    subgraph ingest["hourly sync"]
        GH["GitHub<br/>all repos · all branches"]
        CAL["Google Calendar"]
    end

    GH --> EMB
    CAL --> EMB
    EMB["MiniLM embeddings"] --> DB[("SQLite<br/>activity_records")]
    DB --> CL["entity linking<br/>cosine + 48h window"]

    subgraph query["a question"]
        MIC["F9 / mic"] --> W["faster-whisper"]
        TYPE["typed"]
    end

    W --> S
    TYPE --> S
    CL --> S
    S["search<br/>semantic + keyword + recency"] --> LLM["Groq<br/>gpt-oss-120b"]
    LLM --> SPK["spoken"]
    LLM --> WRI["written"]
```

Ranking blends three signals, since none of them is sufficient alone:

```
0.70 × semantic   +   0.15 × keyword   +   0.15 × recency
```

The recency term matters more than its weight suggests: without it, nothing in
the system encodes what *"latest"* means, and a question about the newest commit
can be answered from a months-old one that embeds slightly closer.

## Setup

**1. Install** with [uv](https://docs.astral.sh/uv/). The extras are optional:
`voice` for asking by microphone, `menubar` for the macOS menubar app.

```bash
uv sync --extra voice --extra menubar     # macOS
uv sync --extra voice                     # Windows, Linux
```

**2. Credentials** are stored in the OS keychain (Keychain on macOS, Credential
Manager on Windows, Secret Service on Linux), and each one is only needed by the
source that uses it:

```bash
uv run python -m src.config.env set GROQ_API_KEY    # console.groq.com, needed to answer
uv run python -m src.config.env set GITHUB_TOKEN    # repo scope, only if you want commits
uv run python -m src.config.env status              # what is set, and where it came from
```

A `.env` file (in the app folder or the repo root) still works as a fallback, and
`python -m src.config.env import-env` copies one into the keychain.

**3. Google Calendar** (optional) — download an OAuth **desktop app** client from
the Google Cloud Console and save it as `credentials.json` in the app folder. In
the same console, set the consent screen's publishing status to **In production**.

> While the app is in *Testing*, Google expires the refresh token every 7 days,
> which means re-authorising constantly. Publishing avoids this; the "unverified
> app" warning shown at consent is cosmetic.

**4. First run** — a browser opens once for Google consent:

```bash
uv run python -m src.ingest_all          # incremental
uv run python -m src.ingest_all --full   # re-fetch and re-embed everything
```

A source that isn't set up is skipped, not treated as an error.

### Where things live

| | |
|---|---|
| macOS | `~/Library/Application Support/SecondBrain` |
| Windows | `%LOCALAPPDATA%\SecondBrain` |
| Linux | `~/.local/share/SecondBrain` |

The database, the Google token and `credentials.json` live there.
`SECOND_BRAIN_HOME` overrides the location. Older checkouts kept these files in the
repo root; on first run they are copied over (never moved, never overwriting), so
delete the originals once you're happy.

### Speech output

`say` on macOS, SAPI (through PowerShell) on Windows, `espeak-ng` on Linux —
install it with your package manager, or answers stay text-only.

### Checks

```bash
uv run python -m unittest discover tests            # offline, no credentials needed
uv run python -m src.eval.retrieval --compare <run> # retrieval quality, see below
```

The retrieval check runs a fixed set of questions with known answers through the
app's ranking, without calling the LLM, so any change that could affect retrieval
(the embedding model, chunking, scoring weights) can be compared against an earlier
run. The questions and a frozen copy of the database stay in `<app folder>/eval`,
since they describe your own activity; see `src/eval/retrieval.py` for the format.

## Running without a terminal

A launch agent runs the menubar app so the repo doesn't need an open terminal.
By default it is started manually:

```bash
scripts/launch_agent.sh install     # set it up, don't start anything yet
scripts/launch_agent.sh start       # start it now
scripts/launch_agent.sh stop        # stop it, and it stays stopped
```

Add `--login` to start it at every login and restart it on crash, while still
respecting **Quit** from the menu (a clean exit stays exited until the next
login):

```bash
scripts/launch_agent.sh install --login
```

```bash
scripts/launch_agent.sh status      # installed? running? last exit code?
scripts/launch_agent.sh logs        # stdout/stderr from the agent
scripts/launch_agent.sh perms       # what to grant for the F9 hotkey
scripts/launch_agent.sh uninstall   # remove it entirely
```

> When run from a terminal, the <kbd>F9</kbd> hotkey inherits the terminal's
> Accessibility permission. Under launchd, the Python interpreter needs its own
> grant — `perms` prints the exact path. Until it is granted, recording still
> works from the menu.

## Interfaces

**Menubar** — the launcher and quick controls. The latest answer is rendered into
the dropdown, so reading it takes no extra click:

```
Open Window
Start Recording
● Speaking — click to stop
──────────────────────────────
heard: what did I ship on the parser?
- 21 Aug 2026 — Add a dashboard so the extension…
- 21 Aug 2026 — Notify on high-risk pages…
Copy for Docs
──────────────────────────────
Recent ▸    Show Transcript…
Sync Now (last: 4m ago)
```

**Window** — `http://127.0.0.1:8765`, served by the app itself using only the
standard library. Type a question with <kbd>⌘</kbd><kbd>↵</kbd>, watch live
state, or replay an earlier answer. Typed questions are never read aloud.

## Resource usage

Measured while idle:

| | |
|---|---|
| CPU | ~0.07%, and zero idle wake-ups |
| Memory | ~0.5–0.7 GB resident (before the speech model loads) |
| Network | 24 syncs a day |

Memory is the main cost, not the per-second menu refresh: the speech-to-text
model alone is ~540 MB, which is why it loads on the first question rather than
at startup, and why syncing is hourly.

## Layout

```
src/
├── ingestion/    github.py · calendar.py      what happened
├── embeddings/   provider.py                  MiniLM, shared instance
├── storage/      db.py · types.py             SQLite + the record shape
├── entities/     linking.py                   commits → coherent clusters
├── retrieval/    search.py                    semantic + keyword + recency
├── synthesis/    answer.py                    one call, two answers
├── capture/      recorder · transcriber · menubar_app
├── output/       speaker.py · speech.py       macOS say, spoken-form rewriting
├── ui/           server.py · index.html       the local window
├── config/       paths · env · google_auth    where files live, credentials
├── eval/         retrieval.py                 fixed questions, scored before and after
├── sync.py       incremental, per-source cursors
└── pipeline.py   question → answer
```

## Design notes

- **Two answers, not one.** Text that sounds right read aloud and text that reads
  well pasted into a document are different, so the model returns both in a
  single call rather than paying for a second round trip.
- **Speech gets its own pass.** Read aloud, `2026-08-22` becomes a
  twenty-million-something number and emoji are announced by name, so the spoken
  copy is rewritten first — markdown stripped, dates spelled out.
- **Sync overlaps by a week.** GitHub filters commits by *commit* date rather
  than push date, so work committed locally and pushed days later would
  otherwise land behind the cursor and never be ingested.
- **Branches are walked default-first**, deduped by SHA, so anything still tagged
  with a topic branch is work that hasn't landed.

## Known limitations

- Retrieval caps at 60 records, so a question spanning more occurrences than that
  returns a complete-looking but partial list.
- The local window has no login. It only answers requests from its own page
  (checked by `Host`, `Origin` and a JSON content type), so other websites can't
  drive it, but any local process can.
- Windows and Linux have no background service or hotkey yet: sync, the local
  window and speech work, but the menubar app and the launch agent are macOS only.
- Questions about a date (*"what did I ship on 21 Aug?"*) rank poorly, because
  embeddings barely see dates. The retrieval check tracks this.
