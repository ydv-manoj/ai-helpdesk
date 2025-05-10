import uuid
from datetime import datetime
from typing import Optional, Dict, Any

class HelpRequest:
    """
    Model representing a help request from an AI agent to a human supervisor.
    
    Properties:
    - id: Unique identifier for the help request
    - question: The question that the AI couldn't answer
    - caller_info: Information about the caller/customer
    - status: Current status (Pending/Resolved)
    - created_at: When the request was created
    - resolved_at: When the request was resolved (if applicable)
    - answer: The supervisor's answer (if resolved)
    """
    
    def __init__(self, question: str, caller_info: str):
        """
        Initialize a new help request.
        
        Args:
            question: The question that the AI couldn't answer
            caller_info: Information about the caller/customer
        """
        self.id = str(uuid.uuid4())[:8]  # Generate a shorter ID for human readability
        self.question = question.lower()  # Store in lowercase for easier matching
        self.caller_info = caller_info
        self.status = "Pending"
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.resolved_at = None
        self.answer = None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the help request to a dictionary for serialization.
        
        Returns:
            Dictionary representation of the help request
        """
        return {
            "id": self.id,
            "question": self.question,
            "caller_info": self.caller_info,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "answer": self.answer
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HelpRequest':
        """
        Create a HelpRequest instance from a dictionary.
        
        Args:
            data: Dictionary representation of a help request
        
        Returns:
            HelpRequest instance
        """
        instance = cls(
            question=data["question"],
            caller_info=data["caller_info"]
        )
        instance.id = data["id"]
        instance.status = data["status"]
        instance.created_at = data["created_at"]
        instance.resolved_at = data.get("resolved_at")
        instance.answer = data.get("answer")
        return instance
    
    def resolve(self, answer: str) -> None:
        """
        Mark the help request as resolved with the provided answer.
        
        Args:
            answer: The supervisor's answer to the question
        """
        self.status = "Resolved"
        self.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.answer = answer