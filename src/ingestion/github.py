from datetime import datetime 
from github import Github 
from src.config.env import GITHUB_TOKEN 
from src.storage.types import ActivityRecord 

def fetch_recent_commits(owner: str, repo: str, since: datetime): 
    gh = Github(GITHUB_TOKEN) 
    repository = gh.get_repo(f"{owner}/{repo}")
    commits = repository.get_commits(since=since) 

    records = [] 
    for commit in commits: 
        message = commit.commit.message 
        title = message.split("\n")[0] if message else message 

        records.append(ActivityRecord(
            id=f"github:commit:{commit.sha}", 
            source="github",  
            timestamp=commit.commit.author.date.isoformat(), 
            title=title, 
            body=message, 
            url=commit.html_url, 
            raw=commit.raw_data,
        ))

    return records