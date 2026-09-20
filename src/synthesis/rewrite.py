"""Turn a follow-up into a question that stands on its own.

Search has no memory: "how many of those merged?" embeds to nothing useful and
matches no keywords, so a follow-up would be answered from whatever that sentence
happened to rank. Rewriting it against the last few turns is what gives retrieval
something to find -- the model still sees the question you actually asked.
"""

from src.config.env import MissingCredential

PROMPT = """Rewrite the user's latest question so it can be understood on its own, without the conversation.

Rules:
- Replace every word that points back -- "those", "them", "it", "that repo", "the second one" -- with what it actually stands for, spelled out.
- Carry over every filter still in force from the earlier turns: what kind of thing, whose projects, which repository, which time range. A follow-up narrows the earlier question; it does not drop it.
- Keep the rest of the wording as it is.
- A question that already stands on its own -- it names its own subject and has no word pointing back -- is repeated exactly, even when it is about something else entirely. Changing the subject is allowed; the conversation is not a filter on everything that follows.
- Never answer the question, and never add anything the conversation does not say.

Example, a follow-up:
Asked: how many pull requests have I opened on other people's projects?
Answered: You have opened thirty-two.
Latest question: how many of those merged?
Rewrite: how many of the pull requests I opened on other people's projects have merged?

Example, a new subject:
Asked: how many pull requests have I opened on other people's projects?
Answered: You have opened thirty-two.
Latest question: what did I do yesterday?
Rewrite: what did I do yesterday?

Reply with the rewritten question and nothing else.

{turns}

Latest question: {question}
Rewrite:"""


def _as_text(turns) -> str:
    lines = []
    for turn in turns:
        lines.append(f"Asked: {turn.question}")
        lines.append(f"Answered: {turn.spoken}")
    return "\n".join(lines)


def standalone_question(question: str, turns, model: str = "openai/gpt-oss-120b") -> str:
    """The question as retrieval should see it. Falls back to the question as asked:
    a bad rewrite is worse than none, and so is failing the whole answer over one."""
    if not turns:
        return question

    from src.synthesis.answer import client

    try:
        response = client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT.format(turns=_as_text(turns), question=question)}],
            max_tokens=300,
            reasoning_effort="low",
            temperature=0,
        )
    except MissingCredential:
        raise
    except Exception as error:
        print(f"[rewrite] falling back to the question as asked: {error}")
        return question

    rewritten = (response.choices[0].message.content or "").strip().strip('"')
    # a model that answers the question instead of rewriting it, or says something
    # about the rewrite, is not to be trusted with the search text
    if not rewritten or len(rewritten) > 4 * len(question) + 200:
        return question
    return rewritten
