# Sarvam AI — Pre-Sales Engineer Assignment Brief

## Source: Email from Ashok Vishnoi (ashok@sarvam.ai)
**Date received:** Tue, Jun 9, 2026  
**Deadline:** 7 days from receipt → **June 16, 2026**  
**Role:** Solution Architect (Pre-Sales Engineer)  
**CC:** Chandana (Sarvam AI)

### Email Summary
> "Pick one of the following enterprise use cases and build a voice bot, an agentic workflow, or both — using Sarvam AI's APIs or other platforms as a core part of the solution."

**Chosen use case:** D2C Customer Support — Post-purchase voice support for **Airtel** (telecom), with OMS-style integration and human escalation.

### Submission Format (from email)
- **Option A:** Live website with shareable URL
- **Option B:** GitHub repo with README, architecture, Sarvam APIs used
- **Option C:** Video walkthrough (3–5 min, show 2 Indian languages)

**Submission email:**
- To: ashok@sarvam.ai (reply to original thread)
- Subject: `[Pre-Sales Assignment] Neerav Mahadane — D2C Customer Support`

---

## Source: Official Assignment PDF (Sarvam_PreSales_Assignment)

### Required Deliverables

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Working solution (voice bot + agentic workflow) | Required | Code + README |
| Demo video (3–5 min, multilingual) | Required | User will record |
| Architecture diagram | Required | `/docs/ARCHITECTURE.md` |
| Business write-up / slide deck (1–2 pages or 8 slides) | Required | `/docs/Sarvam_AI_Airtel_VoiceBot_Neerav_Mahadane.pdf` |
| Post-call analytics pipeline | Optional | Batch STT + diarization + LLM analysis |
| Telephony integration (Plivo/Twilio) | Optional | Actual phone call demo |

### Repository Structure (per PDF spec)
```
README.md          — Setup, architecture overview, demo link, Sarvam APIs used
/src               — All solution code (requirements.txt / package.json / .env.example)
/docs              — Business write-up (PDF or .md) + architecture diagram
Demo video link    — Loom, YouTube, or Google Drive (3–5 min)
```

### Minimum Requirements Checklist
- [x] Working demo: runnable locally or via shareable link
- [x] Uses Sarvam APIs meaningfully: STT + LLM + TTS as core pipeline
- [x] Real enterprise context: Airtel customer support (specific, credible)
- [x] Multilingual (voice bot): Hindi + English; code-mixing (Hinglish) supported
- [x] Clear README: setup, architecture, which Sarvam APIs are used and why

### Business Write-Up Must Cover (Section 03 of PDF)
1. **The Problem** — operational pain point with real numbers
2. **Why AI** — vs current approach; end user context and digital literacy
3. **Why Sarvam** — Indian language models, code-mixing, low latency, data sovereignty
4. **Architecture Summary** — non-technical diagram / description
5. **ROI / Business Case** — cost savings with stated assumptions
6. **Limitations & Next Steps** — PoC gaps + 90-day rollout plan

### Sarvam APIs Referenced in PDF
| API | What it provides |
|-----|-----------------|
| STT — Saaras v3 (saarika) | Speech-to-text across 11 Indian languages + English |
| TTS — Bulbul v3 | Text-to-speech with 37+ Indian voices |
| Chat Completions | LLM inference via sarvam-105b |
| Translate | Text translation across 22+ Indian languages |

### Agentic Workflow Tools Referenced
- n8n (used ✅), Google ADK, LangGraph, CrewAI, Make/Zapier, Custom FastAPI

### Scoring Signals (from PDF)
- Depth and specificity over breadth
- India-specific capabilities solving India-specific problems
- Code-mixing (Hinglish/Tanglish) is a strong plus
- The pre-sales business write-up must "speak business, not just engineering"
- Generic demos without a clear customer context will not score well

---

## What We Built

**Use case:** Airtel D2C telecom support — customer calls in, speaks Hindi or English, gets instant answers about balance/plans/network, with auto-escalation to human agent via WhatsApp for complex issues.

**Stack:**
- STT: Sarvam Saaras v3 (saaras:v3)
- LLM: Sarvam sarvam-105b (OpenAI-compatible endpoint)
- TTS: Sarvam Bulbul v3 (bulbul:v3), voice: ritu / rahul
- RAG: ChromaDB + sentence-transformers (21 Airtel KB docs)
- Agentic workflow: n8n → mock ticket system → 360dialog WhatsApp
- DB: Supabase (PostgreSQL) for call logs
- Frontend: React 18 + Vite + TailwindCSS
- Backend: FastAPI + Python

**GitHub:** https://github.com/nrvm94/sarvam-telecom-bot
