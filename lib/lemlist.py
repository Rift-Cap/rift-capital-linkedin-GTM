"""lemlist client: add a lead to a campaign. Basic auth = empty user + API key."""
from .config import env
from .util import http, split_name

API = "https://api.lemlist.com/api"


class Lemlist:
    def __init__(self):
        self.auth = ("", env("LEMLIST_API_KEY", required=True))
        self.campaign = env("LEMLIST_CAMPAIGN_ID", required=True)

    def add_lead(self, *, name: str, linkedin_url: str, headline: str, icebreaker: str,
                 post_url: str, engagement: str, score: int) -> tuple[str, str]:
        """Returns (status, detail): status in {pushed, exists, error}."""
        first, last = split_name(name)
        body = {
            "firstName": first, "lastName": last, "linkedinUrl": linkedin_url,
            "jobTitle": headline, "icebreaker": icebreaker,
            "sourcePostUrl": post_url, "engagement": engagement, "fitScore": score,
        }
        r = http("POST", f"{API}/campaigns/{self.campaign}/leads/", auth=self.auth, json=body,
                 params={"deduplicate": "true", "linkedinEnrichment": "true", "findEmail": "true"})
        if r.status_code == 200:
            return "pushed", r.json().get("_id", "")
        if r.status_code == 409 or "ALREADY_IN_CAMPAIGN" in r.text:
            return "exists", r.text[:200]
        return "error", f"HTTP {r.status_code}: {r.text[:200]}"
