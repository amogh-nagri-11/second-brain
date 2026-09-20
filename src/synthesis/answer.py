import re
from dataclasses import dataclass
from datetime import datetime, timezone

from dateutil import parser as date_parser

from openai import OpenAI
from src.config.env import groq_api_key

_client: OpenAI | None = None


def client() -> OpenAI:
    """Built on first use, so a missing key surfaces as an answer, not an import
    error that stops the app from starting."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=groq_api_key(), base_url="https://api.groq.com/openai/v1")
    return _client

# full commit messages are long enough that a few dozen records blow the free-tier
# token budget; the subject line carries most of the signal anyway
MAX_BODY_CHARS = 400


def _trim(body: str) -> str:
    body = body.strip()
    if len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS].rstrip() + "..."


def _when(record: dict) -> str:
    """Local time, since that's what the answer should talk in; just the date for
    an all-day entry, which has no time to convert."""
    moment = date_parser.parse(record["timestamp"])
    if record.get("all_day"):
        return f"{moment:%Y-%m-%d}, all day"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().isoformat(timespec="minutes")


def _details(record: dict) -> str:
    """The fields worth reading that aren't already in the title."""
    fields = record.get("fields") or {}
    parts = []
    if fields.get("attendees"):
        parts.append("with " + ", ".join(fields["attendees"]))
    if fields.get("location"):
        parts.append("at " + fields["location"])
    return f" [{'; '.join(parts)}]" if parts else ""


def format_cluster_from_prompt(cluster: list[dict]) -> str:
    """Best-ranked records carry their text; the rest are one line each.

    Counting needs to see every match, and a title with a date is enough to be
    counted -- spending the token budget on bodies for all of them would mean
    showing far fewer records and getting the count wrong instead.
    """
    lines = []
    for r in cluster:
        header = f"- [{r['source']}] {r['title']}{_details(r)} ({_when(r)})"
        if r.get("detailed", True) and r["body"].strip():
            lines.append(f"{header}\n {_trim(r['body'])}")
        else:
            lines.append(header)
    return "\n".join(lines)

@dataclass
class Answer:
    """One answer in its two forms.

    What sounds right read aloud and what reads well pasted into a doc are not the
    same text -- speech wants short and unpunctuated by structure, a document wants
    dates, bullets and enough detail to stand on its own.
    """

    spoken: str
    written: str

    def __str__(self) -> str:
        return self.spoken


SPOKEN_RE = re.compile(r"SPOKEN:\s*(.*?)(?=\n\s*WRITTEN:|\Z)", re.S)
WRITTEN_RE = re.compile(r"WRITTEN:\s*(.*)", re.S)


def _split(reply: str) -> Answer:
    spoken = SPOKEN_RE.search(reply)
    written = WRITTEN_RE.search(reply)

    # if the model ignored the markers, the whole reply is better than nothing in
    # both slots
    if not spoken and not written:
        return Answer(spoken=reply.strip(), written=reply.strip())

    spoken_text = spoken.group(1).strip() if spoken else ""
    written_text = written.group(1).strip() if written else ""

    return Answer(
        spoken=spoken_text or written_text,
        written=written_text or spoken_text,
    )


def synthesize_answer(
    query: str,
    cluster: list[dict],
    model: str = 'openai/gpt-oss-120b',
    now: datetime | None = None,
) -> Answer:
    context = format_cluster_from_prompt(cluster)
    # without this "yesterday" and "this week" have nothing to be measured from, and
    # the model guesses a date out of its training data instead
    # the hour is formatted by hand: "%-I" is a glibc/BSD extension that raises
    # ValueError on Windows
    moment = now or datetime.now().astimezone()
    today = f"{moment:%A %d %b %Y}, {moment.hour % 12 or 12}:{moment:%M %p %Z}"

    prompt = f"""You are answering a question about the user's own recent activity, based only on the records below.

Answer twice, in two forms, using these exact markers and nothing else:

SPOKEN:
This is read aloud, so length is expensive. Two or three sentences, under about 60 words, conversational. Plain text only -- no markdown, no bullets, no headings. If the question asks how many or how often, count the matching records one at a time first, then say just the number and leave the dates to the written version. Every record is a separate occurrence: two that look almost identical are two, not one, and none may be skipped or merged.

WRITTEN:
The same answer formatted to be pasted into a document or an email. Open with one short line saying what it covers, then bullet points. Markdown is fine. Include the detail the spoken version had to leave out, and list every matching record rather than a sample, but stay factual and stick to the records -- no greeting, no sign-off, no invented context.

Write dates the way a person would in a document: "21 Aug 2026", or "21 Aug 2026, 3:40 pm" when the time matters. Never paste a raw timestamp like 2026-08-21T10:09:02+00:00.

It is now {today}. Resolve relative dates in the question ("yesterday", "last week") against that.

Commit titles read "<repo>: <subject>" when the commit is on the repo's default branch, and "<repo> [<branch>]: <subject>" when it is only on another branch -- that is, work that has not been merged yet. Use this when asked what has or hasn't landed.

Pull request titles read "<repo> #<number> (<state>): <subject>", where the state is merged, open or closed. "closed" means it was closed without merging, so it does not count as merged. A pull request on someone else's project is named "<owner>/<repo> #<number> (<state>, external)" and is dated when it merged; one of your own repos is named by the repo alone. Treat a commit and a pull request as separate things: a question about pull requests is only about the records that have a #number.

Records:
{context}

Question: {query}"""

    response = client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        # gpt-oss reasons before it answers out of this same budget. Asking for two
        # answers pushed it far enough that a whole 2000-token budget could go to
        # reasoning and come back with empty content, so the reasoning is capped
        # rather than the cap simply raised -- raising it alone would blow the
        # free tier's 8k tokens-per-minute limit instead.
        max_tokens=2500,
        reasoning_effort="low",
        # counting the same records twice should give the same answer twice
        temperature=0,
    )

    return _split(response.choices[0].message.content)