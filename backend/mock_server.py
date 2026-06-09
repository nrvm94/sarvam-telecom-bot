"""
Mock Server — port 5000
Simulates downstream services (ticket system, SMS, WhatsApp) for n8n workflow testing.
"""

import random
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Mock Downstream Services", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/mock/ticket")
async def create_ticket():
    """Simulate creating a support ticket."""
    ticket_id = "TKT-" + str(random.randint(10000, 99999))
    return {
        "ticket_id": ticket_id,
        "status": "created",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/mock/sms")
async def send_sms():
    """Simulate sending an SMS notification."""
    return {
        "status": "sent",
        "message": "SMS delivered",
    }


@app.post("/mock/whatsapp")
async def send_whatsapp():
    """Simulate sending a WhatsApp message via 360dialog."""
    return {
        "status": "sent",
        "message": "WhatsApp delivered",
    }


if __name__ == "__main__":
    uvicorn.run("mock_server:app", host="0.0.0.0", port=5000, reload=True)
