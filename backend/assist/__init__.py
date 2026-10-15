"""Writing assistance for notes from a language model running on this computer (Ollama).

`ollama.py` is the HTTP client, `prompt.py` turns the text around the cursor into a request and
cleans what comes back, and `completer.py` is the component the API uses: it checks that Ollama
and the model are there before anything is asked of them, and keeps requests from piling up.
"""

from .completer import Completer
from .ollama import Unavailable

__all__ = ["Completer", "Unavailable"]
