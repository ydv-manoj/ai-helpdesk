from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, validator
import os
import json
import httpx
import asyncio
from datetime import datetime
import logging
from typing import Optional, Dict, List, Any, Union
import uuid

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Log to console
    ]
)
logger = logging.getLogger("frontdesk_api")

# Define our models
class CallRequest(BaseModel):
    question: str
    caller_info: str = "Unknown Caller"
    
    # Add input validation
    @validator('question')
    def question_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Question cannot be empty')
        return v.strip()
    
    @validator('caller_info')
    def caller_info_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Caller info cannot be empty')
        return v.strip()

class ResolveRequest(BaseModel):
    id: str
    answer: str
    
    @validator('id')
    def id_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Request ID cannot be empty')
        return v.strip()
    
    @validator('answer')
    def answer_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Answer cannot be empty')
        return v.strip()

class HelpRequest:
    def __init__(self, question: str, caller_info: str):
        self.id = str(uuid.uuid4())[:8]  # Shorter ID for human readability
        self.question = question.lower()  # Store in lowercase for easier matching
        self.caller_info = caller_info
        self.status = "Pending"
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.resolved_at = None
        self.answer = None

    def to_dict(self):
        return {
            "id": self.id,
            "question": self.question,
            "caller_info": self.caller_info,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "answer": self.answer
        }

# Create FastAPI app
app = FastAPI(title="Frontdesk Assistant API")

# Configure CORS to allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

# Create directories if they don't exist
os.makedirs("static", exist_ok=True)

# Serve static files (HTML, CSS, JS) for the frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

# Notification service URL
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:5002")

# Static knowledge base - our agent's baseline knowledge
knowledge_base = {
    "what are your salon hours?": "We are open from 9 AM to 7 PM, Monday to Saturday.",
    "do you offer hair coloring?": "Yes, we offer a full range of hair coloring services!",
    "do you take walk-ins?": "Yes, we accept walk-ins, but appointments are recommended for minimal wait time.",
    "where are you located?": "We're located at 123 Main Street, downtown.",
    "how much does a haircut cost?": "Haircuts start at $45 for short hair and $65 for long hair."
}

# In-memory storage (would be replaced by a database in production)
help_requests = []

# File paths for persistence
HELP_REQUESTS_FILE = "help_requests.json"
KNOWLEDGE_BASE_FILE = "knowledge_base.json"

# Helper functions for data persistence
def load_help_requests():
    """Load help requests from file"""
    if os.path.exists(HELP_REQUESTS_FILE):
        try:
            with open(HELP_REQUESTS_FILE, 'r') as f:
                data = json.load(f)
                
                # Handle different formats
                if isinstance(data, list):
                    # Already in list format, filter out non-dictionaries
                    return [req for req in data if isinstance(req, dict)]
                elif isinstance(data, dict):
                    # Convert dictionary of requests to list
                    result = []
                    for key, value in data.items():
                        if isinstance(value, dict):
                            # If missing ID, use the key
                            if 'id' not in value:
                                value['id'] = key
                            result.append(value)
                    return result
                else:
                    logger.error(f"Invalid data format in {HELP_REQUESTS_FILE}")
                    return []
        except Exception as e:
            logger.error(f"Error loading help requests: {e}")
    return []

def save_help_requests(requests):
    """Save help requests to file"""
    try:
        # Ensure we're saving a list of dictionaries
        valid_requests = []
        for req in requests:
            if isinstance(req, dict):
                # Make a copy to avoid modifying the original
                request_copy = req.copy()
                
                # Ensure required fields for dashboard
                if 'status' not in request_copy:
                    request_copy['status'] = "Pending"
                elif request_copy['status'] == "pending":
                    request_copy['status'] = "Pending"
                
                if 'created_at' not in request_copy:
                    request_copy['created_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                valid_requests.append(request_copy)
            else:
                logger.warning(f"Skipping invalid request during save: {req}")
        
        with open(HELP_REQUESTS_FILE, 'w') as f:
            json.dump(valid_requests, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving help requests: {e}")


def load_dynamic_knowledge():
    """Load dynamic learned knowledge base"""
    if os.path.exists(KNOWLEDGE_BASE_FILE):
        try:
            with open(KNOWLEDGE_BASE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading knowledge base: {e}")
    return {}

def save_knowledge_base(knowledge):
    """Save dynamic learned knowledge base"""
    try:
        with open(KNOWLEDGE_BASE_FILE, 'w') as f:
            json.dump(knowledge, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving knowledge base: {e}")

# Notification function
async def send_notification(endpoint, data):
    """Send notification to the notification service"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{NOTIFICATION_SERVICE_URL}{endpoint}",
                json=data,
                timeout=10.0
            )
            if response.status_code != 200:
                logger.error(f"Error sending notification: {response.status_code} - {response.text}")
                return False
            logger.info(f"Notification sent successfully to {endpoint}")
            return True
    except Exception as e:
        logger.error(f"Exception sending notification: {e}")
        return False

# Initialize data from files if they exist
help_requests = load_help_requests()
logger.info(f"Loaded {len(help_requests)} help requests from file")

# Custom exception handler for validation errors
@app.exception_handler(422)
async def validation_exception_handler(request: Request, exc: Any):
    details = []
    if hasattr(exc, 'errors') and isinstance(exc.errors(), list):
        for error in exc.errors():
            details.append({
                "field": error.get("loc", ["unknown"])[-1],
                "message": error.get("msg", "Unknown error")
            })
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Validation Error",
            "details": details,
            "message": "Please check your input data and try again."
        }
    )

# Route definitions
@app.get("/")
async def root():
    """Root endpoint for API health check"""
    return {"message": "Frontdesk Assistant API is running!", "status": "ok"}

@app.post("/call")
async def receive_call(request: CallRequest):
    """Handle incoming call/question and determine if it can be answered"""
    try:
        question = request.question.lower()
        caller_info = request.caller_info
        
        logger.info(f"Received call from {caller_info} with question: '{question}'")
        
        # Extract room ID from caller_info (assuming format like "room-123")
        room_id = caller_info.split("#")[0] if "#" in caller_info else caller_info
        
        # First check in static knowledge base
        if question in knowledge_base:
            response = knowledge_base[question]
            logger.info(f"Responding from static knowledge base: {response}")
            return {"response": response, "status": "answered"}
        
        # Then check in learned dynamic knowledge base
        dynamic_knowledge = load_dynamic_knowledge()
        if question in dynamic_knowledge:
            response = dynamic_knowledge[question]
            logger.info(f"Responding from learned knowledge base: {response}")
            return {"response": response, "status": "answered"}
        
        # If not known → Escalate to supervisor
        logger.info(f"I don't know the answer to: {question}. Requesting supervisor help.")
        
        # Create help request
        help_request = HelpRequest(question=question, caller_info=caller_info)
        
        # Add to in-memory list
        help_requests.append(help_request.to_dict())
        
        # Save to file
        save_help_requests(help_requests)
        logger.info(f"Created help request with ID: {help_request.id}")
        
        # Send notification to the notification service
        await send_notification("/notify/request-created", {
            "room_id": room_id,
            "request_id": help_request.id,
            "question": question,
            "status": "pending"
        })
        
        # Log the escalation
        logger.info(f"Help needed for question: '{question}' from {caller_info}")
        
        return {
            "response": "Let me check with my supervisor and get back to you.",
            "status": "escalated",
            "help_request_id": help_request.id
        }
    except Exception as e:
        logger.error(f"Error processing call request: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Server error: {str(e)}", "status": "error"}
        )


# Replace the get_pending_requests function with this one
@app.get("/pending-requests")
async def get_pending_requests():
    """Get all pending help requests for supervisor dashboard"""
    # Reload from file to ensure we have the latest data
    global help_requests
    help_requests = load_help_requests()
    
    # Filter out any non-dictionary entries and get pending ones
    valid_requests = []
    for req in help_requests:
        # Check if it's a dictionary and has a status field
        if isinstance(req, dict) and 'status' in req:
            # Convert lowercase "pending" to "Pending" if needed
            if req['status'] == "pending":
                req['status'] = "Pending"
            
            if req['status'] == "Pending":
                # Make sure all required fields exist
                if 'id' not in req:
                    req['id'] = str(uuid.uuid4())[:8]
                if 'created_at' not in req:
                    req['created_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if 'caller_info' not in req:
                    req['caller_info'] = "Unknown Caller"
                
                valid_requests.append(req)
        else:
            logger.warning(f"Found invalid request: {req}")
    
    logger.info(f"Returning {len(valid_requests)} pending requests")
    return valid_requests

@app.get("/all-requests")
async def get_all_requests():
    """Get all help requests (pending and resolved)"""
    # Reload from file to ensure we have the latest data
    global help_requests
    help_requests = load_help_requests()
    
    # Filter out any non-dictionary entries
    valid_requests = [req for req in help_requests if isinstance(req, dict)]
    
    logger.info(f"Returning all {len(valid_requests)} requests")
    return valid_requests

@app.post("/resolve-request")
async def resolve_request(request: ResolveRequest):
    """Handle supervisor submitting an answer to a help request"""
    request_id = request.id
    answer = request.answer
    
    logger.info(f"Resolving request {request_id} with answer: {answer}")
    
    # Reload help requests to ensure we have the latest data
    global help_requests
    help_requests = load_help_requests()
    
    updated = False
    resolved_question = None
    caller_info = None
    
    # Find and update the specific request
    for req in help_requests:
        if isinstance(req, dict) and 'id' in req and req['id'] == request_id:
            # Check if status is pending (case-insensitive)
            if req.get('status', '').lower() == "pending":
                req['status'] = "Resolved"
                req['resolved_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                req['answer'] = answer
                resolved_question = req.get('question')
                caller_info = req.get('caller_info', 'Unknown Caller')
                updated = True
                break
    
    # If not found in the list format, check if it's in dictionary format
    if not updated:
        # Try to find the request by ID directly (if help_requests is a dict)
        if isinstance(help_requests, dict) and request_id in help_requests:
            req = help_requests[request_id]
            if req.get('status', '').lower() == "pending":
                req['status'] = "Resolved"
                req['resolved_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                req['answer'] = answer
                resolved_question = req.get('question')
                caller_info = req.get('caller_info', 'Unknown Caller')
                # Add the ID to the dictionary if not present
                if 'id' not in req:
                    req['id'] = request_id
                updated = True
    
    if updated:
        # Save updated help requests
        save_help_requests(help_requests)
        logger.info(f"Request {request_id} has been resolved")
        
        # Add to dynamic knowledge base for future use
        if resolved_question:
            dynamic_knowledge = load_dynamic_knowledge()
            dynamic_knowledge[resolved_question] = answer
            save_knowledge_base(dynamic_knowledge)
            logger.info(f"Added to knowledge base: '{resolved_question}': '{answer}'")
            
            # Extract room ID from caller_info
            room_id = caller_info.split("#")[0] if "#" in caller_info else caller_info
            
            # Send notification to the notification service
            await send_notification("/notify/request-resolved", {
                "room_id": room_id,
                "request_id": request_id,
                "question": resolved_question,
                "answer": answer,
                "status": "resolved"
            })
            
            # Log the follow-up
            logger.info(f"Following up with caller: The answer to '{resolved_question}' is: {answer}")
            
        return {"message": "Request resolved successfully."}
    else:
        logger.warning(f"Request {request_id} not found or already resolved")
        raise HTTPException(status_code=404, detail="Request not found or already resolved.")

@app.get("/learned-answers")
async def get_learned_answers():
    """Get all learned answers for supervisor dashboard"""
    dynamic_knowledge = load_dynamic_knowledge()
    logger.info(f"Returning {len(dynamic_knowledge)} learned answers")
    return dynamic_knowledge

@app.get("/check-request/{request_id}")
async def check_request_status(request_id: str):
    """Check the status of a specific request"""
    # Reload help requests to ensure we have the latest data
    global help_requests
    help_requests = load_help_requests()
    
    for req in help_requests:
        if req['id'] == request_id:
            logger.info(f"Found request {request_id} with status {req['status']}")
            return req
    
    logger.warning(f"Request {request_id} not found")
    raise HTTPException(status_code=404, detail="Request not found")

@app.get("/request-status/{request_id}")
async def get_request_status(request_id: str):
    """Get the status of a specific request for the agent to check"""
    logger.info(f"Checking status of request {request_id}")
    
    for req in help_requests:
        if req['id'] == request_id:
            status = "resolved" if req['status'] == "Resolved" else "pending"
            result = {
                "status": status,
                "request_id": req['id']
            }
            
            # Include answer if resolved
            if status == "resolved":
                result["answer"] = req['answer']
                
            return result
    
    logger.warning(f"Request {request_id} not found")
    raise HTTPException(status_code=404, detail="Request not found")

@app.delete("/clear-resolved/{caller_id}/{request_id}")
async def clear_resolved_request(caller_id: str, request_id: str):
    """Clear a resolved request after the agent has notified the caller"""
    logger.info(f"Clearing resolved request {request_id} for caller {caller_id}")
    
    # Request should already be marked as resolved at this point
    # This endpoint is just for tracking and logging purposes
    
    # Find the request to confirm it exists and is resolved
    found = False
    for req in help_requests:
        if req['id'] == request_id and req['caller_info'] == caller_id:
            if req['status'] == "Resolved":
                found = True
                # We could add additional tracking here if needed
                # e.g., req['notified'] = True
                logger.info(f"Confirmed request {request_id} was resolved and notification delivered")
            else:
                logger.warning(f"Request {request_id} is not in 'Resolved' state")
                return {"success": False, "error": "Request is not in 'Resolved' state"}
    
    if not found:
        logger.warning(f"Request {request_id} for caller {caller_id} not found")
        return {"success": False, "error": "Request not found"}
    
    # Save any updates we made to the help requests
    save_help_requests(help_requests)
    
    return {"success": True, "message": "Request acknowledged successfully"}

# Serve the dashboard
@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    """Return the dashboard HTML"""
    try:
        with open("static/index.html", "r") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        logger.error("Dashboard HTML file not found. Creating default index.html.")
        # Write index.html to the static directory
        with open("static/index.html", "w") as f:
            # Write frontend HTML (you could include the HTML here)
            with open("static/index.html", "w") as f:
                f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>Frontdesk AI Dashboard</title>
    <meta http-equiv="refresh" content="0; url=/static/index.html">
</head>
<body>
    <p>Redirecting to dashboard...</p>
</body>
</html>
                """)
        return HTMLResponse(content="<h1>Dashboard initialized. Please refresh the page.</h1>")

# Run the app
if __name__ == "__main__":
    import uvicorn
    # Ensure the static directory exists
    os.makedirs("static", exist_ok=True)
    
    # Create index.html if it doesn't exist yet
    if not os.path.exists("static/index.html"):
        logger.info("Creating initial index.html")
        # Copy the dashboard HTML to the static directory
        # This would be where you'd place the HTML from paste-4.txt
        # Here we're just creating a simple redirect
        with open("static/index.html", "w") as f:
            f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>Frontdesk AI Dashboard</title>
    <meta http-equiv="refresh" content="0; url=/static/index.html">
</head>
<body>
    <p>Redirecting to dashboard...</p>
</body>
</html>
            """)
    
    logger.info("Starting Frontdesk API server")
    # Start the server - binding to all interfaces (0.0.0.0)
    # to allow connections from other devices
    uvicorn.run(app, host="0.0.0.0", port=5000)