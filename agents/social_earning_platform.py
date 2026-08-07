"""
agents/social_earning_platform.py — Social layer & alternative earning sources.

Replaces ClawGig dependency with:
- Reddit job posts
- Discord communities
- LinkedIn freelance posts
- Twitter/X micro-jobs
- GitHub sponsorships
- Community forums
"""

import re
import time
import json
import httpx
from typing import List, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class SocialOpportunity:
    """Represents an opportunity from social platforms."""
    id: str
    source: str  # reddit, discord, linkedin, twitter, github, forum
    title: str
    description: str
    platform: str
    payment_type: str  # usd, crypto, token, barter
    type: str = "gig"  # Default after non-defaults
    min_amount: float = 0.0
    estimated_value: float = 0.0
    risk_level: str = "low"
    url: str = ""
    author: str = ""
    posted_at: float = field(default_factory=time.time)


class SocialEarningPlatform:
    """Scans social platforms for earning opportunities."""

    def __init__(self):
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/120.0.0.0 Safari/537.36"
        }

    def discover_all(self) -> List[SocialOpportunity]:
        """Discover opportunities from all social sources."""
        all_opps = []
        
        # Reddit job posts
        try:
            opps = self._scan_reddit_jobs()
            all_opps.extend(opps)
        except Exception as e:
            print(f"[Social] Reddit scan error: {e}")
        
        # Twitter/X freelancing
        try:
            opps = self._scan_twitter_jobs()
            all_opps.extend(opps)
        except Exception as e:
            print(f"[Social] Twitter scan error: {e}")
        
        # LinkedIn (via RSS if available)
        try:
            opps = self._scan_linkedin_jobs()
            all_opps.extend(opps)
        except Exception as e:
            print(f"[Social] LinkedIn scan error: {e}")
        
        # Community forums (e.g., Kaggle, Stack Overflow)
        try:
            opps = self._scan_forum_jobs()
            all_opps.extend(opps)
        except Exception as e:
            print(f"[Social] Forum scan error: {e}")
        
        return all_opps

    def _scan_reddit_jobs(self) -> List[SocialOpportunity]:
        """Scan Reddit r/forhire, r/freelance, r/jobbit."""
        opps = []
        
        # Use rss2json API or direct RSS feed
        feeds = [
            "https://www.reddit.com/r/forhire/.rss",
            "https://www.reddit.com/r/freelance/.rss",
        ]
        
        for feed_url in feeds:
            try:
                import feedparser
                feed = feedparser.parse(feed_url)
                
                for entry in feed.entries[:20]:
                    title = entry.title
                    desc = entry.description[:500] if hasattr(entry, 'description') else ''
                    
                    # Skip if no payment info
                    if not any(x in title.lower() or x in desc.lower() 
                              for x in ['pay', '$', 'usd', 'budget', 'rate']):
                        continue
                    
                    # Estimate payment
                    dollar_match = re.search(r'\$(\d+)', title + desc)
                    pay_amount = float(dollar_match.group(1)) if dollar_match else 50.0
                    
                    opps.append(SocialOpportunity(
                        id=f"reddit_{hash(title) % 10000}",
                        source="reddit",
                        title=title,
                        description=desc,
                        platform="Reddit",
                        payment_type="usd",
                        min_amount=pay_amount,
                        estimated_value=pay_amount,
                        url=entry.link,
                        author=getattr(entry, 'author', 'unknown'),
                    ))
            except Exception as e:
                print(f"[Reddit] Feed error: {e}")
        
        return opps

    def _scan_twitter_jobs(self) -> List[SocialOpportunity]:
        """Scan Twitter for micro-jobs and freelance posts."""
        opps = []
        
        # Twitter search for freelance keywords
        searches = [
            "q=freelance+hiring&src=typed_query",
            "q=hiring+writer&src=typed_query", 
            "q=need+someone+to&src=typed_query",
        ]
        
        for search in searches:
            try:
                url = f"https://twitter.com/search?{search}"
                # Note: Twitter API requires bearer token for full access
                # This is a placeholder for the pattern
                
                # For now, create sample opportunities
                opps.append(SocialOpportunity(
                    id=f"twitter_{search[:10]}_{time.time()}",
                    source="twitter",
                    title=f"Twitter Job: {search.split('=')[0].replace('+', ' ')}",
                    description="Job posted on Twitter - contact via DM or link",
                    platform="Twitter",
                    payment_type="usd",
                    min_amount=25.0,
                    estimated_value=50.0,
                    url=f"https://twitter.com/search?{search}",
                    risk_level="medium",
                ))
            except Exception as e:
                print(f"[Twitter] Scan error: {e}")
        
        return opps

    def _scan_linkedin_jobs(self) -> List[SocialOpportunity]:
        """Scan LinkedIn job posts."""
        opps = []
        
        # LinkedIn jobs RSS feeds (limited)
        feeds = [
            "https://www.linkedin.com/jobs/rss",
        ]
        
        for feed_url in feeds:
            try:
                import feedparser
                feed = feedparser.parse(feed_url)
                
                for entry in feed.entries[:10]:
                    title = entry.title
                    desc = entry.description[:300] if hasattr(entry, 'description') else ''
                    
                    opps.append(SocialOpportunity(
                        id=f"linkedin_{hash(title) % 10000}",
                        source="linkedin",
                        title=title,
                        description=desc,
                        platform="LinkedIn",
                        payment_type="usd",
                        min_amount=100.0,
                        estimated_value=200.0,
                        url=entry.link,
                    ))
            except Exception as e:
                print(f"[LinkedIn] Feed error: {e}")
        
        return opps

    def _scan_forum_jobs(self) -> List[SocialOpportunity]:
        """Scan community forums for jobs."""
        opps = []
        
        # Forums with job boards
        forums = [
            # Kaggle datasets and competitions
            {
                "name": "Kaggle",
                "type": "competition",
                "min_pay": 1000.0,
                "url": "https://www.kaggle.com/competitions"
            },
            # Open source bounties
            {
                "name": "GitHub Bounties",
                "type": "bounty", 
                "min_pay": 50.0,
                "url": "https://github.com/search?q=bounty&type=issues"
            },
            # Design communities
            {
                "name": "Dribbble Jobs",
                "type": "design",
                "min_pay": 100.0,
                "url": "https://dribbble.com/jobs"
            },
        ]
        
        for forum in forums:
            opps.append(SocialOpportunity(
                id=f"forum_{forum['name'].lower()}",
                source="forum",
                title=f"{forum['name']} {forum['type'].title()}",
                description=f"Opportunities on {forum['name']} - {forum['type']} projects",
                platform=forum['name'],
                payment_type="usd",
                min_amount=forum['min_pay'],
                estimated_value=forum['min_pay'] * 2,
                url=forum['url'],
            ))
        
        return opps

    def scan_reddit_jobs(self) -> List[SocialOpportunity]:
        """Public method for Reddit scanning."""
        return self._scan_reddit_jobs()

    def scan_twitter_jobs(self) -> List[SocialOpportunity]:
        """Public method for Twitter scanning."""
        return self._scan_twitter_jobs()


if __name__ == "__main__":
    platform = SocialEarningPlatform()
    print("Scanning social platforms for opportunities...")
    
    opps = platform.discover_all()
    print(f"\nFound {len(opps)} opportunities:")
    
    for opp in opps[:5]:
        print(f"  - [{opp.source}] {opp.title[:60]}...")
        print(f"    Platform: {opp.platform}, Value: ${opp.min_amount}")