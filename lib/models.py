from typing import Literal, Optional

from pydantic import BaseModel

from .util import canonical_url


class Post(BaseModel):
    post_url: str
    social_id: str = ""
    post_date: str = ""
    post_author_name: str = ""
    post_author_profile_url: str = ""
    post_language: str = ""
    matched_keyword: str = ""
    post_text_excerpt: str = ""
    search_query: str = ""
    collected_at: str = ""
    relevant: Optional[bool] = None
    relevance_reason: str = ""


class Engager(BaseModel):
    post_url: str
    post_author_name: str = ""
    name: str
    headline: str = ""
    linkedin_url: str = ""
    source: Literal["reaction", "comment"]
    reaction_type: str = ""
    comment_text: str = ""
    collected_at: str = ""

    @property
    def key(self) -> str:
        """Dedup key. Reactions: post + profile. Comments: + first 80 chars of text."""
        base = f"{canonical_url(self.post_url)}|{canonical_url(self.linkedin_url) or self.name}|{self.source}"
        if self.source == "comment":
            base += "|" + " ".join(self.comment_text.split())[:80]
        return base
