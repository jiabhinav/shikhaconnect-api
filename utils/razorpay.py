import base64
import json
from http.client import HTTPException as HTTPClientError, HTTPSConnection

from fastapi import HTTPException
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RazorpaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    razorpay_key_id: str = ""
    razorpay_key_secret: SecretStr = SecretStr("")
    razorpay_webhook_secret: SecretStr = SecretStr("")


class RazorpayClient:
    def __init__(self, settings: RazorpaySettings):
        self.key_id = settings.razorpay_key_id
        self.secret = settings.razorpay_key_secret.get_secret_value()
        if not self.key_id or not self.secret:
            raise HTTPException(503, "Razorpay checkout is not configured")

    def request(self, method, path, payload=None):
        credential = base64.b64encode(f"{self.key_id}:{self.secret}".encode()).decode()
        connection = HTTPSConnection("api.razorpay.com", timeout=10)
        try:
            connection.request(
                method, path, body=json.dumps(payload) if payload is not None else None,
                headers={"Authorization": f"Basic {credential}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise HTTPException(502, "Razorpay could not process the gateway request")
            result = json.loads(response.read())
            if not isinstance(result, dict):
                raise HTTPException(502, "Razorpay returned an invalid response")
            return result
        except (OSError, HTTPClientError, ValueError) as exc:
            raise HTTPException(502, "Razorpay is unavailable or returned an invalid response") from exc
        finally:
            connection.close()
