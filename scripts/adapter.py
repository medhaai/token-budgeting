from ledger import TokenLedger
import logging

logger = logging.getLogger("hermes.token_adapter")

class HermesTokenAdapter:
    def __init__(self):
        self.ledger = TokenLedger()

    def record_response(self, response, model_name: str, session_id: str = "unknown"):
        """
        Passive Intercept: Logs tokens without interrupting the agent flow.
        Expected response object to have a .usage attribute (OpenAI style).
        """
        try:
            if hasattr(response, 'usage') and response.usage:
                prompt = response.usage.prompt_tokens
                completion = response.usage.completion_tokens
                self.ledger.log_usage(
                    model=model_name, 
                    prompt_tokens=prompt, 
                    completion_tokens=completion, 
                    session_id=session_id
                )
        except Exception as e:
            logger.error(f"Passive token logging failed: {e}")
        return None

# Singleton for a seamless hook into run_agent.py
token_adapter = HermesTokenAdapter()
