from typing import Literal, Any 
from pydantic import BaseModel 

ActivitySource = Literal['github', 'calendar'] 

class ActivityRecord(BaseModel): 
    id: str 
    source: ActivitySource
    timestamp: str 
    title: str 
    body: str 
    url: str | None=None 
    raw: dict[str, Any] = {} 

    

