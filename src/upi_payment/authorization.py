class InMemoryAuthorizationVerifier:
    def __init__(self, approved_tokens: set[str]) -> None:
        self._approved_tokens = approved_tokens

    def verify(self, token: str) -> bool:
        return token in self._approved_tokens

