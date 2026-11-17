"""Basic Agent Platform Python SDK client."""
import os

from agent_platform.sdk import PlatformClient

client = PlatformClient(
    base_url=os.environ["PLATFORM_API_URL"],
    token=os.environ["PLATFORM_API_TOKEN"],
    timeout=10.0,
    retry_attempts=3,
)

print(client.get("/workbench/capabilities"))
for item in client.iter_pages("/knowledge/documents", limit=50):
    print(item)
