"""Tiny client for the creditbot kiosk API running on the NAS."""
import requests


class ApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["X-API-Key"] = api_key

    def _get(self, path: str):
        r = self.session.get(f"{self.base_url}{path}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict):
        r = self.session.post(
            f"{self.base_url}{path}", json=payload, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def health(self):
        return self._get("/health")

    def get_faces(self):
        return self._get("/faces")["faces"]

    def get_members(self):
        return self._get("/members")["members"]

    def checkin(self, discord_id: str, username: str):
        return self._post("/checkin", {"discord_id": discord_id, "username": username})

    def checkout(self, discord_id: str):
        return self._post("/checkout", {"discord_id": discord_id})

    def enroll_face(self, discord_id: str, name: str, embedding):
        return self._post("/faces", {
            "discord_id": discord_id,
            "name": name,
            "embedding": [float(x) for x in embedding],
        })
