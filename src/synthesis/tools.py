"""The one tool the model may call: an exact count from the database.

Retrieval hands the model a budgeted sample of records, which is enough to say
what happened but not how often it happened -- asked to count, the model counts
what it was shown and reports that as the total. Everything here exists so it can
ask the store instead. Nothing the model sends becomes SQL; it picks values for a
fixed set of filters (see TALLY_FIELDS).
"""

import json

from src.storage.db import tally

KINDS = ["commit", "pr", "event"]
DATES = ["happened", "opened", "merged"]
STATES = ["merged", "open", "closed"]
GROUPS = ["kind", "state", "ownership", "repo", "month", "branch"]

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "count_activity",
            "description": (
                "Count the user's stored activity exactly, and get the matching items. "
                "Call this for any question about how many, how often, or for a complete "
                "list -- the records in the prompt are only the best matches, so counting "
                "them gives a wrong total. Returns the exact count over everything stored. "
                "A question that asks for a total and a part of it at once (how many, and "
                "how many of those merged) is one call with group_by, not one call for the "
                "part: the total is then the whole count and the part is one of the groups."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": KINDS,
                        "description": "commit, pr (pull request) or event (calendar)",
                    },
                    "state": {
                        "type": "string",
                        "enum": STATES,
                        "description": "pull requests only: merged, open, or closed without merging",
                    },
                    "ownership": {
                        "type": "string",
                        "enum": ["own", "external"],
                        "description": (
                            "pull requests only, and only when the question itself draws the "
                            "distinction ('my own repos', 'other people's projects'). Leave it "
                            "out whenever a repository is named -- the repository already says "
                            "whose it is."
                        ),
                    },
                    "repo": {
                        "type": "string",
                        "description": "repository, either way round: second-brain or huggingface/peft",
                    },
                    "branch": {"type": "string", "description": "commits only: branch name"},
                    "since": {"type": "string", "description": "ISO date, inclusive, e.g. 2026-09-01"},
                    "until": {"type": "string", "description": "ISO date, inclusive"},
                    "dates": {
                        "type": "string",
                        "enum": DATES,
                        "description": (
                            "which time since/until are measured against. 'happened' (the default) "
                            "is when the item counts as having happened, which for a merged pull "
                            "request is when it merged. Use 'opened' for when a pull request was "
                            "opened, and 'merged' for when it merged."
                        ),
                    },
                    "group_by": {
                        "type": "string",
                        "enum": GROUPS,
                        "description": "also break the count down by this, e.g. state or month",
                    },
                },
            },
        },
    }
]

FILTER_NAMES = ("kind", "state", "ownership", "repo", "branch")



def describe(result: dict) -> str:
    """A count, written out for the answering prompt.

    JSON invited the model to treat the number as one more thing to check, and it
    would answer 29 to a result of 30 -- it had recounted the items. Plain
    sentences, with the number stated once and the items clearly subordinate to
    it, are followed.
    """
    if "error" in result:
        return f"A count could not be run: {result['error']}"

    counted = result.get("counted") or {}
    # said back in the same words the count was asked in, so an answer can tell one
    # count from another -- and see when the filters were not what it meant
    what = ", ".join(f"{name}={value}" for name, value in counted.items()) or "everything stored"
    lines = [f"Counted {what}: {result['total']}. This number is final -- state it as it is, do not re-derive it."]

    if result.get("groups"):
        breakdown = ", ".join(f"{name}: {count}" for name, count in result["groups"].items())
        lines.append(f"  broken down: {breakdown}")

    if result.get("items"):
        shown = "the most recent" if result.get("truncated") else "all"
        lines.append(f"  {shown} {result['listed']} of them, newest first:")
        lines.extend(f"    - {item['title']} ({item['date']})" for item in result["items"])
    return "\n".join(lines)


def run_tool(conn, name: str, arguments: str) -> str:
    """Execute a tool call and return what the model gets back, as JSON text."""
    if name != "count_activity":
        return json.dumps({"error": f"no such tool: {name}"})

    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        return json.dumps({"error": "arguments were not valid JSON"})
    if not isinstance(args, dict):
        return json.dumps({"error": "arguments must be an object"})

    result = tally(
        conn,
        filters={name: args.get(name) for name in FILTER_NAMES},
        since=args.get("since"),
        until=args.get("until"),
        group_by=args.get("group_by"),
        dates=args.get("dates") or "happened",
    )
    # the filters are echoed back so a second call with different ones can't be
    # confused for this one
    result["counted"] = {k: v for k, v in args.items() if v is not None}
    return json.dumps(result)
