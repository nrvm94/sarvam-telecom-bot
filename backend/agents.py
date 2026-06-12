import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "mock_customers.json")


@dataclass
class CallContext:
    call_id: str
    customer: dict
    conversation_history: list
    current_query: str
    language: str
    turn_count: int
    issue_type: str
    sarvam_client: Any
    rag_engine: Any
    escalated: bool = False


# ---------------------------------------------------------------------------
# CustomerProfileAgent
# ---------------------------------------------------------------------------

class CustomerProfileAgent:

    def load_customer(self, phone: str) -> dict:
        logger.debug("[CustomerAgent] Looking up phone=%r in DB=%s", phone, DB_PATH)
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                db = json.load(f)
            phone_key = str(phone).strip()
            found_in_db = phone_key in db
            customer = db.get(phone_key, db.get("_unknown"))
            name = customer.get("name", "Valued Customer")
            segment = customer.get("segment", "unknown")
            if found_in_db:
                logger.info(
                    "[CustomerAgent] Customer found | phone=%r | name=%r | segment=%s | churn=%s",
                    phone_key, name, segment, customer.get("churn_risk", "unknown"),
                )
            else:
                logger.info(
                    "[CustomerAgent] Phone not in DB — using _unknown fallback | phone=%r",
                    phone_key,
                )
            return customer
        except Exception as e:
            logger.error("[CustomerAgent] Failed to load customer | phone=%r | error=%s", phone, e, exc_info=True)
            return {
                "name": "Valued Customer",
                "segment": "unknown",
                "churn_risk": "unknown",
                "plan": None,
                "add_ons": [],
                "complaint_history": [],
            }

    def build_customer_context_string(self, customer: dict) -> str:
        try:
            if customer.get("segment") == "unknown":
                return "Customer: Unidentified caller. Provide generic Airtel support."

            name = customer.get("name", "Customer")
            cid = customer.get("customer_id", "N/A")
            segment = customer.get("segment", "unknown")
            tenure = customer.get("tenure_years", 0)
            churn = customer.get("churn_risk", "unknown")
            plan = customer.get("plan") or {}
            addons = customer.get("add_ons", [])
            complaints = customer.get("complaint_history", [])

            lines = [f"Customer: {name} (ID: {cid})"]
            lines.append(f"Segment: {segment} | Tenure: {tenure} years | Churn Risk: {churn}")

            if "monthly_data" in plan:
                used = plan.get("used_data_gb", 0)
                total = plan.get("monthly_data", "?")
                lines.append(f"Plan: {plan.get('name', 'N/A')} | Data: {used}GB used of {total}")
                lines.append(f"5G: {'Yes' if plan.get('is_5g') else 'No'} | Voice: {plan.get('voice', 'N/A')}")
                lines.append(f"Validity: {plan.get('validity', 'N/A')}")

                bill = customer.get("last_bill", {})
                if bill:
                    paid_str = "PAID" if bill.get("paid") else "UNPAID"
                    lines.append(f"Last Bill: Rs.{bill.get('amount', 0)} ({paid_str}) - Due: {bill.get('due_date', 'N/A')}")
                    bd = bill.get("breakdown", {})
                    if bd:
                        parts = [f"Base Rs.{bd.get('base_plan', 0)}"]
                        if bd.get("add_ons"):
                            parts.append(f"Add-ons Rs.{bd['add_ons']}")
                        if bd.get("late_fee"):
                            parts.append(f"Late fee Rs.{bd['late_fee']}")
                        if bd.get("roaming"):
                            parts.append(f"Roaming Rs.{bd['roaming']}")
                        lines.append("Breakdown: " + " + ".join(parts))
                    if bill.get("dispute_raised"):
                        lines.append(f"DISPUTE RAISED: {bill.get('dispute_reason', 'N/A')}")

            elif "data_per_day" in plan:
                used_today = plan.get("used_data_today_gb", 0)
                limit = plan.get("data_per_day", "?")
                lines.append(f"Plan: {plan.get('name', 'N/A')} | Daily data: {used_today}GB used of {limit}")
                lines.append(f"Validity ends: {plan.get('validity_ends', 'N/A')} | Voice: {plan.get('voice', 'N/A')}")

                recharge = customer.get("last_recharge", {})
                if recharge:
                    lines.append(f"Last Recharge: Rs.{recharge.get('amount', 0)} on {recharge.get('date', 'N/A')}")

            if addons:
                lines.append("Add-ons: " + ", ".join(addons))
            else:
                lines.append("Add-ons: None")

            if complaints:
                lines.append("Open Complaints: " + " | ".join(complaints))
            else:
                lines.append("Open Complaints: None")

            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[CustomerAgent] Failed to build context string: {e}")
            return "Customer context unavailable."


# ---------------------------------------------------------------------------
# Deterministic account-answer builder (Plan B / safety net).
# ---------------------------------------------------------------------------

def _fmt_date(iso: str) -> str:
    """'2026-06-15' -> '15 June' (TTS-friendly); pass through anything unparseable."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %B")
    except Exception:
        return iso or ""


def build_account_answer(issue_type: str, customer: dict, language: str) -> str | None:
    """Return a complete, speakable account answer, or None if not account-backed."""
    if language == "mr":
        return None  # fall through to LLM, which has the Marathi system prompt
    plan = customer.get("plan") or {}
    if not plan:
        return None
    hi = language == "hi"
    postpaid = "monthly_data" in plan

    # ---- PLAN DETAILS ----
    if issue_type == "plan_query":
        if postpaid:
            total = int(str(plan.get("monthly_data", "0")).replace("GB", "").strip() or 0)
            used = plan.get("used_data_gb", 0)
            fiveg_en = " It includes 5G access." if plan.get("is_5g") else ""
            fiveg_hi = " इसमें 5G भी शामिल है।" if plan.get("is_5g") else ""
            if hi:
                return (f"आप {plan['name']} plan पर हैं, जिसमें {total} GB monthly data और "
                        f"{plan.get('voice', '')} calling है।{fiveg_hi} "
                        f"अभी तक आपने {used} GB use किया है और plan {_fmt_date(plan.get('validity', ''))} तक valid है।")
            return (f"You're on the {plan['name']} plan with {total} GB of monthly data and "
                    f"{plan.get('voice', '')} calling.{fiveg_en} "
                    f"You've used {used} GB so far, and it's valid until {_fmt_date(plan.get('validity', ''))}.")
        else:  # prepaid
            if hi:
                return (f"आप {plan['name']} plan पर हैं, जिसमें रोज़ाना {plan.get('data_per_day', '')} data और "
                        f"{plan.get('voice', '')} calling है। यह {_fmt_date(plan.get('validity_ends', ''))} तक valid है।")
            return (f"You're on the {plan['name']} plan with {plan.get('data_per_day', '')} of data per day and "
                    f"{plan.get('voice', '')} calling. It's valid until {_fmt_date(plan.get('validity_ends', ''))}.")

    # ---- DATA REMAINING (data_query + balance_check) ----
    if issue_type in ("data_query", "balance_check"):
        if postpaid:
            total = int(str(plan.get("monthly_data", "0")).replace("GB", "").strip() or 0)
            used = plan.get("used_data_gb", 0)
            remaining = max(0, total - used)
            if hi:
                return (f"आपके plan में {total} GB monthly data है। आपने {used} GB use किया है, "
                        f"लगभग {remaining} GB बचा है। Validity {_fmt_date(plan.get('validity', ''))} तक है।")
            return (f"Your plan includes {total} GB of monthly data. You've used {used} GB, "
                    f"so about {remaining} GB is remaining, valid until {_fmt_date(plan.get('validity', ''))}.")
        else:  # prepaid
            used = plan.get("used_data_today_gb", 0)
            if hi:
                return (f"आपको रोज़ाना {plan.get('data_per_day', '')} data मिलता है। आज आपने {used} GB use किया है। "
                        f"Pack {_fmt_date(plan.get('validity_ends', ''))} तक valid है।")
            return (f"You get {plan.get('data_per_day', '')} of data per day. Today you've used {used} GB. "
                    f"Your pack is valid until {_fmt_date(plan.get('validity_ends', ''))}.")

    # ---- BILLING ----
    if issue_type == "billing_query":
        bill = customer.get("last_bill")
        if not bill:  # prepaid: no bill, show last recharge
            rc = customer.get("last_recharge")
            if not rc:
                return None
            if hi:
                return (f"आप prepaid plan पर हैं, इसलिए कोई monthly bill नहीं है। "
                        f"आपका last recharge {rc.get('amount', 0)} रुपये का था, {_fmt_date(rc.get('date', ''))} को।")
            return (f"You're on a prepaid plan, so there's no monthly bill. "
                    f"Your last recharge was {rc.get('amount', 0)} rupees on {_fmt_date(rc.get('date', ''))}.")
        amt = bill.get("amount", 0)
        status_hi = "अभी तक unpaid है" if not bill.get("paid") else "paid हो चुका है"
        status_en = "currently unpaid" if not bill.get("paid") else "already paid"
        bd = bill.get("breakdown", {})
        seg_en, seg_hi = [], []
        if bd.get("base_plan"):
            seg_en.append(f"base plan {bd['base_plan']} rupees"); seg_hi.append(f"base plan {bd['base_plan']} रुपये")
        if bd.get("add_ons"):
            seg_en.append(f"add-ons {bd['add_ons']} rupees"); seg_hi.append(f"add-ons {bd['add_ons']} रुपये")
        if bd.get("late_fee"):
            seg_en.append(f"a late fee of {bd['late_fee']} rupees"); seg_hi.append(f"late fee {bd['late_fee']} रुपये")
        if bd.get("roaming"):
            seg_en.append(f"roaming {bd['roaming']} rupees"); seg_hi.append(f"roaming {bd['roaming']} रुपये")
        dispute_en = f" A dispute is on record: {bill['dispute_reason']}." if bill.get("dispute_raised") else ""
        dispute_hi = f" एक dispute भी record पर है: {bill['dispute_reason']}।" if bill.get("dispute_raised") else ""
        if hi:
            return (f"आपका latest bill {amt} रुपये है, due date {_fmt_date(bill.get('due_date', ''))}, और यह {status_hi}। "
                    f"इसमें {', '.join(seg_hi)} शामिल हैं।{dispute_hi}")
        return (f"Your latest bill is {amt} rupees, due on {_fmt_date(bill.get('due_date', ''))}, and it's {status_en}. "
                f"It includes {', '.join(seg_en)}.{dispute_en}")

    return None


# ---------------------------------------------------------------------------
# Tool-calling for account queries (Plan A — production-grade path).
# ---------------------------------------------------------------------------

ACCOUNT_ISSUE_TYPES = {"plan_query", "balance_check", "data_query", "billing_query"}

ACCOUNT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_account_details",
            "description": (
                "Look up the authenticated caller's Airtel plan: plan name, monthly/daily "
                "data allowance, data used, validity, voice benefits, 5G status, and active "
                "add-ons. Call this for questions about the plan, data balance, or usage."
            ),
            "parameters": {
                "type": "object",
                "properties": {"phone": {"type": "string", "description": "Caller's 10-digit number"}},
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bill_details",
            "description": (
                "Look up the authenticated caller's latest Airtel bill: amount, due date, "
                "paid/unpaid status, line-item breakdown, and any dispute on record. Call "
                "this for billing and payment questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {"phone": {"type": "string", "description": "Caller's 10-digit number"}},
                "required": ["phone"],
            },
        },
    },
]

VOICE_SYSTEM_PROMPT_EN = (
    "You are Priya, an Airtel customer support voice agent. You have tools to look up the "
    "caller's account and bill — use them whenever the caller asks about their plan, data, "
    "balance, or bill, then answer using the returned data. This is a PHONE CALL: reply in "
    "2-3 short spoken sentences of plain text. Absolutely NO markdown, tables, bullet points, "
    "headings, or symbols. Never tell the customer to check an app or website — you have their "
    "data, so just tell them the answer directly. Speak naturally, as if talking out loud."
)

VOICE_SYSTEM_PROMPT_HI = (
    "Aap Priya hain, Airtel ki customer support voice agent. Aapke paas caller ke account aur "
    "bill dekhne ke tools hain — jab caller apne plan, data, balance ya bill ke baare mein "
    "poochhe to tools use karke returned data se jawab dein. Yeh ek PHONE CALL hai: 2-3 chhote "
    "bole jaane wale sentences mein plain text mein jawab dein. Koi markdown, table, bullet, "
    "heading ya symbol bilkul nahi. Customer ko kabhi app ya website check karne ko mat kahein "
    "— aapke paas data hai, isliye seedha jawab batayein. Natural, bolte hue andaz mein."
)

REFUSAL_MARKERS = (
    "don't have access", "do not have access", "cannot access", "can't access",
    "unable to access", "access your account", "access to your", "myairtel",
    "my airtel app", "airtel thanks", "check the app", "log into", "logging into",
    "main aapke account", "account tak pahunch", "app par check", "app mein check",
)


def _looks_like_refusal(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in REFUSAL_MARKERS)


def _voice_clean(text: str) -> str:
    """Strip markdown so TTS doesn't read tables/bullets/symbols aloud."""
    t = text or ""
    t = t.replace("|", " ")
    t = re.sub(r"[#*`_>]", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"\n{2,}", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def _execute_account_tool(name: str, customer: dict) -> dict:
    """Run the tool against the AUTHENTICATED caller's record (ignore model-supplied
    phone for safety — the caller was identified at call start)."""
    plan = customer.get("plan") or {}
    if name == "get_bill_details":
        bill = customer.get("last_bill")
        if bill:
            return {"last_bill": bill}
        return {
            "last_bill": None,
            "last_recharge": customer.get("last_recharge"),
            "note": "Prepaid line — no monthly bill.",
        }
    # default: get_account_details
    return {
        "name": customer.get("name"),
        "plan": plan,
        "add_ons": customer.get("add_ons", []),
        "segment": customer.get("segment"),
    }


# ---------------------------------------------------------------------------
# QueryResolverAgent
# ---------------------------------------------------------------------------

class QueryResolverAgent:

    def format_conversation_history(self, history: list) -> str:
        if not history:
            return ""
        recent = history[-10:]
        parts = []
        for turn in recent:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            label = "Customer said" if role == "user" else "Agent responded"
            parts.append(f"{label}: {content}")
        return "\n".join(parts)

    async def _answer_account_query(self, context: CallContext, lang: str):
        """Return a deterministic account answer from the mock DB. No LLM involved."""
        answer = build_account_answer(context.issue_type, context.customer, lang)
        if answer:
            logger.info(
                "[QueryAgent] Deterministic account answer | issue=%s | lang=%s",
                context.issue_type, lang,
            )
            return {"response": answer, "rag_context_used": "", "customer_context_used": answer}
        return None

    async def run(self, context: CallContext) -> dict:
        t0 = time.time()
        try:
            lang = context.language or "hi"
            customer_found = context.customer.get("segment") != "unknown"
            taking_account_path = customer_found and context.issue_type in ACCOUNT_ISSUE_TYPES

            logger.info(
                "[QueryAgent] Entering run | call_id=%s | lang=%s | found=%s | segment=%s | issue=%s | account_path=%s",
                context.call_id, lang, customer_found, context.customer.get("segment"),
                context.issue_type, taking_account_path,
            )

            # Account questions from an identified caller → deterministic path
            if taking_account_path:
                logger.info(
                    "[QueryAgent] Taking deterministic account path | call_id=%s | issue=%s",
                    context.call_id, context.issue_type,
                )
                res = await self._answer_account_query(context, lang)
                if res is not None:
                    elapsed_ms = (time.time() - t0) * 1000
                    logger.info(
                        "[QueryAgent] Deterministic answer returned | call_id=%s | elapsed=%.0fms",
                        context.call_id, elapsed_ms,
                    )
                    return res
                logger.info(
                    "[QueryAgent] No deterministic answer — falling through to RAG+LLM | call_id=%s",
                    context.call_id,
                )

            # General / non-account query — RAG + full customer context
            logger.info(
                "[QueryAgent] RAG lookup | call_id=%s | query=%r",
                context.call_id, context.current_query[:80],
            )
            rag_context = await context.rag_engine.query(context.current_query)
            logger.info(
                "[QueryAgent] RAG done | call_id=%s | rag_context_len=%d",
                context.call_id, len(rag_context or ""),
            )
            customer_context = CustomerProfileAgent().build_customer_context_string(context.customer)

            if lang == "hi":
                system_prompt = (
                    "आप Airtel की एक महिला customer support agent हैं। "
                    "हमेशा शुद्ध हिंदी में देवनागरी लिपि में जवाब दें — Roman/Hinglish में नहीं। "
                    "स्त्रीलिंग क्रियापद उपयोग करें (सकती हूँ, करती हूँ, बताती हूँ, रहूँगी, करूँगी आदि)। "
                    "नीचे दिया गया CUSTOMER ACCOUNT DATA authentic और verified है — सीधे इसी data से जवाब दें। "
                    "Customer को कभी app, website, store या 121 पर जाने को न कहें। "
                    "2-3 sentences में सीधा जवाब दें।"
                )
            elif lang == "mr":
                system_prompt = (
                    "महत्त्वाचे: फक्त मराठीतच उत्तर द्या. हिंदी किंवा इंग्रजीत उत्तर देऊ नका, "
                    "जरी आधीचे संभाषण हिंदीत होते तरी. "
                    "तुम्ही Airtel ची एक महिला customer support agent आहात. "
                    "फक्त मराठीत उत्तर द्या — Roman script मध्ये नाही. "
                    "2-3 वाक्यांपेक्षा जास्त नको. "
                    "Customer ला app, website किंवा 121 वर पाठवू नका. "
                    "Customer च्या account data वापरून थेट उत्तर द्या."
                )
            else:
                system_prompt = (
                    "You are a female Airtel customer support agent. "
                    "The customer is speaking English. Respond ONLY in English — never in Hindi, Marathi, or any other language. "
                    "Do NOT say you can only help in Hindi. Do NOT say your purpose is to respond in Hindi. "
                    "If the user asks why you responded in Hindi, apologize and continue in English immediately. "
                    "The CUSTOMER ACCOUNT DATA below is authentic — answer directly from it. "
                    "Reply in 2-3 short spoken sentences, plain text only, no markdown."
                )

            if customer_found:
                user_message = (
                    f"CUSTOMER ACCOUNT DATA:\n{customer_context}\n\n"
                    f"Customer's question: {context.current_query}"
                )
                if rag_context:
                    user_message += f"\n\nGeneral reference:\n{rag_context}"
            else:
                user_message = context.current_query
                if rag_context:
                    user_message += f"\n\n[Reference: {rag_context}]"

            # Prepend an explicit language directive to the user message so the
            # model cannot ignore it (system-prompt-only mandates are insufficient
            # when conversation history contains cross-language content).
            language_label = {"en": "English", "hi": "Hindi", "mr": "Marathi"}.get(lang, "English")
            user_message = (
                f"[IMPORTANT: Customer is speaking {language_label}. Respond ONLY in {language_label}.]\n\n"
                + user_message
            )

            # Only pass history turns in the current language.
            # Hindi history passed to an English LLM call causes the model
            # to respond in Hindi despite the system prompt mandate.
            same_lang_history = [
                {"role": t["role"], "content": t["content"]}
                for t in context.conversation_history[-8:]
                if t.get("language") == lang
            ]
            logger.info(
                "[QueryAgent] LLM call | call_id=%s | lang=%s | history_turns=%d | customer_found=%s",
                context.call_id, lang, len(same_lang_history), customer_found,
            )
            llm_start = time.time()
            response = await context.sarvam_client.generate_response(
                query=user_message,
                context="",
                language=lang,
                history=same_lang_history,
                system_prompt_override=system_prompt,
            )
            llm_elapsed_ms = (time.time() - llm_start) * 1000
            total_elapsed_ms = (time.time() - t0) * 1000
            logger.info(
                "[QueryAgent] LLM done | call_id=%s | llm_elapsed=%.0fms | total_elapsed=%.0fms | response=%r",
                context.call_id, llm_elapsed_ms, total_elapsed_ms, (response or "")[:80],
            )
            return {
                "response": response,
                "rag_context_used": rag_context or "",
                "customer_context_used": customer_context,
            }
        except Exception as e:
            logger.error("[QueryAgent] Error in run | call_id=%s | error=%s", context.call_id, e, exc_info=True)
            fallback = (
                "मुझे अभी आपका जवाब देने में थोड़ी परेशानी हो रही है। कृपया थोड़ी देर बाद try करें।"
                if (context.language or "hi") == "hi"
                else "I'm having trouble answering right now. Please try again in a moment."
            )
            return {"response": fallback, "rag_context_used": "", "customer_context_used": ""}


# ---------------------------------------------------------------------------
# ActionAgent
# ---------------------------------------------------------------------------

ACTION_KEYWORDS = {
    "CANCEL_ADDON": ["cancel", "band karo", "hatao", "remove", "discontinue", "band kar do", "cancel kar do"],
    "SCHEDULE_CALLBACK": ["call back", "callback", "baad mein call", "agent bulao", "call me back", "call karo"],
    "SEND_BILL_WHATSAPP": ["bill bhejo", "send bill", "whatsapp pe bhejo", "copy chahiye", "bill send karo", "bill whatsapp"],
    "RECHARGE_ACCOUNT": ["recharge", "recharge karo", "top up", "topup", "balance add"],
    "ACTIVATE_DATA_PACK": ["data pack", "extra data", "data add", "data chahiye", "more data", "data khatam"],
    "RAISE_COMPLAINT": ["complaint", "shikayat", "report", "issue raise", "complaint karo", "complaint darj"],
}

ADDON_KEYWORDS = ["disney", "hotstar", "amazon prime", "prime", "wynk", "netflix", "zee5", "sonyliv"]

AFFIRMATIVE = ["haan", "yes", "theek hai", "okay", "ok", "kar do", "haan kar do", "confirm", "bilkul", "zaroor", "sure"]

MOCK_BASE = "http://localhost:5000"

ACTION_ENDPOINTS = {
    "CANCEL_ADDON": "/mock/cancel-addon",
    "SCHEDULE_CALLBACK": "/mock/schedule-callback",
    "SEND_BILL_WHATSAPP": "/mock/send-bill",
    "RECHARGE_ACCOUNT": "/mock/recharge",
    "ACTIVATE_DATA_PACK": "/mock/activate-data-pack",
    "RAISE_COMPLAINT": "/mock/raise-complaint",
}

CONFIRMATION_RESPONSES = {
    "hi": {
        "CANCEL_ADDON": "क्या आप {target} cancel करना चाहते हैं? Confirm करें — हाँ या नहीं?",
        "SCHEDULE_CALLBACK": "क्या आप चाहते हैं कि एक agent आपको call करे? Confirm करें।",
        "SEND_BILL_WHATSAPP": "क्या आप अपना bill WhatsApp पर चाहते हैं? Confirm करें।",
        "RECHARGE_ACCOUNT": "Recharge process करूँ? Confirm करें।",
        "ACTIVATE_DATA_PACK": "Data pack activate करूँ? Confirm करें।",
        "RAISE_COMPLAINT": "Complaint दर्ज करूँ? Confirm करें।",
    },
    "en": {
        "CANCEL_ADDON": "Do you want to cancel {target}? Please confirm — yes or no?",
        "SCHEDULE_CALLBACK": "Do you want an agent to call you back? Please confirm.",
        "SEND_BILL_WHATSAPP": "Do you want your bill sent to WhatsApp? Please confirm.",
        "RECHARGE_ACCOUNT": "Shall I process the recharge? Please confirm.",
        "ACTIVATE_DATA_PACK": "Shall I activate the data pack? Please confirm.",
        "RAISE_COMPLAINT": "Shall I raise a complaint? Please confirm.",
    },
    "mr": {
        "CANCEL_ADDON": "तुम्हाला {target} रद्द करायचे आहे का? कृपया होय किंवा नाही सांगा.",
        "SCHEDULE_CALLBACK": "एका agent ने तुम्हाला call करावा असे तुम्हाला वाटते का? कृपया confirm करा.",
        "SEND_BILL_WHATSAPP": "तुमचे bill WhatsApp वर पाठवायचे का? कृपया confirm करा.",
        "RECHARGE_ACCOUNT": "Recharge process करू का? कृपया confirm करा.",
        "ACTIVATE_DATA_PACK": "Data pack activate करू का? कृपया confirm करा.",
        "RAISE_COMPLAINT": "तक्रार नोंदवू का? कृपया confirm करा.",
    },
}

SUCCESS_RESPONSES = {
    "hi": {
        "CANCEL_ADDON": "{target} cancel request submit हो गई है। 2 घंटे में effective हो जाएगी। Confirmation आपके WhatsApp पर आ जाएगी।",
        "SCHEDULE_CALLBACK": "Callback schedule हो गया है। हमारे agent 2 घंटे में आपको call करेंगे।",
        "SEND_BILL_WHATSAPP": "आपका bill आपके WhatsApp पर भेज दिया गया है।",
        "RECHARGE_ACCOUNT": "Recharge request submit हो गई है। Balance जल्द update हो जाएगा।",
        "ACTIVATE_DATA_PACK": "Data pack activate हो गया है। 1GB 24 घंटे के लिए valid है।",
        "RAISE_COMPLAINT": "आपकी complaint दर्ज हो गई है। Ticket number {complaint_id}। 48 घंटे में resolve हो जाएगी।",
    },
    "en": {
        "CANCEL_ADDON": "{target} cancellation submitted. It will be effective within 2 hours. You'll get a WhatsApp confirmation.",
        "SCHEDULE_CALLBACK": "Callback scheduled. An agent will call you within 2 hours.",
        "SEND_BILL_WHATSAPP": "Your bill has been sent to your WhatsApp.",
        "RECHARGE_ACCOUNT": "Recharge request submitted. Your balance will update shortly.",
        "ACTIVATE_DATA_PACK": "Data pack activated. 1GB added, valid for 24 hours.",
        "RAISE_COMPLAINT": "Complaint raised. Ticket number {complaint_id}. Resolution within 48 hours.",
    },
    "mr": {
        "CANCEL_ADDON": "{target} रद्द करण्याची विनंती सादर केली आहे. 2 तासांत effective होईल. WhatsApp वर confirmation येईल.",
        "SCHEDULE_CALLBACK": "Callback scheduled झाला आहे. आमचे agent 2 तासांत तुम्हाला call करतील.",
        "SEND_BILL_WHATSAPP": "तुमचे bill तुमच्या WhatsApp वर पाठवले गेले आहे.",
        "RECHARGE_ACCOUNT": "Recharge विनंती सादर केली आहे. Balance लवकरच update होईल.",
        "ACTIVATE_DATA_PACK": "Data pack activate झाला आहे. 1GB 24 तासांसाठी valid आहे.",
        "RAISE_COMPLAINT": "तुमची तक्रार नोंदवली गेली आहे. Ticket number {complaint_id}. 48 तासांत resolve होईल.",
    },
}


class ActionAgent:

    def _detect_target(self, text: str, customer: dict) -> str:
        text_lower = text.lower()
        for keyword in ADDON_KEYWORDS:
            if keyword in text_lower:
                addons = customer.get("add_ons", [])
                for addon in addons:
                    if keyword in addon.lower():
                        return addon.split(" - ")[0]
                return keyword.title()
        return ""

    def _last_bot_asked_confirmation(self, history: list) -> bool:
        for turn in reversed(history):
            if turn.get("role") == "assistant":
                content = turn.get("content", "").lower()
                return "confirm" in content or "haan ya nahi" in content or "yes or no" in content
        return False

    def detect_action_intent(self, context: CallContext) -> dict:
        try:
            text = context.current_query.lower()

            for action, keywords in ACTION_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    target = self._detect_target(context.current_query, context.customer)
                    confirmed = (
                        self._last_bot_asked_confirmation(context.conversation_history)
                        and any(aff in text for aff in AFFIRMATIVE)
                    )
                    logger.info(f"[ActionAgent] Detected: {action}, target: {target}, confirmed: {confirmed}")
                    return {"action": action, "target": target, "confirmed": confirmed}

            if any(aff in text for aff in AFFIRMATIVE) and self._last_bot_asked_confirmation(context.conversation_history):
                for turn in reversed(context.conversation_history):
                    if turn.get("role") == "assistant":
                        content = turn.get("content", "").lower()
                        for action in ACTION_KEYWORDS:
                            action_lower = action.lower().replace("_", " ")
                            if action_lower.split()[0] in content:
                                target = self._detect_target(turn.get("content", ""), context.customer)
                                logger.info(f"[ActionAgent] Confirmed from history: {action}")
                                return {"action": action, "target": target, "confirmed": True}
                        break

            return {"action": None}
        except Exception as e:
            logger.error(f"[ActionAgent] detect_action_intent error: {e}")
            return {"action": None}

    async def execute_action(self, action: str, target: str, context: CallContext) -> dict:
        try:
            endpoint = ACTION_ENDPOINTS.get(action, "/mock/raise-complaint")
            url = f"{MOCK_BASE}{endpoint}"
            payload = {
                "action": action,
                "call_id": context.call_id,
                "customer_id": context.customer.get("customer_id"),
                "target": target,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(
                "[ActionAgent] Executing action | call_id=%s | action=%s | target=%r | url=%s",
                context.call_id, action, target, url,
            )
            complaint_id = f"CMP-{random.randint(10000, 99999)}"
            t0 = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    elapsed_ms = (time.time() - t0) * 1000
                    result = await resp.json()
                    complaint_id = result.get("complaint_id", complaint_id)
                    logger.info(
                        "[ActionAgent] Mock server response | action=%s | status=%d | elapsed=%.0fms | complaint_id=%s",
                        action, resp.status, elapsed_ms, complaint_id,
                    )

            lang = context.language or "hi"
            template = SUCCESS_RESPONSES.get(lang, SUCCESS_RESPONSES["hi"]).get(action, "Action completed.")
            message = template.format(target=target or "service", complaint_id=complaint_id)
            logger.info("[ActionAgent] Action completed | call_id=%s | action=%s", context.call_id, action)
            return {"message": message, "success": True}
        except Exception as e:
            logger.error("[ActionAgent] execute_action failed | call_id=%s | action=%s | error=%s", context.call_id, action, e, exc_info=True)
            lang = context.language or "hi"
            fallback = (
                "Action process करने में problem आई। कृपया थोड़ी देर बाद try करें।"
                if lang == "hi"
                else "Could not process the action. Please try again in a moment."
            )
            return {"message": fallback, "success": False}

    def request_confirmation(self, action: str, target: str, language: str) -> str:
        lang = language or "hi"
        template = CONFIRMATION_RESPONSES.get(lang, CONFIRMATION_RESPONSES["hi"]).get(
            action, "Kya aap confirm karna chahte hain?"
        )
        return template.format(target=target or "service")


# ---------------------------------------------------------------------------
# EscalationAgent
# ---------------------------------------------------------------------------

ESCALATION_PHRASES = {
    "en": [
        "escalate", "talk to someone", "speak to agent", "human agent", "real person",
        "supervisor", "manager", "not resolved", "still not working", "connect me to",
        "transfer me", "talk to a person", "want to speak", "speak to a human",
        "talk to human", "senior", "senior agent",
        "real human", "actual person", "not a bot", "talk to a real person",
        "i want human", "human please", "get me an agent", "put me through",
        "connect me", "live agent", "live person",
    ],
    "hi": [
        "escalate karo", "kisi se baat", "manager se baat", "agent chahiye",
        "insaan se baat", "abhi tak resolve nahi", "aage bado", "senior bulao",
        "supervisor chahiye", "transfer karo", "isse solve nahi hua", "senior se baat",
        "kisi insaan", "real agent", "manush se baat",
        "koi real banda chahiye", "machine se nahi baat", "bot se nahi chahiye",
        "seedha baat karni hai", "mujhe agent chahiye", "call transfer karo",
        "kisi aur se baat",
    ],
    # Devanagari transliterations — STT outputs these when customer speaks Hindi/code-switched
    "devanagari": [
        "एस्केलेट", "ह्यूमन एजेंट", "ह्यूमन एजन्ट", "मानव एजेंट",
        "एजेंट से बात", "एजेंट चाहिए", "इंसान से बात", "मैनेजर से बात",
        "सुपरवाइजर", "ट्रांसफर", "सीनियर से बात", "किसी से बात",
        "वास्तविक व्यक्ति", "असली एजेंट", "इंसान चाहिए", "बात करनी है किसी से",
        "एजेंट दो", "ट्रांसफर करो",
    ],
    "mr": [
        "escalate kara", "agent hava", "manushya hava", "supervisor hava",
        "manager shi bola", "dusarya kunashi bola", "ha problem soda",
        "khare manasashi bola", "transfer kara",
    ],
}

HIGH_PRIORITY_ISSUES = [
    "billing_dispute", "unauthorized_charge", "account_security",
    "fraud", "legal_threat", "roaming_dispute",
]

DEFLECTION_PHRASES = [
    "call 121", "visit store", "contact us", "check app", "check the app",
    "visit airtel store", "app par check", "store mein jaein",
]

ROUTING_MAP = {
    "billing_dispute": "billing_team",
    "unauthorized_charge": "billing_team",
    "roaming_dispute": "billing_team",
    "network_issue": "technical_team",
    "account_security": "security_team",
    "customer_requested": "general_support",
    "churn_risk": "retention_team",
    "unresolved": "general_support",
    "open_complaint": "billing_team",
}

ESCALATION_RESPONSES = {
    "hi": {
        "billing_team": "{name} जी, मैंने आपका billing dispute हमारी senior billing team को escalate कर दिया है। Priority {priority}। हमारे specialist 2 घंटे में आपको call करेंगे। आपका ticket number {ticket_id} है।",
        "technical_team": "{name} जी, मैंने आपकी network issue technical team को भेज दी है। Engineer 4 घंटे में contact करेंगे। Ticket: {ticket_id}।",
        "retention_team": "{name} जी, आप हमारे valued customer हैं। मैंने आपकी request retention team को दी है जो special offer के साथ आपसे contact करेंगे। Ticket: {ticket_id}।",
        "security_team": "{name} जी, मैंने आपका account security team को escalate कर दिया है। 30 मिनट में call आएगी। Ticket: {ticket_id}।",
        "general_support": "{name} जी, मैंने आपका issue escalate कर दिया है। हमारे agent 2 घंटे में call करेंगे। Ticket: {ticket_id}।",
    },
    "en": {
        "billing_team": "{name}, I've escalated your billing dispute to our senior billing specialist with {priority} priority. They will call you within 2 hours. Your ticket number is {ticket_id}.",
        "technical_team": "{name}, I've raised a technical complaint. Our engineer will contact you within 4 hours. Ticket: {ticket_id}.",
        "retention_team": "{name}, you're a valued customer. I've connected you with our retention team who will call with a special offer. Ticket: {ticket_id}.",
        "security_team": "{name}, I've escalated your account concern to our security team. You'll receive a call within 30 minutes. Ticket: {ticket_id}.",
        "general_support": "{name}, I've escalated your issue. An agent will call you within 2 hours. Ticket: {ticket_id}.",
    },
    "mr": {
        "billing_team": "{name}, maine tumcha billing dispute senior billing team la escalate kela ahe. Priority {priority}. Specialist 2 taasaat tumhala call kareel. Ticket: {ticket_id}.",
        "technical_team": "{name}, maine tumchi network issue technical team la pathavli ahe. Engineer 4 taasaat contact karel. Ticket: {ticket_id}.",
        "retention_team": "{name}, tumhi amha valued customer ahat. Maine tumchi request retention team la dili ahe. Ticket: {ticket_id}.",
        "security_team": "{name}, maine tumcha account security team la escalate kela ahe. 30 minutat call yeil. Ticket: {ticket_id}.",
        "general_support": "{name}, maine tumcha issue escalate kela ahe. Agent 2 taasaat call kareel. Ticket: {ticket_id}.",
    },
}

FRUSTRATED_WORDS = [
    "angry", "disgusting", "worst", "cheating", "fraud", "terrible", "horrible",
    "gussa", "bakwaas", "cheat", "thag", "paisa wapas", "ghatiya", "bekar",
    "pathetic", "useless", "ridiculous",
]

RESOLUTION_QUESTION_PHRASES = [
    "solve ho gayi", "issue resolved", "problem solve", "madad mili",
    "problem theek", "your issue been resolved", "has been resolved",
    "kya aapki problem", "kya issue fix",
]

DENIAL_PHRASES = [
    "no", "nahi", "nope", "not yet", "still", "same",
    "abhi bhi", "nahi hua", "solve nahi", "still not", "not resolved",
    "problem hai", "same problem", "wahi problem",
]


class EscalationAgent:

    async def should_escalate(self, context: CallContext) -> dict:
        try:
            text = context.current_query.lower()
            lang = context.language or "hi"

            phrases = (
                ESCALATION_PHRASES.get("en", [])
                + ESCALATION_PHRASES.get("hi", [])
                + ESCALATION_PHRASES.get("devanagari", [])
                + ESCALATION_PHRASES.get("mr", [])
            )
            if any(phrase in text for phrase in phrases):
                routing = ROUTING_MAP.get(context.issue_type, "general_support")
                priority = self._get_priority(context)
                logger.info(f"[EscalationAgent] Trigger: customer_requested → {routing} {priority}")
                return {"escalate": True, "reason": "customer_requested", "routing": routing, "priority": priority}

            if context.issue_type in HIGH_PRIORITY_ISSUES:
                routing = ROUTING_MAP.get(context.issue_type, "general_support")
                priority = self._get_priority(context)
                logger.info(f"[EscalationAgent] Trigger: high_priority_issue {context.issue_type} → {routing} {priority}")
                return {"escalate": True, "reason": "complex_issue", "routing": routing, "priority": priority}

            if context.customer.get("complaint_history") and context.turn_count >= 2:
                routing = ROUTING_MAP.get("open_complaint", "billing_team")
                priority = self._get_priority(context)
                logger.info(f"[EscalationAgent] Trigger: open_complaint → {routing} {priority}")
                return {"escalate": True, "reason": "open_complaint", "routing": routing, "priority": priority}

            churn = context.customer.get("churn_risk", "low")
            if churn in ("high", "very_high") and context.turn_count >= 3:
                routing = "retention_team"
                priority = "P1"
                logger.info(f"[EscalationAgent] Trigger: churn_risk={churn} → retention_team P1")
                return {"escalate": True, "reason": "churn_risk", "routing": routing, "priority": priority}

            if context.turn_count >= 3:
                bot_turns = [t["content"].lower() for t in context.conversation_history if t.get("role") == "assistant"]
                deflection_count = sum(
                    1 for turn in bot_turns[-2:]
                    if any(phrase in turn for phrase in DEFLECTION_PHRASES)
                )
                if deflection_count >= 2:  # BOTH last 2 turns must have deflected
                    logger.info(f"[EscalationAgent] Trigger: bot_failing (deflection detected) → general_support")
                    return {"escalate": True, "reason": "unresolved", "routing": "general_support", "priority": self._get_priority(context)}

            # Condition 6: Customer says issue NOT resolved after bot confirmed resolution
            if context.turn_count >= 2 and context.conversation_history:
                last_bot = next(
                    (t["content"].lower() for t in reversed(context.conversation_history)
                     if t.get("role") == "assistant"), ""
                )
                if any(p in last_bot for p in RESOLUTION_QUESTION_PHRASES):
                    if any(p in text for p in DENIAL_PHRASES):
                        logger.info(f"[EscalationAgent] Trigger: issue_not_resolved → general_support")
                        return {"escalate": True, "reason": "unresolved", "routing": "general_support",
                                "priority": self._get_priority(context)}

            logger.debug(
                "[EscalationAgent] No escalation condition met | call_id=%s | turn=%d | issue=%s | churn=%s",
                context.call_id, context.turn_count, context.issue_type,
                context.customer.get("churn_risk", "unknown"),
            )
            return {"escalate": False, "reason": None, "routing": None, "priority": None}
        except Exception as e:
            logger.error("[EscalationAgent] should_escalate error | call_id=%s | error=%s", context.call_id, e, exc_info=True)
            return {"escalate": False, "reason": None, "routing": None, "priority": None}

    def _get_priority(self, context: CallContext) -> str:
        segment = context.customer.get("segment", "unknown")
        churn = context.customer.get("churn_risk", "low")
        if segment == "postpaid_premium":
            return "P1"
        if segment == "postpaid_standard" and churn in ("high", "very_high"):
            return "P1"
        if segment == "postpaid_standard":
            return "P2"
        if segment == "prepaid_regular":
            return "P2"
        return "P3"

    def build_escalation_payload(self, context: CallContext, routing: str, priority: str) -> dict:
        try:
            return {
                "call_id": context.call_id,
                "customer_id": context.customer.get("customer_id"),
                "customer_name": context.customer.get("name", "Valued Customer"),
                "customer_phone": context.customer.get("phone", ""),
                "customer_segment": context.customer.get("segment", "unknown"),
                "issue_type": context.issue_type,
                "priority": priority,
                "routing": routing,
                "conversation_summary": self.build_conversation_summary(context),
                "customer_sentiment": self.detect_sentiment(context),
                "recommended_action": self._get_recommended_action(routing, context),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"[EscalationAgent] build_escalation_payload error: {e}")
            return {"call_id": context.call_id, "issue_type": context.issue_type}

    def build_conversation_summary(self, context: CallContext) -> str:
        try:
            if not context.conversation_history:
                return f"Customer called about {context.issue_type}. No prior conversation recorded."

            user_turns = [t["content"] for t in context.conversation_history if t.get("role") == "user"]
            bot_turns = [t["content"] for t in context.conversation_history if t.get("role") == "assistant"]

            first_query = user_turns[0] if user_turns else context.current_query
            last_bot = bot_turns[-1] if bot_turns else "No bot response yet."

            customer_name = context.customer.get("name", "Customer")
            summary = (
                f"{customer_name} called about: {first_query[:120]}. "
                f"Issue type: {context.issue_type}. "
                f"Last bot response: {last_bot[:120]}. "
                f"Total turns: {context.turn_count}."
            )
            return summary
        except Exception as e:
            logger.error(f"[EscalationAgent] build_conversation_summary error: {e}")
            return f"Customer called about {context.issue_type}."

    def detect_sentiment(self, context: CallContext) -> str:
        try:
            all_text = " ".join(
                t.get("content", "") for t in context.conversation_history if t.get("role") == "user"
            ).lower()
            all_text += " " + context.current_query.lower()
            if any(word in all_text for word in FRUSTRATED_WORDS):
                return "frustrated"
            return "neutral"
        except Exception:
            return "neutral"

    def _get_recommended_action(self, routing: str, context: CallContext) -> str:
        actions = {
            "billing_team": "Review bill details and waive disputed charges if valid",
            "technical_team": "Run network diagnostics for customer location",
            "retention_team": "Offer plan upgrade or loyalty discount",
            "security_team": "Verify account ownership and check for unauthorized access",
            "general_support": "Review conversation history and resolve customer concern",
        }
        return actions.get(routing, "Review and resolve customer issue")

    async def generate_escalation_response(
        self, routing: str, language: str, customer_name: str, ticket_id: str, priority: str = "P2"
    ) -> str:
        try:
            lang = language or "hi"
            first_name = (
                customer_name.split()[0]
                if customer_name and customer_name != "Valued Customer"
                else ("Aap" if lang in ("hi", "mr") else "You")
            )
            templates = ESCALATION_RESPONSES.get(lang, ESCALATION_RESPONSES["hi"])
            template = templates.get(routing, templates["general_support"])
            return template.format(name=first_name, priority=priority, ticket_id=ticket_id)
        except Exception as e:
            logger.error(f"[EscalationAgent] generate_escalation_response error: {e}")
            return (
                f"आपका issue escalate कर दिया गया है। Ticket: {ticket_id}। Agent 2 घंटे में call करेंगे।"
                if (language or "hi") == "hi"
                else f"Your issue has been escalated. Ticket: {ticket_id}. An agent will call within 2 hours."
            )
