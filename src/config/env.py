import os 
from dotenv import load_dotenv 

load_dotenv() 

def require_env(key: str): 
    value = os.environ.get(key) 

    if not value: 
        raise ValueError(f"Missing required evn var: {key}")

    return value

GITHUB_TOKEN = require_env("GITHUB_TOKEN") 
API_KEY = require_env("GROQ_API_KEY")


