import logging

logger = logging.getLogger("hermes.token_adapter")


class HermesTokenAdapter:
    """Backward-compatible passive adapter.

    Hermes now records token usage directly in ~/.hermes/state.db. The
    token-budgeting skill should not duplicate that source of truth into a
    separate CSV ledger. This adapter remains as a safe no-op for older Hermes
    configurations that still import token_adapter.
    """

    def record_response(self, response, model_name: str, session_id: str = "unknown"):
        logger.debug(
            "token-budgeting adapter called for model=%s session=%s; "
            "no-op because Hermes state.db is the source of truth",
            model_name,
            session_id,
        )
        return None


# Singleton for old soft-hook integrations.
token_adapter = HermesTokenAdapter()
