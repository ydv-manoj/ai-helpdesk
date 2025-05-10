from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Set
import json
import asyncio
from datetime import datetime
import logging
from pydantic import BaseModel

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Log to console
    ]
)
logger = logging.getLogger("notification_service")

app = FastAPI(title="Frontdesk Notification Service")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active connections
active_connections: Dict[str, List[WebSocket]] = {}
# Store pending requests for each room
pending_requests: Dict[str, Dict[str, Dict]] = {}

# Pydantic model for notification payloads
class Notification(BaseModel):
    room_id: str
    request_id: str
    question: str
    answer: str = None
    status: str

@app.get("/")
async def root():
    """Root endpoint for health check"""
    logger.info("Health check request received")
    return {"status": "ok", "message": "Notification service is running"}

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    """WebSocket endpoint for voice agent to connect and receive notifications"""
    await websocket.accept()
    logger.info(f"WebSocket connection established for room: {room_id}")
    
    # Initialize connection list for room if needed
    if room_id not in active_connections:
        active_connections[room_id] = []
    
    # Add this connection to the list
    active_connections[room_id].append(websocket)
    
    try:
        # Send any pending requests for this room
        if room_id in pending_requests:
            for request_id, request_data in pending_requests[room_id].items():
                if request_data.get("status") == "resolved":
                    logger.info(f"Sending resolved request {request_id} to newly connected client in room {room_id}")
                    await websocket.send_json({
                        "type": "request_resolved",
                        "request_id": request_id,
                        "question": request_data.get("question"),
                        "answer": request_data.get("answer"),
                        "timestamp": datetime.now().isoformat()
                    })
                elif request_data.get("status") == "pending":
                    logger.info(f"Sending pending request {request_id} to newly connected client in room {room_id}")
                    await websocket.send_json({
                        "type": "request_created",
                        "request_id": request_id,
                        "question": request_data.get("question"),
                        "timestamp": datetime.now().isoformat()
                    })
        
        # Keep the connection alive and handle incoming messages
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                if message["type"] == "ping":
                    logger.debug(f"Received ping from {room_id}, sending pong")
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                logger.warning(f"Non-JSON message received from {room_id}: {data}")
            except Exception as e:
                logger.error(f"Error processing message from {room_id}: {e}")
    except WebSocketDisconnect:
        # Remove connection when disconnected
        logger.info(f"WebSocket disconnected for room: {room_id}")
        active_connections[room_id].remove(websocket)
        if not active_connections[room_id]:
            del active_connections[room_id]
    except Exception as e:
        logger.error(f"WebSocket error for {room_id}: {e}")
        if room_id in active_connections and websocket in active_connections[room_id]:
            active_connections[room_id].remove(websocket)
            if not active_connections[room_id]:
                del active_connections[room_id]

@app.post("/notify/request-created")
async def notify_request_created(notification: Notification):
    """Notify that a new help request has been created"""
    room_id = notification.room_id
    request_id = notification.request_id
    question = notification.question
    
    logger.info(f"New help request created: {request_id} for room {room_id}, question: '{question}'")
    
    # Store the pending request
    if room_id not in pending_requests:
        pending_requests[room_id] = {}
    
    pending_requests[room_id][request_id] = {
        "question": question,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }
    
    # Notify ALL connected clients about the new request (including dashboard)
    notification_sent = False
    
    # First notify the room that created the request
    if room_id in active_connections:
        for connection in active_connections[room_id]:
            try:
                await connection.send_json({
                    "type": "request_created",
                    "request_id": request_id,
                    "question": question,
                    "timestamp": datetime.now().isoformat()
                })
                notification_sent = True
                logger.info(f"Notification sent to room {room_id} about request {request_id}")
            except Exception as e:
                logger.error(f"Error sending notification to room {room_id}: {e}")
    
    # Then notify the dashboard
    if "dashboard" in active_connections:
        for connection in active_connections["dashboard"]:
            try:
                await connection.send_json({
                    "type": "request_created",
                    "request_id": request_id,
                    "question": question,
                    "room_id": room_id,
                    "timestamp": datetime.now().isoformat()
                })
                notification_sent = True
                logger.info(f"Notification sent to dashboard about request {request_id}")
            except Exception as e:
                logger.error(f"Error sending notification to dashboard: {e}")
    
    return {
        "status": "ok", 
        "message": "Request created notification sent" if notification_sent else "No active connections, notification stored"
    }

@app.post("/notify/request-resolved")
async def notify_request_resolved(notification: Notification):
    """Notify that a help request has been resolved"""
    room_id = notification.room_id
    request_id = notification.request_id
    question = notification.question
    answer = notification.answer
    
    logger.info(f"Help request resolved: {request_id} for room {room_id}, answer: '{answer}'")
    
    # Update the pending request
    if room_id in pending_requests and request_id in pending_requests[room_id]:
        pending_requests[room_id][request_id]["status"] = "resolved"
        pending_requests[room_id][request_id]["answer"] = answer
        pending_requests[room_id][request_id]["resolved_at"] = datetime.now().isoformat()
    else:
        # Create the request if it doesn't exist (might happen if notification service restarted)
        if room_id not in pending_requests:
            pending_requests[room_id] = {}
        
        pending_requests[room_id][request_id] = {
            "question": question,
            "answer": answer,
            "status": "resolved",
            "created_at": datetime.now().isoformat(),
            "resolved_at": datetime.now().isoformat()
        }
    
    notification_sent = False
    
    # Notify the room and dashboard
    # First notify the room that created the request
    if room_id in active_connections:
        for connection in active_connections[room_id]:
            try:
                await connection.send_json({
                    "type": "request_resolved",
                    "request_id": request_id,
                    "question": question,
                    "answer": answer,
                    "timestamp": datetime.now().isoformat()
                })
                notification_sent = True
                logger.info(f"Resolution notification sent to room {room_id} about request {request_id}")
            except Exception as e:
                logger.error(f"Error sending resolution notification to room {room_id}: {e}")
    
    # Then notify the dashboard
    if "dashboard" in active_connections:
        for connection in active_connections["dashboard"]:
            try:
                await connection.send_json({
                    "type": "request_resolved",
                    "request_id": request_id,
                    "question": question,
                    "answer": answer,
                    "room_id": room_id,
                    "timestamp": datetime.now().isoformat()
                })
                notification_sent = True
                logger.info(f"Resolution notification sent to dashboard about request {request_id}")
            except Exception as e:
                logger.error(f"Error sending resolution notification to dashboard: {e}")
    
    return {
        "status": "ok", 
        "message": "Notification sent successfully" if notification_sent else "No active connections for this room, notification stored"
    }

@app.get("/pending-requests/{room_id}")
async def get_pending_requests(room_id: str):
    """Get all pending requests for a room"""
    if room_id in pending_requests:
        return pending_requests[room_id]
    return {}

@app.delete("/clear-resolved/{room_id}/{request_id}")
async def clear_resolved_request(room_id: str, request_id: str):
    """Clear a resolved request after it's been handled"""
    if room_id in pending_requests and request_id in pending_requests[room_id]:
        del pending_requests[room_id][request_id]
        logger.info(f"Cleared request {request_id} for room {room_id}")
        return {"status": "ok", "message": "Request cleared"}
    
    raise HTTPException(status_code=404, detail="Request not found")

# Background task to clean up inactive connections
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(check_connections())

async def check_connections():
    """Periodically check connections and clean up inactive ones"""
    while True:
        try:
            # Send ping to all connections to keep them alive
            for room_id, connections in list(active_connections.items()):
                for conn in list(connections):
                    try:
                        await conn.send_json({"type": "ping"})
                    except Exception:
                        # Connection is closed, remove it
                        if conn in active_connections[room_id]:
                            active_connections[room_id].remove(conn)
                        if not active_connections[room_id]:
                            del active_connections[room_id]
                            
            logger.debug(f"Active connections: {list(active_connections.keys())}")
        except Exception as e:
            logger.error(f"Error checking connections: {e}")
        
        # Wait 30 seconds before next check
        await asyncio.sleep(30)

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting notification service")
    uvicorn.run(app, host="0.0.0.0", port=5002)