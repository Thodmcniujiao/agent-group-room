from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional

class BaseTTSEngine(ABC):
    """Abstract base class for Voice TTS engines."""

    @abstractmethod
    async def synthesize(
        self,
        character_id: str,
        character_config: Dict[str, Any],
        text: str,
        output_path: Path,
        speed: float = 1.0,
        pitch_offset: int = 0
    ) -> bool:
        """Synthesize text to speech audio file."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if engine backend is available."""
        pass
