# Salon Frontdesk AI Assistant

A human-in-the-loop voice AI system that acts as a salon receptionist, with capabilities to escalate questions to human supervisors when necessary.

## System Overview

This system creates a seamless customer service experience by combining AI and human expertise:

1. **Voice AI Agent**: Answers common customer questions about salon services, pricing, and policies
2. **Human Supervisors**: Handle complex inquiries that require human judgment or knowledge
3. **Real-time Notifications**: Ensure efficient communication between AI and human supervisors

## Architecture & Workflow

```
+------------------+         +-----------------+         +------------------+
|                  |         |                 |         |                  |
|    Customer      |  <-->   |   Voice Agent   |  <-->   |   Knowledge Base |
|                  |         |  (voice_agent.py)|        |                  |
+------------------+         +-----------------+         +------------------+
                                    |  ^
                                    |  |
                                    v  |
+-------------------+         +----------------+         +------------------+
|                   |         |                |         |                  |
|    Supervisor     |  <-->   |  Main API      |  <-->   |   Help Requests  |
|    Dashboard      |         |  (app.py)      |         |   (JSON file)    |
|                   |         |                |         |                  |
+-------------------+         +----------------+         +------------------+
        ^                           |  ^
        |                           |  |
        v                           v  |
+-------------------+         +-------------------------+
|                   |         |                         |
|   Token Server    |  <-->   |   Notification Service  |
|  (token_server.py)|         | (notification_service.py)|
|                   |         |                         |
+-------------------+         +-------------------------+

Data Flow:
1. Customer asks question → Voice Agent processes using LiveKit and Groq(deepseek model)
2. If answer known → Response from Knowledge Base
3. If answer unknown → Create Help Request → Notify Supervisor
4. Supervisor resolves request through dashboard
5. Notification Service alerts Voice Agent of resolution
6. Voice Agent speaks answer to Customer
7. New answer added to Knowledge Base for future use
```

### Component Interactions

1. **Customer Interaction**
   - Customer speaks with the AI voice agent
   - Voice agent uses knowledge base to answer common questions
   - For unknown questions, agent creates a help request

2. **Request Escalation Flow**
   - Voice agent sends help request to main app.py API
   - Notification service alerts supervisors of pending request
   - Help request stored in help_requests.json

3. **Resolution Flow**
   - Supervisor reviews and answers the request
   - Notification service alerts voice agent of resolution
   - Voice agent speaks the answer to the customer
   - Knowledge base is updated with new information

## Components

### Voice Agent (voice_agent.py)
- Conversational AI that interacts with customers
- Uses speech-to-text and text-to-speech for natural conversation
- Connects to knowledge base for common questions
- Creates help requests when it cannot answer a question

### API Server (app.py)
- Manages help requests and resolutions
- Serves as central coordination point
- Handles request creation, tracking, and resolution
- Updates knowledge base with new answers

### Notification Service (notification_service.py)
- Real-time WebSocket notifications between components
- Alerts supervisors of new help requests
- Notifies voice agent of resolved requests
- Maintains persistent connections for immediate updates

### Token Server (token_server.py)
- Handles authentication and security
- Generates session tokens for voice agent and supervisors
- Verifies request authenticity

### Knowledge Base
- Contains frequently asked questions and answers
- Automatically updated with new information from resolved requests
- Used by voice agent for answering common questions

## Installation Guide

### Prerequisites
- Python 3.9 or higher
- FFmpeg (for audio processing)
- LiveKit account (for voice communication)
- Node.js and npm (for dashboard frontend)

### Setup Instructions

1. **Clone the repository**
   ```bash
   git clone git@github.com:ydv-manoj/ai-helpdesk.git
   cd salon-frontdesk-ai
   ```

2. **Set up a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   cd backend
   ```

3. **Install dependencies**
   ```bash
   pip install \
   "livekit-agents[deepgram,openai,cartesia,silero,turn-detector]~=1.0" \
   "livekit-plugins-noise-cancellation~=0.2" \
   "python-dotenv"
   pip install -r requirements.txt
   ```

4. **Create and configure .env file**
   ```
   LIVEKIT_URL=""
   LIVEKIT_API_KEY=""
   LIVEKIT_API_SECRET=""
   GROQ_API_KEY=""
   CARTESIA_API_KEY=""
   DEEPGRAM_API_KEY=""
   NOTIFICATION_SERVICE_URL="ws://127.0.0.1:5002/ws"
   ```

5. **Initialize the knowledge base**
   ```bash
   echo "{}" > knowledge_base.json
   echo "{}" > help_requests.json
   ```

6. **Start the components (in separate terminals)**
   ```bash
   # Terminal 1: Start the main API server
   uvicorn app:app --reload --port 5000
   
   # Terminal 2: Start the notification service
   python3 notification_service.py
   
   # Terminal 3: Start the token server
   python3 token_server.py
   
   # Terminal 4: Start the voice agent
   python3 voice_agent.py
   ```

7. **Access the supervisor dashboard**
   - Start local port 5500 for /static/index.html


## Usage

### For Developers
- Voice agent automatically connects to LiveKit room
- New help requests appear in real-time on the dashboard
- Resolved answers are automatically spoken to customers


### For Supervisors
- Start localport 5500 for /static/index.html
- Review pending help requests
- Submit answers which are automatically delivered to customers
- Monitor conversation history and learn from previous interactions

## Requirements
- fastapi
- uvicorn
- websockets
- httpx
- python-dotenv
- asyncio
- livekit
- livekit-agents
- pydantic
- uuid
- requests
- json
- logging
- datetime
- time
