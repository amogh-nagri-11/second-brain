from litellm import completion 
from src.config.env import API_KEY 

def format_cluster_from_prompt(cluster: list[dict]) -> str: 
    lines=[] 
    for r in cluster: 
        lines.append(f"- [{r['source']}] {r['title']} ({r['timestamp']})\n {r['body']}") 
    return "\n".join(lines) 

def synthesize_answer(query: str, cluster: list[dict], model: str='groq/llama-3.3-70b-versatile') -> str: 
    context = format_cluster_from_prompt(cluster) 

    prompt = f"""You are answering a question about the user's own recent activity, based only on the records below. Be concise and conversational, like a quick spoken summary — not a report.

        Records:
        {context}

        Question: {query}

        Answer:"""

    response = completion(
        model=model, 
        messages=[{"role": "user", "content": prompt}], 
        max_tokens=300, 
        api_key=API_KEY
    )

    return response.choices[0].message.content