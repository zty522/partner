"""BioNeMo stub adapter for when NGC_API_KEY is not available."""
def execute(task: str = "", **kwargs) -> dict:
    return {"status": "unavailable", "output": {"text": "BioNeMo requires NGC_API_KEY"}, "error": "no_api_key"}
