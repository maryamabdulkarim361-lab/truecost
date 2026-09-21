"""TrueCost configuration settings.

Loads configuration from environment variables with sensible defaults.
Secrets are loaded via Firebase Secrets Manager (production) or environment variables (emulator).
"""

import os
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
from config.production import validate_production

# Load .env file for non-secret configuration (emulator hosts, feature flags, etc.)
# Secrets should come from Firebase Secrets Manager or environment variables
load_dotenv()


def _get_default_a2a_url() -> str:
    """Get default A2A URL based on environment mode."""
    if (os.getenv("USE_FIREBASE_EMULATORS", "false").lower() == "true" or
        os.getenv("FUNCTIONS_EMULATOR", "false").lower() == "true"):
        return "http://127.0.0.1:5003/collabcanvas-dev/us-central1"
    return "http://localhost:5001"


@dataclass
class Settings:
    """Application settings loaded from environment variables.

    Note: Secrets (OPENAI_API_KEY, etc.) should be accessed via config.secrets module,
    not directly from this class. The openai_api_key property is provided for
    backwards compatibility but delegates to the secrets module.
    """

    # LLM Configuration (non-secrets)
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o"))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))

    # Firebase Configuration
    firebase_project_id: Optional[str] = field(default_factory=lambda: os.getenv("FIREBASE_PROJECT_ID"))
    use_firebase_emulators: bool = field(default_factory=lambda: os.getenv("USE_FIREBASE_EMULATORS", "false").lower() == "true")
    firestore_emulator_host: str = field(default_factory=lambda: os.getenv("FIRESTORE_EMULATOR_HOST", "localhost:8081"))

    # A2A Protocol Configuration
    a2a_base_url: str = field(default_factory=lambda: os.getenv("A2A_BASE_URL", _get_default_a2a_url()))
    a2a_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("A2A_TIMEOUT_SECONDS", "300")))

    # Pipeline Configuration
    pipeline_max_retries: int = field(default_factory=lambda: int(os.getenv("PIPELINE_MAX_RETRIES", "2")))
    pipeline_passing_score: int = field(default_factory=lambda: int(os.getenv("PIPELINE_PASSING_SCORE", "60")))

    # Leave room inside the unchanged A2A timeout for storage and LLM cleanup.
    cost_execution_budget_seconds: float = field(default_factory=lambda: float(os.getenv("COST_EXECUTION_BUDGET_SECONDS", "240")))
    price_enrichment_budget_seconds: float = field(default_factory=lambda: float(os.getenv("PRICE_ENRICHMENT_BUDGET_SECONDS", "20")))

    # Monte Carlo Configuration
    monte_carlo_iterations: int = field(default_factory=lambda: int(os.getenv("MONTE_CARLO_ITERATIONS", "10000")))

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # Internal: cached secret value (use openai_api_key property instead)
    _openai_api_key: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        validate_production(self)
        if self.llm_provider not in ("openai", "gemini"):
            raise ValueError("LLM_PROVIDER must be 'openai' or 'gemini'")
        if not 0 < self.cost_execution_budget_seconds <= self.a2a_timeout_seconds - 30:
            raise ValueError("COST_EXECUTION_BUDGET_SECONDS must leave at least 30 seconds before A2A timeout")
        if not 0 < self.price_enrichment_budget_seconds < self.cost_execution_budget_seconds:
            raise ValueError("PRICE_ENRICHMENT_BUDGET_SECONDS must be below the Cost budget")

    @property
    def llm_key_env(self) -> str:
        return "GEMINI_API_KEY" if self.llm_provider == "gemini" else "OPENAI_API_KEY"

    @property
    def llm_api_key(self) -> Optional[str]:
        if self.llm_provider == "gemini":
            return os.getenv("GEMINI_API_KEY")
        return self.openai_api_key

    @property
    def llm_client_options(self) -> dict:
        if self.llm_provider == "gemini":
            return {
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "use_responses_api": False,
            }
        return {}

    @property
    def openai_api_key(self) -> Optional[str]:
        """Get OpenAI API key from Firebase Secrets Manager or environment.

        This property uses the unified secrets module for consistent access
        across emulator and production environments.
        """
        if self._openai_api_key is None:
            from config.secrets import get_openai_api_key
            self._openai_api_key = get_openai_api_key()
        return self._openai_api_key

    def validate(self) -> None:
        """Validate required settings are present.

        Raises:
            ValueError: If required settings are missing.
        """
        if not self.llm_api_key and not self.use_firebase_emulators:
            raise ValueError(f"{self.llm_key_env} is required in production")

    @property
    def is_emulator_mode(self) -> bool:
        """Check if running in emulator mode."""
        return self.use_firebase_emulators


# Singleton settings instance
settings = Settings()
