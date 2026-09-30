"""Claude: (1) filters discovered posts for relevance, (2) scores engagers vs the ICP
and writes a personalised opener. Context comes from icp.md at the project root."""
import anthropic

from .config import CLAUDE_MODEL, ROOT
from .util import extract_json

BATCH = 12


class Claude:
    def __init__(self):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.context = (ROOT / "icp.md").read_text()

    def _ask(self, instruction: str, payload: str) -> object:
        msg = self.client.messages.create(
            model=CLAUDE_MODEL, max_tokens=4096,
            system=f"{self.context}\n\nYou are a precise B2B research assistant. Reply with JSON only.",
            messages=[{"role": "user", "content": f"{instruction}\n\n{payload}"}],
        )
        return extract_json("".join(b.text for b in msg.content if b.type == "text"))

    def classify_posts(self, posts: list[dict]) -> dict[str, dict]:
        """posts: [{id, author, text}] -> {id: {relevant, reason, language}}"""
        out: dict[str, dict] = {}
        for i in range(0, len(posts), BATCH):
            chunk = posts[i:i + BATCH]
            import json
            res = self._ask(
                'For each post decide if it is genuinely about the topic in "Topic" above (not spam, a job ad, '
                'or an incidental keyword match). Return {"results":[{"id":str,"relevant":bool,'
                '"reason":str(<=120 chars),"language":"ISO 639-1"}]}.',
                json.dumps(chunk, ensure_ascii=False))
            for r in res.get("results", []):
                out[str(r["id"])] = r
        return out

    def score_engagers(self, post: dict, engagers: list[dict]) -> dict[str, dict]:
        """engagers: [{id, name, headline, action, comment}] -> {id: {score, fit, rationale, icebreaker}}"""
        import json
        out: dict[str, dict] = {}
        for i in range(0, len(engagers), BATCH):
            chunk = engagers[i:i + BATCH]
            res = self._ask(
                'Score each person 0-100 on fit with the ICP above, using headline, action and comment. '
                'Private individuals with no business role, recruiters and obvious competitors score low. '
                'For score >= 60 write a 1-2 sentence "icebreaker" for a cold outreach: reference the post '
                'they engaged with, be specific, no flattery, no emojis, no em dashes, never invent facts. '
                'Return {"results":[{"id":str,"score":int,"fit":"high|medium|low","rationale":str(<=160 chars),'
                '"icebreaker":str}]} (icebreaker "" when score < 60).',
                json.dumps({"post": post, "people": chunk}, ensure_ascii=False))
            for r in res.get("results", []):
                out[str(r["id"])] = r
        return out
