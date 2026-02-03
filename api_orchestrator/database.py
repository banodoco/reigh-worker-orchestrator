"""Minimal database client for API orchestrator logging."""

import os
import logging
from datetime import datetime, timezone
from supabase import create_client, Client

logger = logging.getLogger(__name__)


class DatabaseClient:
    """Minimal database client for centralized logging support."""
    
    def __init__(self):
        """Initialize Supabase client."""
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        
        self.supabase: Client = create_client(supabase_url, supabase_key)

    def register_worker(self, worker_id: str) -> bool:
        """Register or update an API worker in the workers table.
        
        Uses upsert to handle both new registration and reconnection cases.
        """
        try:
            result = self.supabase.table('workers').upsert({
                'id': worker_id,
                'instance_type': 'api',
                'status': 'active',
                'last_heartbeat': datetime.now(timezone.utc).isoformat(),
                'metadata': {'type': 'api_orchestrator'},
                'created_at': datetime.now(timezone.utc).isoformat()
            }, on_conflict='id').execute()
            
            logger.info(f"Registered API worker: {worker_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to register API worker {worker_id}: {e}")
            return False





