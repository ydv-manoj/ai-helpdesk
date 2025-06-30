import json
import logging
import asyncio
import websockets
from dotenv import load_dotenv

from livekit import agents
from livekit.agents import (
    AgentSession, 
    Agent, 
    RoomInputOptions,
    function_tool,
    RunContext
)
from livekit.plugins import (
    groq,
    deepgram,
    noise_cancellation,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel
import os
import requests
import uuid
from datetime import datetime
import time 
from threading import Timer

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("salon-assistant")

# Load environment variables
load_dotenv()

# API and WebSocket endpoints
API_URL = os.getenv("API_URL", "http://127.0.0.1:5000")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:5002")
WEBSOCKET_URL = os.getenv("WEBSOCKET_URL", "ws://127.0.0.1:5002/ws")

# Path for help requests
HELP_REQUESTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "help_requests.json")

# Track recently asked questions to avoid duplicates
recent_questions = {}


def save_help_request(request_id, question):
    """Save a help request to the JSON file"""
    try:
        # Load existing requests
        existing_requests = {}
        if os.path.exists(HELP_REQUESTS_PATH):
            try:
                with open(HELP_REQUESTS_PATH, 'r') as f:
                    existing_requests = json.load(f)
            except json.JSONDecodeError:
                existing_requests = {}
        
        # Ensure it's a dictionary
        if not isinstance(existing_requests, dict):
            existing_requests = {}
        
        # Check if this question already exists in any form
        question_lower = question.lower().strip()
        for req_id, req_data in existing_requests.items():
            if req_data.get("question", "").lower().strip() == question_lower:
                logger.info(f"Question already exists as request {req_id}, not creating duplicate")
                return req_id
        
        # Add the new request
        existing_requests[request_id] = {
            "question": question,
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        }
        
        # Save back to file
        with open(HELP_REQUESTS_PATH, 'w') as f:
            json.dump(existing_requests, f, indent=2)
        
        logger.info(f"Saved help request {request_id} to file: {HELP_REQUESTS_PATH}")
        return request_id
    except Exception as e:
        logger.error(f"Error saving help request to file: {e}")
        return request_id


class SalonAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a salon receptionist voice AI agent named Salon Assistant. Follow these guidelines:\n\n"
                "1. IMMEDIATELY GREET CUSTOMERS when they join with a warm, friendly welcome.\n\n"
                "2. NEVER use your own knowledge to answer questions about salon services, pricing, or appointments.\n\n"
                "3. USE THE QUERY_KNOWLEDGE_BASE TOOL for ALL customer inquiries about:\n"
                "   - Services (haircuts, coloring, styling, treatments)\n"
                "   - Pricing information\n"
                "   - Availability/appointments\n"
                "   - Salon policies\n"
                "   - Product recommendations\n"
                "   - Bridal services\n"
                "   - Spa services\n\n"
                "4. IMMEDIATELY use the create_help_request tool when:\n"
                "   - query_knowledge_base returns status 'escalated'\n"
                "   - ANY questions about payments, especially international payments\n"
                "   - Questions about currencies or payment methods\n"
                "   - ANY question you don't have a direct answer for\n\n"
                "5. When creating a help request:\n"
                "   - Tell the customer you're checking with a supervisor\n"
                "   - Provide the request ID and estimated response time\n"
                "   - Reassure them that you'll notify them when you get an answer\n\n"
                "6. When the customer SPECIFICALLY ASKS to check with a supervisor:\n"
                "   - ALWAYS use the CREATE_HELP_REQUEST tool to submit their question\n"
                "   - Tell them you've sent their question to the supervisor\n"
                "   - Provide a request ID and estimated response time\n\n"
                "7. Maintain a NATURAL CONVERSATIONAL TONE:\n"
                "   - Be attentive and responsive\n"
                "   - Avoid robotic-sounding phrases\n"
                "   - Use casual, friendly language\n"
                "   - Acknowledge and validate customer concerns\n\n"
                "IMPORTANT: ALWAYS KEEP THE CONVERSATION GOING. If you don't know an answer, apologize, "
                "explain you need to check with a supervisor, and suggest they can ask about other topics.\n\n"
                "CRITICAL: For payment-related questions about international payments, credit cards, currencies, etc., "
                "ALWAYS use query_knowledge_base and then create_help_request in sequence. NEVER skip creating a help request."
            ),
            tools=[]  # We'll add tools via decorators
        )
        self._agent_session = None  # Using a different name to avoid conflict
        self._websocket = None
        self._pending_requests = {}  # Store pending requests by ID
        self._room_id = None
        self._recently_escalated = {}  # Track recently escalated questions to avoid duplicates
        logger.info("Salon Assistant Initialized")
    
    def set_agent_session(self, session, room_id):
        """Set the agent session and room ID for WebSocket connection"""
        self._agent_session = session
        self._room_id = room_id
        # Start WebSocket client in background
        asyncio.create_task(self._start_websocket_client())
        logger.info(f"Session set with room ID: {room_id}")
    
    async def _start_websocket_client(self):
        """Start WebSocket client to listen for resolved requests"""
        ws_url = f"{WEBSOCKET_URL}/{self._room_id}"
        retry_count = 0
        max_retries = 5
        retry_delay = 5  # seconds
        
        while retry_count < max_retries:
            try:
                logger.info(f"Connecting to WebSocket at {ws_url}")
                async with websockets.connect(ws_url) as websocket:
                    self._websocket = websocket
                    logger.info(f"WebSocket connected for room {self._room_id}")
                    
                    # Main message loop
                    while True:
                        try:
                            message = await websocket.recv()
                            data = json.loads(message)
                            logger.info(f"Received WebSocket message: {data}")
                            
                            if data.get("type") == "request_resolved":
                                # Handle resolved request
                                await self._handle_resolved_request(data)
                            elif data.get("type") == "ping":
                                # Respond to ping
                                await websocket.send(json.dumps({"type": "pong"}))
                        except json.JSONDecodeError:
                            logger.warning(f"Received non-JSON message: {message}")
                        except Exception as e:
                            logger.error(f"Error processing WebSocket message: {e}")
                
                # If we exit the loop normally, reset retry count
                retry_count = 0
            except (websockets.exceptions.ConnectionClosed, 
                    websockets.exceptions.ConnectionClosedError,
                    websockets.exceptions.ConnectionClosedOK):
                logger.warning(f"WebSocket connection closed, retrying in {retry_delay} seconds...")
                retry_count += 1
                await asyncio.sleep(retry_delay)
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
                retry_count += 1
                await asyncio.sleep(retry_delay)
            
            # Increase retry delay for exponential backoff
            retry_delay = min(retry_delay * 2, 60)  # Cap at 60 seconds
        
        logger.error(f"Failed to maintain WebSocket connection after {max_retries} attempts")
    
    async def _handle_resolved_request(self, data):
        """Handle a resolved request notification"""
        request_id = data.get("request_id")
        question = data.get("question", "")
        answer = data.get("answer", "")
        
        if not request_id or not answer:
            logger.warning(f"Received incomplete resolved request data: {data}")
            return
        
        logger.info(f"Handling resolved request {request_id}: Q: '{question}', A: '{answer}'")
        
        # Check if we've already handled this request
        if request_id in self._pending_requests and self._pending_requests[request_id].get("handled", False):
            logger.info(f"Request {request_id} already handled, skipping")
            return
        
        # Store the request details
        self._pending_requests[request_id] = {
            "question": question,
            "answer": answer,
            "status": "resolved",
            "handled": False
        }
        
        # Add a small delay before speaking to ensure the system is ready
        await asyncio.sleep(1)
        
        # Speak the answer to the user
        await self._speak_resolved_answer(request_id, question, answer)
    
    async def _speak_resolved_answer(self, request_id, question, answer):
        """Speak the resolved answer to the user with multiple fallback mechanisms"""
        if not self._agent_session:
            logger.error(f"Cannot speak answer for request {request_id}: No active session")
            return
        
        # Prepare the message to acknowledge the resolved request
        message = (
            f"I just received an answer to your question about {question}. "
            f"The answer is: {answer} "
            f"Is there anything else you'd like to know about our salon services?"
        )
        
        # Track if any method succeeds
        success = False
        
        # Method 1: Use say directly (most direct approach)
        try:
            logger.info(f"First attempt: Speaking answer for request {request_id} using say")
            await self._agent_session.say(message)
            success = True
            logger.info(f"Successfully spoke answer using say method")
        except Exception as e:
            logger.error(f"Error using say method for request {request_id}: {e}")
        
        # Method 2: If first method failed, use generate_reply with wait
        if not success:
            try:
                logger.info(f"Second attempt: Speaking answer using generate_reply with delay")
                await asyncio.sleep(2)  # Pause to ensure any previous speech is complete
                
                # Use shorter message to reduce chance of TTS issues
                short_message = f"About your question on {question}: {answer}"
                await self._agent_session.generate_reply(
                    instructions=f"Speak only this exact message to the user: {short_message}"
                )
                success = True
                logger.info(f"Successfully spoke answer using generate_reply method")
            except Exception as e:
                logger.error(f"Error using generate_reply for request {request_id}: {e}")
        
        # Method 3: Try a different format
        if not success:
            try:
                logger.info(f"Third attempt: Breaking message into smaller chunks")
                await asyncio.sleep(3)  # Longer pause
                
                # Break into smaller chunks
                await self._agent_session.say(f"I have an answer to your question.")
                await asyncio.sleep(1)
                await self._agent_session.say(f"Regarding {question}, the answer is: {answer}")
                
                success = True
                logger.info(f"Successfully spoke answer in chunks")
            except Exception as e:
                logger.error(f"Error speaking in chunks for request {request_id}: {e}")
        
        # Method 4: Final fallback attempt with simpler text
        if not success:
            try:
                logger.info(f"Final attempt: Using simplest possible method")
                await asyncio.sleep(4)  # Even longer pause
                
                # Use the most basic message possible
                await self._agent_session.say(f"The answer to your question is: {answer}")
                
                success = True
                logger.info(f"Successfully delivered answer using final fallback")
            except Exception as e:
                logger.error(f"All speech attempts failed for request {request_id}: {e}")
        
        # Even if we failed to speak, mark as handled so we don't try again
        self._pending_requests[request_id]["handled"] = True
        
        # Set up a delayed notification to the API
        # This gives time for TTS to complete before clearing the request
        def delayed_notification():
            asyncio.create_task(self._notify_answer_delivered(request_id))
        
        # Run the notification after a delay to ensure TTS completes
        Timer(3.0, delayed_notification).start()
        logger.info(f"Set up delayed notification for request {request_id}")
    
    async def _notify_answer_delivered(self, request_id):
        """Notify the API that the answer was delivered to the user"""
        try:
            # Double check that this request exists and hasn't been notified already
            if request_id not in self._pending_requests:
                logger.warning(f"Request {request_id} not found in pending requests, skipping notification")
                return
            
            # Call the API to mark the request as delivered
            response = requests.delete(
                f"{API_URL}/clear-resolved/{self._room_id}/{request_id}",
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"Successfully notified API that request {request_id} was delivered")
                
                # Mark the request as notified to prevent duplicate notifications
                self._pending_requests[request_id]["notified"] = True
            else:
                logger.warning(f"Failed to notify API about delivered request {request_id}: {response.status_code}")
        except Exception as e:
            logger.error(f"Error notifying API about delivered request {request_id}: {e}")
    
    def _question_recently_escalated(self, question):
        """Check if a question was recently escalated to avoid duplicates"""
        question_lower = question.lower().strip()
        
        # Clean up old entries (older than 5 minutes)
        current_time = time.time()
        for q in list(self._recently_escalated.keys()):
            if current_time - self._recently_escalated[q] > 300:  # 5 minutes
                del self._recently_escalated[q]
        
        # Check if this question or a similar one was recently escalated
        for q in self._recently_escalated:
            if question_lower in q or q in question_lower:
                return True
        
        return False
    
    async def handle_escalated_question(self, question):
        """
        Handle an escalated question by creating a help request and notifying the user.
        This is called manually when needed, bypassing the LLM decision process.
        """
        try:
            # Check if this question was recently escalated
            if self._question_recently_escalated(question):
                logger.info(f"Question '{question}' was recently escalated, skipping duplicate")
                return None
            
            # Mark as recently escalated
            self._recently_escalated[question.lower().strip()] = time.time()
            
            # Generate request ID
            request_id = str(uuid.uuid4())[:8]
            
            # Save help request to file directly
            save_help_request(request_id, question)
            
            # Try API call
            room_id = self._room_id or "room-unknown"
            
            try:
                # Call the API
                response = requests.post(
                    f"{API_URL}/call",
                    json={
                        "question": question.strip(),
                        "caller_info": room_id,
                        "require_supervisor": True
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if "help_request_id" in data:
                        request_id = data["help_request_id"]  # Use API-generated ID
                        logger.info(f"Help request created via API with ID: {request_id}")
            except Exception as e:
                logger.error(f"Error sending help request to API: {e}")
            
            # Store in memory
            self._pending_requests[request_id] = {
                "question": question,
                "status": "pending",
                "created_at": datetime.now().isoformat()
            }
            
            # Tell the user about the escalation
            message = (
                f"I've sent your question about {question} to my supervisor. "
                f"Your request ID is {request_id}. They usually respond within 5-10 minutes. "
                f"I'll let you know as soon as I hear back. "
                f"Is there anything else I can help you with in the meantime?"
            )
            
            if self._agent_session:
                logger.info(f"Informing user about escalation with request ID: {request_id}")
                await self._agent_session.generate_reply(
                    instructions=f"Speak this exact message to the user: {message}"
                )
            
            return request_id
        except Exception as e:
            logger.error(f"Error handling escalated question: {e}")
            return None
    
    @function_tool()
    async def query_knowledge_base(
        self,
        context: RunContext,
        question: str
    ) -> dict:
        """
        Query the salon knowledge base with a customer question.
        
        Args:
            question: The customer's question about salon services or information
                
        Returns:
            A dictionary containing:
            - status: 'answered' or 'escalated'
            - response: Answer text if status is 'answered'
            - question: Original question (included for escalated questions)
        """
        logger.info(f"Querying knowledge base for: '{question}'")
        
        # First, check the local knowledge base
        response = self._check_local_knowledge(question)
        
        if response:
            # Knowledge found locally
            logger.info(f"Found answer in local knowledge base: {response}")
            return {
                "status": "answered",
                "response": response
            }
        else:
            # If not found in local knowledge, escalate to supervisor
            logger.info(f"No answer found in knowledge base for: '{question}', escalating")
            
            # Automatically create a help request for escalated questions
            # But avoid creating duplicate requests
            request_id = await self.handle_escalated_question(question)
            
            return {
                "status": "escalated",
                "question": question,  # Include the original question for easier reference
                "response": (
                    f"I don't have information about {question} in my knowledge base yet. "
                    f"I'd need to check with my supervisor about this specific question. "
                    f"In the meantime, is there anything else I can help you with about our services, "
                    f"pricing, or appointment availability?"
                )
            }
    
    @function_tool()
    async def create_help_request(
        self,
        context: RunContext,
        question: str
    ) -> dict:
        """
        Create a specific help request for supervisor assistance.
        
        Args:
            question: The customer's question that needs supervisor input
                
        Returns:
            A dictionary containing:
            - status: 'escalated'
            - help_request_id: ID of the created request
        """
        logger.info(f"Creating help request for: '{question}'")
        
        # Check if this question was recently escalated
        if self._question_recently_escalated(question):
            logger.info(f"Question '{question}' was recently escalated via another method, skipping duplicate")
            # Return the same style of response, but don't actually create a new request
            return {
                "status": "escalated",
                "help_request_id": "pending",
                "estimated_time": "5-10 minutes", 
                "response": (
                    f"I've already sent your question about {question} to my supervisor. "
                    f"They usually respond within 5-10 minutes. "
                    f"I'll let you know as soon as I hear back. "
                    f"Is there anything else I can help you with in the meantime?"
                )
            }
        
        # Mark as recently escalated
        self._recently_escalated[question.lower().strip()] = time.time()
        
        # Generate a unique request ID
        request_id = str(uuid.uuid4())[:8]
        
        # Save help request to file
        request_id = save_help_request(request_id, question)
        
        # Use the room_id from the session if available
        room_id = self._room_id or "room-unknown"
        
        try:
            logger.info(f"Sending help request to API at {API_URL}/call")
            response = requests.post(
                f"{API_URL}/call",
                json={
                    "question": question.strip(),
                    "caller_info": room_id,
                    "require_supervisor": True
                },
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "help_request_id" in data:
                    request_id = data["help_request_id"]
                logger.info(f"Help request created with ID: {request_id}")
                
                # Add to pending requests
                self._pending_requests[request_id] = {
                    "question": question,
                    "status": "pending",
                    "created_at": datetime.now().isoformat()
                }
            else:
                logger.warning(f"API returned non-200 status: {response.status_code}")
        except Exception as e:
            logger.error(f"Error sending help request to API: {e}")
        
        return {
            "status": "escalated",
            "help_request_id": request_id,
            "estimated_time": "5-10 minutes", 
            "response": (
                f"I've sent your question about {question} to my supervisor. "
                f"Your request ID is {request_id}. They usually respond within 5-10 minutes. "
                f"I'll let you know as soon as I hear back. "
                f"Is there anything else I can help you with in the meantime?"
            )
        }
    
    def _check_local_knowledge(self, question: str) -> str:
        """
        Check the local knowledge base for an answer to the question.
        
        Args:
            question: The customer's question
        
        Returns:
            Answer string if found, None if not found
        """
        # Convert question to lowercase for easier matching
        question_lower = question.lower()
        
        # Check for services questions
        if any(keyword in question_lower for keyword in ["haircut", "cut", "trim", "style"]):
            if "price" in question_lower or "cost" in question_lower:
                if "women" in question_lower:
                    return "Our women's haircuts start at $45, depending on hair length and styling needs."
                elif "men" in question_lower:
                    return "Men's haircuts start at $30, including a wash and style."
                elif "child" in question_lower or "kid" in question_lower:
                    return "Children's haircuts (12 and under) start at $25."
                else:
                    return "Our haircuts start at $30 for men, $45 for women, and $25 for children. The final price depends on hair length and styling needs."
            else:
                return "Yes, we offer haircut services for men, women, and children. Our stylists are experienced in various cutting techniques and styles."
        
        # Keep the rest of your knowledge checks...
        
        # Check exact matches or key phrases from the API knowledge base
        exact_matches = {
            "do you have bridal makeup services": "Yes We have bridal makeup services",
            "do you have spa services": "Yes, we provide a variety of spa treatments including massages and facials.",
            "do you have hair dressing cutting services": "Yes, we offer professional hair cutting and styling services.",
            "do you have party makeup": "Yes we provide party make up",
            "do you offer hair bleaching": "Yes we offer different colors of hair bleaching",
            "do you have makeup services": "Yes, we provide all kinds of makeup services from facial, tanning to every other makeup service",
            "do you offer bridal makeup": "Yes we do offer bridal makeup",
            "do you offer back or hair removal service in your salon": "Yes we do offer waxing and hair removal at our salon"
        }
        
        # Try to match question (with and without question mark)
        clean_question = question_lower.rstrip('?').strip()
        if clean_question in exact_matches:
            return exact_matches[clean_question]
        
        # If no matches found
        return None


async def entrypoint(ctx: agents.JobContext):
    # Connect to the LiveKit room
    await ctx.connect()
    room_name = ctx.room.name if ctx.room else "fake_room"  # Provide a default room name
    logger.info(f"Connected to LiveKit room: {room_name}")
    
    # Create help_requests.json file if it doesn't exist
    if not os.path.exists(HELP_REQUESTS_PATH):
        try:
            with open(HELP_REQUESTS_PATH, 'w') as f:
                json.dump({}, f)
                logger.info(f"Created empty help_requests.json file at {HELP_REQUESTS_PATH}")
        except Exception as e:
            logger.error(f"Error creating help_requests.json: {e}")
    
    # Initialize agent
    salon_assistant = SalonAssistant()
    
    # Initialize agent session
    session = AgentSession(
        # Speech to text
        stt=deepgram.STT(model="nova-3", language="en"),
        
        # Language model 
        llm=groq.LLM(model="meta-llama/llama-4-maverick-17b-128e-instruct"),
        
        # Text to speech
        tts=deepgram.TTS(
            model="aura-2-athena-en",
        ),
        # Voice activity detection
        vad=silero.VAD.load(),
        
        # Turn detection
        turn_detection=MultilingualModel(),
    )
    
    # Start the agent session
    await session.start(
        room=ctx.room,
        agent=salon_assistant,
        room_input_options=RoomInputOptions(
            # Add noise cancellation if using LiveKit Cloud
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )
    
    # Set the session in the agent AFTER starting the session
    salon_assistant.set_agent_session(session, room_name)
    
    # Send an immediate greeting
    try:
        await asyncio.sleep(1)  # Small delay to ensure connection is ready
        await session.generate_reply(
            instructions=(
                "Greet the user warmly as a salon receptionist and offer your assistance with booking appointments, "
                "inquiring about services, or answering any salon-related questions. Keep it brief and welcoming."
            )
        )
        logger.info("Initial greeting sent successfully")
    except Exception as e:
        logger.error(f"Error sending initial greeting: {e}")
        # Try one more time
        try:
            await asyncio.sleep(2)
            await session.say(
                "Hello! Welcome to our salon. I'm your virtual receptionist. How can I help you today?"
            )
            logger.info("Fallback greeting sent successfully")
        except Exception as retry_e:
            logger.error(f"Failed to send fallback greeting: {retry_e}")

    # Send instructions about creating help requests
    await asyncio.sleep(2)
    try:
        await session.generate_reply(
            instructions=(
                "IMPORTANT: Whenever a user asks about salon services or any information you don't have a direct answer for, "
                "ALWAYS escalate to a supervisor by creating a help request. NEVER just say you don't know without creating "
                "a formal help request that will be tracked. Always notify the user that you're checking with a supervisor and "
                "provide them with a request ID and estimated response time."
            )
        )
    except Exception as e:
        logger.error(f"Error sending critical instructions: {e}")

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))