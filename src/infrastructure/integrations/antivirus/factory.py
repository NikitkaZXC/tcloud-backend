from src.config import settings
from src.infrastructure.integrations.antivirus.clamav import ClamAVClient


def create_scanner() -> ClamAVClient:
    return ClamAVClient(
        settings.antivirus.HOST, settings.antivirus.PORT, settings.antivirus.TIMEOUT
    )
