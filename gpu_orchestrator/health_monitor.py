"""
Health Monitor for Orchestrator
================================

Monitors critical orchestrator health metrics and alerts on anomalies.
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class OrchestratorHealthMonitor:
    """Monitor orchestrator health and detect anomalies."""
    
    def __init__(self):
        self.last_db_log_time: Optional[datetime] = None
        self.last_task_count: Optional[int] = None
        self.last_worker_count: int = 0
        self.rapid_scale_up_threshold: int = 3  # Alert if scaling up by 3+ workers
        self.task_count_spike_threshold: float = 10.0  # Alert if task count increases by 10x
    
    def check_logging_health(self) -> bool:
        """
        Check if database logging is working.
        
        Returns:
            True if healthy, False if degraded
        """
        from .logging_config import get_db_log_handler
        
        handler = get_db_log_handler()
        
        if handler is None:
            logger.error("❌ HEALTH CHECK: Database logging is NOT enabled")
            return False
        
        stats = handler.get_stats()
        
        # Check if handler is alive
        if not stats['is_alive']:
            logger.error("❌ HEALTH CHECK: Database log handler thread is DEAD")
            logger.error(f"   Stats: {stats}")
            return False
        
        # Check error rate
        if stats['total_logs_sent'] > 0:
            error_rate = stats['total_errors'] / (stats['total_logs_sent'] + stats['total_errors'])
            if error_rate > 0.1:  # More than 10% error rate
                logger.warning(f"⚠️  HEALTH CHECK: Database logging error rate high: {error_rate:.1%}")
                logger.warning(f"   Sent: {stats['total_logs_sent']}, Errors: {stats['total_errors']}")
                return False
        
        # Check queue size
        if stats['queue_size'] > 1000:
            logger.warning(f"⚠️  HEALTH CHECK: Database log queue is large: {stats['queue_size']}")
        
        # Check if logs are being dropped
        if stats['total_logs_dropped'] > 100:
            logger.warning(f"⚠️  HEALTH CHECK: {stats['total_logs_dropped']} logs have been dropped")
        
        return True
    
    def check_scaling_anomaly(self, task_count: int, worker_count: int, workers_spawned: int) -> None:
        """
        Check for unusual scaling behavior.
        
        Args:
            task_count: Current available task count
            worker_count: Current total worker count
            workers_spawned: Number of workers spawned this cycle
        """
        # Check for rapid scale-up
        if workers_spawned >= self.rapid_scale_up_threshold:
            logger.warning(f"🚨 ANOMALY DETECTED: Rapid scale-up of {workers_spawned} workers in one cycle")
            logger.warning(f"   Task count: {task_count}")
            logger.warning(f"   Previous worker count: {self.last_worker_count}")
            logger.warning(f"   This may indicate:")
            logger.warning(f"   - Large batch of new tasks submitted")
            logger.warning(f"   - Edge function returning incorrect count")
            logger.warning(f"   - Workers failing rapidly and being replaced")
        
        # Check for task count spike
        if self.last_task_count is not None and self.last_task_count > 0:
            if task_count > self.last_task_count * self.task_count_spike_threshold:
                logger.warning(f"🚨 ANOMALY DETECTED: Task count spike")
                logger.warning(f"   Previous count: {self.last_task_count}")
                logger.warning(f"   Current count: {task_count}")
                logger.warning(f"   Increase: {task_count - self.last_task_count} ({task_count/self.last_task_count:.1f}x)")
        
        # Check for task count appearing out of nowhere
        if self.last_task_count == 0 and task_count > 10:
            logger.warning(f"🚨 ANOMALY DETECTED: Task count jumped from 0 to {task_count}")
            logger.warning(f"   This could indicate:")
            logger.warning(f"   - Legitimate batch submission")
            logger.warning(f"   - Edge function bug (old tasks becoming visible)")
            logger.warning(f"   - Database query issue")
        
        # Update tracking
        self.last_task_count = task_count
        self.last_worker_count = worker_count
    
    def log_health_summary(self, cycle_number: int) -> None:
        """
        Log health summary (call periodically).
        
        Args:
            cycle_number: Current orchestrator cycle number
        """
        if cycle_number % 20 == 0:  # Every 20 cycles (~10 minutes)
            logger.info(f"📊 HEALTH SUMMARY (Cycle #{cycle_number}):")
            
            # Check logging health
            logging_healthy = self.check_logging_health()
            logger.info(f"   • Database logging: {'✅ Healthy' if logging_healthy else '❌ Degraded'}")
            
            # Log current metrics
            logger.info(f"   • Last task count: {self.last_task_count}")
            logger.info(f"   • Last worker count: {self.last_worker_count}")





