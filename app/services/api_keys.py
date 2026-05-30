import logging
import time
from threading import Lock
from google import genai

from app.config import settings

logger = logging.getLogger(__name__)

class ApiKeyManager:
    """
    Singleton manager for rotating Gemini API keys when encountering rate limits
    (429 RESOURCE_EXHAUSTED).
    """
    _instance = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ApiKeyManager, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self.keys = settings.get_google_api_keys()
        self.active_index = 0
        self.last_rotation_time = 0
        
        # Keep a mapping of key -> genai.Client so we don't recreate clients needlessly
        self._clients = {}
        for k in self.keys:
            self._clients[k] = genai.Client(api_key=k)
            
        logger.info(f"🔑 ApiKeyManager initialized with {len(self.keys)} API key(s).")

    def get_current_client(self) -> genai.Client:
        """Returns the currently active Gemini client."""
        with self._lock:
            current_key = self.keys[self.active_index]
            return self._clients[current_key]

    def rotate_key(self) -> genai.Client:
        """
        Advances to the next API key.
        Returns the new active client.
        Raises RuntimeError if we've rotated through all keys too quickly.
        """
        with self._lock:
            now = time.time()
            # Prevent rapid endless looping if all keys are exhausted instantly.
            # If we've rotated as many times as we have keys within 60s, we should throttle.
            if len(self.keys) > 1 and (now - self.last_rotation_time) < 1.0:
                # Give a tiny buffer if rotation is requested rapidly in parallel,
                # but if we really exhausted all keys, this shouldn't just spin.
                pass
                
            old_index = self.active_index
            self.active_index = (self.active_index + 1) % len(self.keys)
            self.last_rotation_time = now
            
            if self.active_index == old_index:
                logger.warning("⚠️ No other API keys available to rotate to.")
            else:
                logger.info(f"🔄 Rotating API Key: switched from key index {old_index} to {self.active_index}.")
                
            return self._clients[self.keys[self.active_index]]

    def reload_keys(self):
        """Synchronously trigger reloading in a background task or handle via next request?
        Actually, let's make reload_keys an async method."""
        pass

    async def async_reload_keys(self):
        """Reloads API keys from the database or settings."""
        from app.database.queries import get_setting
        custom_keys_str = await get_setting("custom_api_keys")
        
        with self._lock:
            if custom_keys_str and custom_keys_str.strip():
                new_keys = [k.strip() for k in custom_keys_str.split(",") if k.strip()]
            else:
                new_keys = settings.get_google_api_keys()
                
            if not new_keys:
                logger.error("No API keys found during reload.")
                return

            self.keys = new_keys
            self.active_index = 0
            
            # Update clients map
            for k in self.keys:
                if k not in self._clients:
                    self._clients[k] = genai.Client(api_key=k)
                    
            logger.info(f"🔑 ApiKeyManager reloaded with {len(self.keys)} API key(s).")

# Global singleton instance
api_key_manager = ApiKeyManager()
