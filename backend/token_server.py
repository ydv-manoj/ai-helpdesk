from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import os
from livekit import api

# Load environment variables
load_dotenv()

# Get LiveKit credentials
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# Create FastAPI app
app = FastAPI(title="LiveKit Token Server")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TokenRequest(BaseModel):
    room_name: str
    participant_name: str

@app.get("/")
async def root():
    """Root endpoint for status check"""
    return {"status": "ok", "message": "LiveKit Token Server is running"}

@app.get("/getToken")
async def get_token(
    identity: str = Query("user", description="Participant identity"),
    name: str = Query("User", description="Participant name"),
    room: str = Query("my-room", description="Room name")
):
    """Get a LiveKit token with default parameters"""
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=500, 
            detail="LiveKit credentials not configured. Please set LIVEKIT_API_KEY and LIVEKIT_API_SECRET environment variables."
        )
    
    try:
        # Create the token based on LiveKit documentation example
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(identity) \
            .with_name(name) \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=room,
            ))
        
        # Return the JWT token
        return JSONResponse(content={"token": token.to_jwt()})
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating token: {str(e)}")

@app.post("/create-token")
async def create_token(request: TokenRequest):
    """Create a LiveKit token for a room and participant"""
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=500, 
            detail="LiveKit credentials not configured. Please set LIVEKIT_API_KEY and LIVEKIT_API_SECRET environment variables."
        )
    
    try:
        # Create the token using the same approach as getToken
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(request.participant_name) \
            .with_name(request.participant_name) \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=request.room_name,
                can_publish=True,
                can_subscribe=True,
            ))
        
        # Return the token
        return {"token": token.to_jwt()}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating token: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)