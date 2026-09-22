import os
import re

files = [
    'scp/api_server_parts/_ask_impl.py', 
    'scp/api/chat.py', 
    'scp/api/webhook.py', 
    'scp/api/routes/v105_routes.py', 
    'scp/api_server_parts/lifespan.py', 
    'scp/api_server_parts/_async_fact_check.py',
    'COMPREHENSIVE_AUDIT_REPORT.md'
]

mojibakes = [
    "│Ă¢â€\x9aÂ¬Ă¢â‚¬Â\x9d",
    "Ă¢â€šÂ¬Ă¢â‚¬Â",
    "Ă¢â€\x9aÂ¬Ă¢â‚¬Â\x9d",
    "Ă„â€šĂ‚Â¢│Ă¢â‚¬ÂšĂ‚Â¬│Ă¢â€šÂ¬Ă‚Â",
    "-žĂ¢â‚¬Âš-šĂ‚Â¢Ă„â€šĂ‚Â¢Ă¢â€šÂ¬Ă‚Âš-šĂ‚Â¬Ă„â€šĂ‚Â¢Ă¢â‚¬ÂšĂ‚Â¬-šĂ‚Â ",
    "-žĂ¢â‚¬Âš-šĂ‚Â¢Ă„â€šĂ‚Â¢Ă¢â‚¬ÂšĂ‚Â¬-šĂ‚Â Ă„â€šĂ‚Â¢Ă¢â‚¬ÂšĂ‚Â¬Ă¢â‚¬ÂžĂ‚Â¢",
    "Ă„â€šĂ‚Â¢│Ă¢â€šÂ¬Ă‚Â │Ă¢â€šÂ¬Ă¢â€žÂ¢",
    "│Ă…â€œĂ¢â‚¬Â¦",
    "TĂ„â€šĂ‚Â¡-šĂ‚Âº-šĂ‚Â I SAO:",
    "-žĂ¢â‚¬Âš-šĂ‚Â¡",
    "-žĂ¢â‚¬Âš-šĂ‚Â ",
    "Ă„â€šĂ¢â‚¬Âš-šĂ‚Â§"
]

for f in files:
    if os.path.exists(f):
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
        for m in mojibakes:
            content = content.replace(m, "—")
        with open(f, 'w', encoding='utf-8') as file:
            file.write(content)
