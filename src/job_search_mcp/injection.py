"""Flag posting text that looks like instructions aimed at an AI.

We cannot make hostile text harmless by editing it, and removing it could
change what the posting means. So we (1) never act on posting text, (2) keep it
in clearly labelled data fields only, and (3) flag suspicious text here so Claude
and the user are warned. This is a tripwire, not a guarantee: a clever attacker
can phrase an instruction we don't recognise. Treat all posting text as data.
"""

from __future__ import annotations

import re

FLAG = "possible_prompt_injection"

_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|preceding)\b",
    r"disregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|instructions)",
    r"forget\s+(?:all\s+|your\s+|everything)",
    r"\b(?:system|developer)\s+(?:prompt|message|instruction)s?\b",
    r"\bnew\s+instructions?\b",
    r"\byou\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|language model|llm|chatbot|claude|gpt)\b",
    r"\b(?:if|when)\s+you\s+are\s+(?:an?\s+)?(?:ai|llm|chatbot|language model|assistant)\b",
    r"\bas\s+an?\s+(?:ai\s+(?:language\s+model|assistant)|language\s+model)\b",
    r"\b(?:ai|llm|chatbot)\s+(?:agents?|assistants?|models?)[,:]?\s+(?:must|should|please|do|ignore)",
    # Exfiltration: sending *someone else's* data (not the normal "send us your resume").
    r"\b(?:send|email|forward|post|upload|share)\b.{0,40}\b(?:the\s+user|user'?s|candidate'?s|"
    r"applicant'?s|your\s+user)\b.{0,25}\b(?:resume|r[ée]sum[ée]|cv|api[ -]?key|password|"
    r"credentials?|token|personal (?:data|information))\b",
    r"\b(?:reveal|output|print|repeat|disclose)\b.{0,30}\b(?:system prompt|api[ -]?key|password|"
    r"credentials?|your instructions)\b",
    r"\bdo\s+not\s+(?:tell|mention|reveal|disclose|inform)\b.{0,40}\b(?:user|candidate|human)\b",
    r"\bwhen\s+(?:summari[sz]ing|listing|ranking|reviewing)\s+this\b",
    r"<\|?(?:im_start|im_end|system|assistant)\|?>",
    r"(?:^|\n)\s*(?:system|assistant|human)\s*:",
    r"\brank\s+this\s+(?:job|posting|role)\b.{0,30}\b(?:first|highest|top|best)\b",
    r"\b(?:ai|llm|agent|assistant)s?\b.{0,40}\bapply\b.{0,30}\b(?:automatically|on behalf)\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _PATTERNS]


def looks_like_injection(*texts: str) -> bool:
    return any(p.search(text) for text in texts if text for p in _COMPILED)
