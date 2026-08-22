from openai import OpenAI
from src.config.env import API_KEY

client = OpenAI(
    api_key=API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

# full commit messages are long enough that a few dozen records blow the free-tier
# token budget; the subject line carries most of the signal anyway
MAX_BODY_CHARS = 400


def _trim(body: str) -> str:
    body = body.strip()
    if len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS].rstrip() + "..."


def format_cluster_from_prompt(cluster: list[dict]) -> str:
    lines = []
    for r in cluster:
        lines.append(f"- [{r['source']}] {r['title']} ({r['timestamp']})\n {_trim(r['body'])}")
    return "\n".join(lines)

def synthesize_answer(query: str, cluster: list[dict], model: str = 'openai/gpt-oss-120b') -> str:
    context = format_cluster_from_prompt(cluster)

    prompt = f"""You are answering a question about the user's own recent activity, based only on the records below. Be concise and conversational, like a quick spoken summary — not a report.

This answer is read aloud, so length is expensive. Keep it to two or three sentences, under about 60 words. If the question asks how many or how often, count the matching records one at a time before you answer, then give just the number -- counting badly is worse than being long. If it asks you to list or name things, give one short line per item and nothing else. Never write headings or a closing summary.

Records:
{context}

Question: {query}

Answer:"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        # gpt-oss reasons before it answers, and those tokens come out of the same
        # budget -- too low a cap gets spent entirely on reasoning and returns empty
        # content rather than a short answer
        max_tokens=1500,
    )

    return response.choices[0].message.content