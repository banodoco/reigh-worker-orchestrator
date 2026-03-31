"""Storage and network-volume helpers for RunPod workers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from .api import get_network_volumes

logger = logging.getLogger(__name__)


class RunpodStorageMixin:
    """Storage expansion and health-check methods shared by RunpodClient."""

    def _expand_network_volume(self, volume_id: str, new_size_gb: int) -> bool:
        try:
            url = f"https://rest.runpod.io/v1/networkvolumes/{volume_id}"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            data = {"size": new_size_gb}

            logger.info(f"📦 Expanding network volume {volume_id} to {new_size_gb} GB...")
            response = requests.patch(url, json=data, headers=headers)

            if response.status_code == 200:
                logger.info(f"✅ Successfully expanded volume to {new_size_gb} GB")
                return True

            logger.error(f"❌ Failed to expand volume: {response.status_code} - {response.text}")
            return False
        except Exception as exc:
            logger.error(f"❌ Error expanding volume: {exc}")
            return False

    def _check_and_expand_storage(self, storage_name: str, volume_id: str, min_free_gb: int = 50) -> bool:
        try:
            volumes = get_network_volumes(self.api_key)
            volume_info = next((v for v in volumes if v.get("id") == volume_id), None)

            if not volume_info:
                logger.warning(f"⚠️  Could not find volume info for {volume_id}")
                return True

            current_size_gb = volume_info.get("size", 0)
            logger.info(f"📊 Storage '{storage_name}': {current_size_gb} GB total")

            if current_size_gb < 100:
                new_size = current_size_gb + min_free_gb
                logger.warning(f"⚠️  Storage '{storage_name}' is only {current_size_gb} GB")
                logger.info(f"🔧 Expanding to {new_size} GB to ensure adequate space...")

                if self._expand_network_volume(volume_id, new_size):
                    logger.info("✅ Storage expansion successful")
                    return True

                logger.warning("⚠️  Storage expansion failed, continuing anyway...")
                return True

            logger.info(f"✅ Storage '{storage_name}' has adequate capacity ({current_size_gb} GB)")
            return True
        except Exception as exc:
            logger.warning(f"⚠️  Error checking storage space: {exc}, continuing anyway...")
            return True

    def check_storage_health(
        self,
        storage_name: str,
        volume_id: str,
        active_runpod_id: str,
        min_free_gb: int = 50,
        max_percent_used: int = 85,
    ) -> Dict[str, Any]:
        try:
            logger.info(f"📦 STORAGE_HEALTH Checking '{storage_name}' via pod {active_runpod_id}")

            volumes = get_network_volumes(self.api_key)
            volume_info = next((v for v in volumes if v.get("id") == volume_id), None)

            api_total_gb = None
            if volume_info:
                api_total_gb = volume_info.get("size", 0)
                logger.info(f"📦 STORAGE_HEALTH API reports '{storage_name}': {api_total_gb} GB total")

            check_command = """
            echo "=== WORKSPACE STORAGE ==="
            df -h /workspace 2>/dev/null | tail -1
            echo ""
            echo "=== WORKSPACE USAGE DETAILS ==="
            df -BG /workspace 2>/dev/null | tail -1
            echo ""
            echo "=== LARGEST DIRECTORIES ==="
            du -sh /workspace/*/ 2>/dev/null | sort -rh | head -10
            """

            result = self.execute_command_on_worker(active_runpod_id, check_command, timeout=30)
            if not result or result[0] != 0:
                logger.error(f"📦 STORAGE_HEALTH SSH failed for '{storage_name}': {result}")
                return {
                    "healthy": False,
                    "needs_expansion": False,
                    "message": f"SSH check failed: {result}",
                    "error": True,
                }

            raw_output = result[1] or ""
            logger.info(f"📦 STORAGE_HEALTH Raw output for '{storage_name}':\n{raw_output}")

            total_gb = 0
            used_gb = 0
            free_gb = 0
            percent_used = 0

            def parse_size(size_value: str) -> int:
                normalized = size_value.strip()
                if normalized.endswith("T"):
                    return int(float(normalized[:-1]) * 1024)
                if normalized.endswith("G"):
                    return int(float(normalized[:-1]))
                if normalized.endswith("M"):
                    return int(float(normalized[:-1]) / 1024)
                if normalized.endswith("K"):
                    return 0
                return int(normalized)

            for line in raw_output.split("\n"):
                if "/workspace" in line and not line.startswith("==="):
                    parts = line.split()
                    if len(parts) >= 5:
                        try:
                            if "G" in parts[1] or "T" in parts[1]:
                                total_gb = parse_size(parts[1])
                                used_gb = parse_size(parts[2])
                                free_gb = parse_size(parts[3])
                                percent_str = parts[4].rstrip("%")
                                percent_used = int(percent_str) if percent_str.isdigit() else 0
                                break
                        except (ValueError, IndexError) as exc:
                            logger.warning(f"📦 STORAGE_HEALTH Could not parse df output: {exc}")

            healthy = True
            needs_expansion = False
            message = f"OK: {free_gb}GB free ({100 - percent_used}% available)"

            if percent_used >= max_percent_used:
                healthy = False
                needs_expansion = True
                message = f"CRITICAL: {percent_used}% used, only {free_gb}GB free!"
                logger.error(f"📦 STORAGE_HEALTH ❌ '{storage_name}' {message}")
            elif free_gb < min_free_gb:
                healthy = False
                needs_expansion = True
                message = f"LOW SPACE: Only {free_gb}GB free (need {min_free_gb}GB)"
                logger.warning(f"📦 STORAGE_HEALTH ⚠️  '{storage_name}' {message}")
            else:
                logger.info(f"📦 STORAGE_HEALTH ✅ '{storage_name}' {message}")

            return {
                "healthy": healthy,
                "needs_expansion": needs_expansion,
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "percent_used": percent_used,
                "message": message,
                "raw_df": raw_output,
                "api_total_gb": api_total_gb,
            }
        except Exception as exc:
            logger.error(f"📦 STORAGE_HEALTH Error checking '{storage_name}': {exc}")
            return {
                "healthy": False,
                "needs_expansion": False,
                "message": f"Error: {exc}",
                "error": True,
            }

    def _get_storage_volume_id(self, storage_name: Optional[str] = None) -> Optional[str]:
        if storage_name is None:
            storage_name = self.storage_name
            if self._storage_volume_id is not None:
                return self._storage_volume_id

        if not storage_name:
            logger.info("No storage name configured")
            return None

        logger.info(f"Looking up storage volume: {storage_name}")
        volumes = get_network_volumes(self.api_key)

        for volume in volumes:
            if volume.get("name") == storage_name:
                volume_id = volume.get("id")
                dc_info = volume.get("dataCenter", {})
                logger.info(f"Found storage '{storage_name}' (ID: {volume_id})")
                logger.info(f"  Size: {volume.get('size')}GB")
                logger.info(f"  Location: {dc_info.get('name')} ({dc_info.get('location')})")

                if storage_name == self.storage_name:
                    self._storage_volume_id = volume_id

                return volume_id

        logger.warning(f"Storage '{storage_name}' not found. Available volumes:")
        for volume in volumes:
            dc_info = volume.get("dataCenter", {})
            logger.warning(f"  • {volume.get('name')} (ID: {volume.get('id')}) - {volume.get('size')}GB")
            logger.warning(f"    Location: {dc_info.get('name')} ({dc_info.get('location')})")

        return None


__all__ = ["RunpodStorageMixin"]
