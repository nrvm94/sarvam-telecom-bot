"""
Conversation Manager
Handles escalation detection and issue classification for Airtel support calls.
"""

import logging

logger = logging.getLogger(__name__)

# Keywords that trigger escalation to a human agent
ESCALATION_KEYWORDS = [
    "dispute",
    "complaint",
    "escalate",
    "not working",
    "error",
    "problem",
    "wrong charge",
    "refund",
    "cancel",
    "port",
    "disconnect",
    "legal",
    "consumer court",
    # Hindi keywords
    "shikayat",      # complaint
    "galat charge",  # wrong charge
    "wapas",         # return/refund
    "band karo",     # stop/cancel
    "kaam nahi",     # not working
]

# Issue classification keyword map
ISSUE_KEYWORDS = {
    "balance_check": ["balance", "bakaya", "kitna hai", "kitna bacha"],
    "plan_query": ["plan", "offer", "pack", "validity", "recharge", "tariff"],
    "data_query": ["data", "internet", "speed", "4g", "5g", "mb", "gb", "net"],
    "billing_query": ["bill", "charge", "payment", "invoice", "amount due"],
    "billing_dispute": ["dispute", "wrong charge", "extra charge", "galat", "overcharge"],
    "port_request": ["port", "mnp", "number portability", "porting"],
}


class ConversationManager:
    """
    Analyses conversation turns for escalation signals and issue categorisation.
    """

    async def detect_escalation(self, query: str, response: str) -> bool:
        """
        Check if the conversation turn indicates the customer needs human support.

        Args:
            query: Customer's transcribed utterance.
            response: Bot's generated response.

        Returns:
            True if escalation is warranted, False otherwise.
        """
        combined = (query + " " + response).lower()

        for keyword in ESCALATION_KEYWORDS:
            if keyword.lower() in combined:
                logger.info(
                    "Escalation triggered | keyword=%r | query=%r",
                    keyword,
                    query[:80],
                )
                return True

        logger.debug("No escalation detected | query=%r", query[:80])
        return False

    async def classify_issue(self, query: str) -> str:
        """
        Categorise the customer's issue into a predefined bucket.

        Args:
            query: Customer's transcribed utterance.

        Returns:
            Issue type string (e.g., "billing_dispute", "data_query").
        """
        q_lower = query.lower()

        for issue_type, keywords in ISSUE_KEYWORDS.items():
            for kw in keywords:
                if kw in q_lower:
                    logger.info(
                        "Issue classified | type=%s | keyword=%r | query=%r",
                        issue_type,
                        kw,
                        query[:80],
                    )
                    return issue_type

        logger.info("Issue classified | type=general_query | query=%r", query[:80])
        return "general_query"
