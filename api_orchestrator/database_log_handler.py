"""
Database Log Handler for Orchestrator
======================================

Custom logging handler that batches logs and sends them to Supabase in the background.
This allows centralized log storage without blocking the main orchestrator loop.

Uses contextvars for proper async context isolation - each concurrent task maintains
its own task_id, worker_id, and cycle_number without interference.

Usage:
    from database_log_handler import DatabaseLogHandler
    from database import DatabaseClient
    
    db = DatabaseClient()
    handler = DatabaseLogHandler(
        supabase_client=db.supabase,
        source_type="orchestrator_gpu",
        source_id="orchestrator-main"
    )
    logging.getLogger().addHandler(handler)
    
    # Set current cycle for context (thread-safe, async-safe)
    handler.set_current_cycle(5)
"""

import logging
import queue
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from supabase import Client

# Context variables for async-safe context tracking
# These maintain separate values per async task/thread
_context_cycle: ContextVar[Optional[int]] = ContextVar('cycle_number', default=None)
_context_task_id: ContextVar[Optional[str]] = ContextVar('task_id', default=None)
_context_worker_id: ContextVar[Optional[str]] = ContextVar('worker_id', default=None)


class DatabaseLogHandler(logging.Handler):
    """
    Async logging handler that batches logs and sends to Supabase database.
    
    Runs a background thread that:
    1. Collects log records into batches
    2. Sends batches to database via RPC function
    3. Prevents blocking the main application thread
    
    Features:
    - Automatic batching for efficiency
    - Background thread for non-blocking operation
    - Graceful error handling (logs to stderr on failure)
    - Context tracking (cycle number for orchestrator)
    - Queue size limiting to prevent memory issues
    """
    
    def __init__(
        self,
        supabase_client: Client,
        source_type: str,
        source_id: str,
        batch_size: int = 50,
        flush_interval: float = 5.0,
        max_queue_size: int = 2000,
        min_level: int = logging.INFO
    ):
        """
        Initialize database log handler.
        
        Args:
            supabase_client: Supabase client instance
            source_type: Type of source ('orchestrator_gpu', 'orchestrator_api', 'worker')
            source_id: Unique identifier for this source instance
            batch_size: Number of logs to batch before sending (default: 50)
            flush_interval: Seconds between automatic flushes (default: 5.0)
            max_queue_size: Maximum queued logs before dropping (default: 2000)
            min_level: Minimum log level to send to database (default: INFO)
        """
        super().__init__()
        self.supabase = supabase_client
        self.source_type = source_type
        self.source_id = source_id
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        
        # Only send logs at or above this level to database
        self.setLevel(min_level)
        
        # Queue for batching logs
        self.log_queue = queue.Queue(maxsize=max_queue_size)
        
        # Shutdown signal
        self.shutdown_event = threading.Event()
        
        # Background thread for async sending
        self.worker_thread = threading.Thread(
            target=self._background_worker,
            daemon=True,
            name=f"DBLogHandler-{source_id}"
        )
        self.worker_thread.start()
        
        # Statistics
        self.total_logs_queued = 0
        self.total_logs_sent = 0
        self.total_logs_dropped = 0
        self.total_batches_sent = 0
        self.total_errors = 0
    
    def set_current_cycle(self, cycle_number: Optional[int]):
        """
        Set current orchestrator cycle number for context.
        Uses contextvars for async-safe context isolation.
        """
        _context_cycle.set(cycle_number)
    
    def set_current_task(self, task_id: Optional[str]):
        """
        Set current task ID for context.
        Uses contextvars for async-safe context isolation - each concurrent task
        maintains its own task_id without interference.
        """
        _context_task_id.set(task_id)
    
    def set_current_worker(self, worker_id: Optional[str]):
        """
        Set current worker ID for context.
        Uses contextvars for async-safe context isolation.
        """
        _context_worker_id.set(worker_id)
    
    def emit(self, record: logging.LogRecord):
        """
        Queue log record for batch processing.
        
        This is called by the logging framework when a log is emitted.
        We queue it for background processing to avoid blocking.
        """
        try:
            # Build log entry
            entry = {
                'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                'source_type': self.source_type,
                'source_id': self.source_id,
                'log_level': record.levelname,
                'message': self._format_message(record),
                'metadata': {
                    'module': record.module,
                    'funcName': record.funcName,
                    'lineno': record.lineno,
                    'pathname': record.pathname,
                    'thread': record.thread,
                    'threadName': record.threadName,
                }
            }
            
            # Add context if available (from contextvars - async-safe)
            cycle_number = _context_cycle.get()
            if cycle_number is not None and cycle_number > 0:
                entry['cycle_number'] = cycle_number
            
            task_id = _context_task_id.get()
            if task_id:
                entry['task_id'] = task_id
            
            worker_id = _context_worker_id.get()
            if worker_id:
                entry['worker_id'] = worker_id
            
            # Add exception info if present
            if record.exc_info:
                import traceback
                entry['metadata']['exception'] = ''.join(
                    traceback.format_exception(*record.exc_info)
                )
            
            # Add extra fields from record
            if hasattr(record, 'extra_data'):
                entry['metadata']['extra'] = record.extra_data
            
            # Try to add to queue (non-blocking to avoid deadlock)
            try:
                self.log_queue.put_nowait(entry)
                self.total_logs_queued += 1
            except queue.Full:
                # Queue is full, drop log to prevent memory issues
                self.total_logs_dropped += 1
                # Only log to stderr occasionally to avoid spam
                if self.total_logs_dropped % 100 == 1:
                    import sys
                    print(
                        f"WARNING: DatabaseLogHandler queue full, "
                        f"dropped {self.total_logs_dropped} logs total",
                        file=sys.stderr
                    )
                
        except Exception:
            # Don't let logging errors break the application
            self.handleError(record)
    
    def _format_message(self, record: logging.LogRecord) -> str:
        """Format log message, handling any formatting issues."""
        try:
            return record.getMessage()
        except Exception:
            # If message formatting fails, return raw message
            return str(record.msg)
    
    def _background_worker(self):
        """
        Background thread that batches and sends logs to database.
        
        This runs continuously until shutdown, batching logs and
        sending them via RPC function.
        """
        batch = []
        last_flush_time = time.time()
        
        while not self.shutdown_event.is_set():
            try:
                # Calculate time until next flush
                time_since_flush = time.time() - last_flush_time
                timeout = max(0.1, self.flush_interval - time_since_flush)
                
                # Try to get log from queue with timeout
                try:
                    entry = self.log_queue.get(timeout=timeout)
                    batch.append(entry)
                except queue.Empty:
                    pass
                
                # Flush if batch is full or interval elapsed
                current_time = time.time()
                should_flush = (
                    len(batch) >= self.batch_size or
                    (batch and current_time - last_flush_time >= self.flush_interval)
                )
                
                if should_flush:
                    self._flush_batch(batch)
                    batch = []
                    last_flush_time = current_time
                    
            except Exception as e:
                # Log to stderr to avoid recursion
                import sys
                print(f"Error in DatabaseLogHandler background worker: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
                # Sleep briefly to avoid tight error loop
                time.sleep(1)
        
        # Flush remaining logs on shutdown
        if batch:
            self._flush_batch(batch)
    
    def _flush_batch(self, batch: list):
        """
        Send batch of logs to database via RPC function.
        
        Args:
            batch: List of log entry dictionaries
        """
        if not batch:
            return
        
        try:
            # Call RPC function to insert logs
            result = self.supabase.rpc('func_insert_logs_batch', {
                'logs': batch
            }).execute()
            
            # Update statistics
            self.total_batches_sent += 1
            self.total_logs_sent += len(batch)
            
            # Log any errors from RPC function
            if result.data:
                errors = result.data.get('errors', 0)
                if errors > 0:
                    self.total_errors += errors
                    import sys
                    print(
                        f"DatabaseLogHandler: {errors} logs failed to insert in batch",
                        file=sys.stderr
                    )
            
        except Exception as e:
            # Log to stderr
            self.total_errors += len(batch)
            import sys
            print(f"Failed to flush log batch of {len(batch)} entries: {e}", file=sys.stderr)
            # Don't retry to avoid infinite loops
    
    def flush(self):
        """
        Force flush of any pending logs.
        
        This is synchronous and will block until the current queue is processed.
        Use sparingly, primarily for testing or before shutdown.
        """
        # Give background thread time to process queue
        max_wait = 10  # seconds
        start_time = time.time()
        
        while not self.log_queue.empty() and time.time() - start_time < max_wait:
            time.sleep(0.1)
    
    def close(self):
        """
        Shutdown handler gracefully.
        
        Signals background thread to stop and waits for it to finish.
        Flushes any remaining logs before exiting.
        """
        # Signal shutdown
        self.shutdown_event.set()
        
        # Wait for background thread to finish
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=15)
        
        # Call parent close
        super().close()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get handler statistics.
        
        Returns:
            Dictionary with statistics about log processing
        """
        return {
            'source_type': self.source_type,
            'source_id': self.source_id,
            'total_logs_queued': self.total_logs_queued,
            'total_logs_sent': self.total_logs_sent,
            'total_logs_dropped': self.total_logs_dropped,
            'total_batches_sent': self.total_batches_sent,
            'total_errors': self.total_errors,
            'queue_size': self.log_queue.qsize(),
            'is_alive': self.worker_thread.is_alive()
        }
    
    def __repr__(self):
        return (
            f"DatabaseLogHandler(source_type={self.source_type}, "
            f"source_id={self.source_id}, "
            f"sent={self.total_logs_sent}, "
            f"dropped={self.total_logs_dropped})"
        )

