# n8n Cloud Escalation Workflow Setup

## Overview
This workflow triggers when the Airtel voice bot detects a complex customer issue.
It creates a support ticket, sends a WhatsApp notification, and logs the escalation back to the FastAPI backend.

**Deployment:** n8n Cloud (no Docker or local setup required)
**Backend:** https://sarvam-telecom-bot-production.up.railway.app

---

## Prerequisites
- n8n Cloud account at https://nrvmhdn.app.n8n.cloud
- Railway backend deployed and running (verify: `/health` endpoint returns `{"status":"ok"}`)
- 360dialog sandbox API key configured in `.env` as `DIALOG_360_API_KEY`
- WhatsApp opt-in completed for the sandbox recipient number

---

## Workflow Structure

```
[Webhook] → [Set Fields] → [Create Ticket] → [Send WhatsApp] → [Callback to FastAPI]
```

---

## Step 1 — Open n8n Cloud

1. Go to https://nrvmhdn.app.n8n.cloud and log in
2. Click **"+ New Workflow"**
3. Rename it: **"Airtel Support Escalation"**
4. Click **Save** (Ctrl+S)

---

## Step 2 — Add Webhook Trigger Node

1. Click **"+"** to add a node
2. Search for **"Webhook"** and select it
3. Configure:
   - **HTTP Method:** POST
   - **Path:** `escalation`
   - **Response Mode:** Last Node
   - **Authentication:** None
4. Click **Save**

The webhook URL will be: `https://nrvmhdn.app.n8n.cloud/webhook/escalation`

This URL must match the `N8N_WEBHOOK_URL` environment variable in Railway.

---

## Step 3 — Add "Set Fields" Node

1. Click **"+"** after the Webhook node
2. Search for **"Edit Fields"** and select it
3. Add these field mappings:

| Field Name   | Value                    |
|--------------|--------------------------|
| call_id      | `={{ $json.call_id }}`   |
| issue_type   | `={{ $json.issue_type }}` |
| user_query   | `={{ $json.user_query }}` |
| bot_response | `={{ $json.bot_response }}` |
| timestamp    | `={{ $json.timestamp }}` |

4. Click **Save**

---

## Step 4 — Add "Create Ticket" HTTP Request Node

1. Click **"+"** after the Set Fields node
2. Search for **"HTTP Request"** and select it
3. Rename it: **"Create Ticket"**
4. Configure:
   - **Method:** POST
   - **URL:** `https://sarvam-telecom-bot-production.up.railway.app/mock/ticket`
   - **Body Content Type:** JSON
   - **Specify Body:** Using JSON
   - **Body:**
     ```json
     {
       "call_id": "={{ $json.call_id }}",
       "issue_type": "={{ $json.issue_type }}",
       "user_query": "={{ $json.user_query }}"
     }
     ```
5. Click **Save**

The response will contain `ticket_id` (e.g. `"TKT-58291"`).

---

## Step 5 — Add "Send WhatsApp" HTTP Request Node

1. Click **"+"** after Create Ticket
2. Add another **HTTP Request** node
3. Rename it: **"Send WhatsApp"**
4. Configure:
   - **Method:** POST
   - **URL:** `https://waba-sandbox.360dialog.io/v1/messages`
   - **Authentication:** None (key goes in header)
   - **Headers:**
     - `D360-API-KEY`: `<your DIALOG_360_API_KEY>`
     - `Content-Type`: `application/json`
   - **Body Content Type:** JSON
   - **Specify Body:** Using JSON
   - **Body:**
     ```json
     {
       "messaging_product": "whatsapp",
       "recipient_type": "individual",
       "to": "919827952804",
       "type": "text",
       "text": {
         "body": "={{ 'Ticket: ' + $('Create Ticket').item.json.ticket_id + ' | Issue: ' + $('Set Fields').item.json.issue_type + ' | Agent will contact you within 2 hours.' }}"
       }
     }
     ```
5. Click **Save**

> **Sandbox note:** The recipient number must have opted in by sending any WhatsApp message
> to the 360dialog sandbox number shown in your dashboard. Messages arrive in WhatsApp
> under **Updates → Business** tab, not the main Chats tab.

---

## Step 6 — Add "Callback to FastAPI" HTTP Request Node

1. Click **"+"** after Send WhatsApp
2. Add another **HTTP Request** node
3. Rename it: **"Callback to FastAPI"**
4. Configure:
   - **Method:** POST
   - **URL:** `https://sarvam-telecom-bot-production.up.railway.app/n8n/webhook`
   - **Body Content Type:** JSON
   - **Specify Body:** Using JSON
   - **Body:**
     ```json
     {
       "call_id": "={{ $('Set Fields').item.json.call_id }}",
       "ticket_id": "={{ $('Create Ticket').item.json.ticket_id }}",
       "status": "escalated"
     }
     ```
5. Click **Save**

---

## Step 7 — Activate the Workflow

1. Connect all nodes: `[Webhook] → [Set Fields] → [Create Ticket] → [Send WhatsApp] → [Callback to FastAPI]`
2. Click **Save** (Ctrl+S)
3. Toggle the **"Active"** switch (top right) to **ON**

---

## Step 8 — Test the Workflow

Run this to simulate an escalation:

```bash
curl -X POST https://nrvmhdn.app.n8n.cloud/webhook/escalation \
  -H "Content-Type: application/json" \
  -d '{
    "call_id": "call_test123abc",
    "issue_type": "billing_dispute",
    "user_query": "I was charged wrong amount",
    "bot_response": "I understand your concern. Let me escalate this to our team.",
    "timestamp": "2026-06-15T10:30:00Z"
  }'
```

**Expected result:**
1. n8n executes all 4 nodes successfully
2. `/mock/ticket` returns `{"ticket_id": "TKT-XXXXX", "status": "created"}`
3. WhatsApp notification sent to sandbox number
4. `/n8n/webhook` on Railway backend receives the callback
5. Supabase call record updated with `ticket_id` and `escalated=true`

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Webhook not receiving | Check workflow is toggled ON in n8n Cloud |
| Create Ticket returns 404 | Confirm Railway backend is deployed and `/mock/ticket` route exists |
| WhatsApp not received | Re-opt-in: send any message from recipient number to 360dialog sandbox number |
| WhatsApp messages in wrong tab | Check **Updates → Business** tab in WhatsApp, not main Chats |
| FastAPI callback fails | Check Railway logs; confirm `/n8n/webhook` route is reachable |
| n8n expression errors | Do NOT use `{{ }}` alone in JSON body — use `={{ }}` syntax for expressions |
