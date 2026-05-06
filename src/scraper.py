"""
Fetches event listings from Resident Advisor via their internal GraphQL API.

Design note: RAScraper inherits from BaseScraper so the data source can be
swapped (e.g. for a mock in tests, or a different events site) without
touching any downstream code.

Key findings from API exploration:
- Endpoint: https://ra.co/graphql (no auth required)
- Cities: looked up by name via areas(searchTerm:) → returns numeric area id
- Genres: filtered server-side using lowercase genre name string
- Promoter contact: email + website + instagram available per promoter
- Pagination: page/pageSize params, totalResults in response
"""

import abc
import dataclasses
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

RA_GRAPHQL = "https://ra.co/graphql"

HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://ra.co/events",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

EVENT_LISTINGS_QUERY = """
query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, $pageSize: Int, $page: Int) {
  eventListings(filters: $filters, pageSize: $pageSize, page: $page) {
    data {
      id
      event {
        id
        title
        date
        startTime
        content
        contentUrl
        attending
        venue { name id contentUrl }
        promoters { name id email website instagram facebook contentUrl }
        genres { name }
      }
    }
    totalResults
  }
}
"""

AREA_LOOKUP_QUERY = """
query($searchTerm: String) {
  areas(searchTerm: $searchTerm) {
    id name urlName country { name }
  }
}
"""


@dataclasses.dataclass
class Event:
    title: str
    date: str
    venue: str
    city: str
    genre: str
    promoter: Optional[str]
    description: Optional[str]
    contact_email: Optional[str]
    contact_website: Optional[str]
    contact_instagram: Optional[str]
    ra_url: str
    ra_promoter_url: Optional[str]


class BaseScraper(abc.ABC):
    @abc.abstractmethod
    def fetch_events(self, city: str, genre: str, limit: int) -> list[Event]:
        """Return up to `limit` events matching city and genre."""
        ...


class RAScraper(BaseScraper):

    def _graphql(self, query: str, variables: dict) -> dict:
        resp = requests.post(
            RA_GRAPHQL,
            headers=HEADERS,
            json={"query": query, "variables": variables},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise ValueError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    def resolve_area_id(self, city: str) -> tuple[int, str]:
        """
        Returns (area_id, canonical_city_name) for a city name.
        Raises ValueError if city not found on RA.
        """
        data = self._graphql(AREA_LOOKUP_QUERY, {"searchTerm": city})
        areas = data.get("areas", [])
        if not areas:
            raise ValueError(f"No RA area found for city: '{city}'. Try a different spelling.")
        # Prefer exact match (case-insensitive), fall back to first result
        exact = [a for a in areas if a["name"].lower() == city.lower()]
        chosen = exact[0] if exact else areas[0]
        return int(chosen["id"]), chosen["name"]

    def fetch_events(self, city: str, genre: str, limit: int = 20) -> list[Event]:
        area_id, canonical_city = self.resolve_area_id(city)

        # Fetch from today for the next 60 days
        date_from = datetime.utcnow()
        date_to = date_from + timedelta(days=60)

        events: list[Event] = []
        page = 1
        page_size = min(limit, 20)  # RA caps at 20 per page

        while len(events) < limit:
            data = self._graphql(
                EVENT_LISTINGS_QUERY,
                {
                    "filters": {
                        "areas": {"eq": area_id},
                        "genre": {"eq": genre.lower()},
                        "listingDate": {
                            "gte": date_from.strftime("%Y-%m-%dT00:00:00.000Z"),
                            "lte": date_to.strftime("%Y-%m-%dT23:59:59.000Z"),
                        },
                    },
                    "pageSize": page_size,
                    "page": page,
                },
            )

            listings = data["eventListings"]["data"]
            total = data["eventListings"]["totalResults"]

            if not listings:
                break

            for item in listings:
                e = item["event"]
                promoters = e.get("promoters") or []
                # Pick the first promoter with an email, else the first promoter
                contact = next((p for p in promoters if p.get("email")), None) or (promoters[0] if promoters else None)

                events.append(Event(
                    title=e["title"],
                    date=e["date"][:10],  # "2026-05-06"
                    venue=(e["venue"] or {}).get("name", "Unknown venue"),
                    city=canonical_city,
                    genre=genre,
                    promoter=contact["name"] if contact else None,
                    description=e.get("content"),
                    contact_email=contact.get("email") if contact else None,
                    contact_website=contact.get("website") if contact else None,
                    contact_instagram=contact.get("instagram") if contact else None,
                    ra_url=f"https://ra.co{e['contentUrl']}",
                    ra_promoter_url=(f"https://ra.co{contact['contentUrl']}" if contact and contact.get("contentUrl") else None),
                ))

                if len(events) >= limit:
                    break

            if len(listings) < page_size or len(events) >= total:
                break

            page += 1
            time.sleep(0.5)  # be polite to RA's servers

        return events
