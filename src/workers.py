"""
PC AutoSpec — Background workers (QThread subclasses).
Handles system spec collection, upload, and GPU monitoring.
"""

import time
import logging
from PySide6.QtCore import QThread, Signal


class SpecCollectorWorker(QThread):
    """Collect system specifications in the background."""
    progress = Signal(str)        # phase text ("Detecting CPU...")
    log_message = Signal(str)     # messages for the activity log
    spec_update = Signal(dict)    # partial specs as each section completes
    finished = Signal(dict)       # complete specs dict
    error = Signal(str)           # error message
    stress_test_started = Signal()         # emitted just before stress test begins
    stress_test_temp = Signal(float)       # live temp sample during stress test
    stress_test_finished = Signal()        # emitted when stress test ends

    def __init__(self, skip_categories=None, parent=None):
        super().__init__(parent)
        self.skip_categories = skip_categories or set()

    def run(self):
        specs = None
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                from system_specs import get_system_specs
                specs = get_system_specs(
                    log_callback=lambda msg: self.log_message.emit(msg),
                    progress_callback=lambda phase: self.progress.emit(phase),
                    spec_callback=lambda s: self.spec_update.emit(dict(s)),
                    skip_categories=self.skip_categories,
                )

                # Run advanced health checks (temperatures, disk speed, etc.)
                try:
                    self.progress.emit("Running advanced health checks...")
                    self.log_message.emit("Running advanced health checks...\n")
                    from diagnostics import collect_advanced_health_summary
                    adv_health = collect_advanced_health_summary(
                        log_callback=lambda msg: self.log_message.emit(f"{msg}\n"),
                        stress_started_callback=lambda: self.stress_test_started.emit(),
                        stress_temp_callback=lambda t: self.stress_test_temp.emit(t),
                        stress_finished_callback=lambda: self.stress_test_finished.emit(),
                        skip_categories=self.skip_categories,
                    )
                    specs['AdvancedHealth'] = adv_health

                    # Bridge device manager errors from AdvancedHealth
                    dm_errors = adv_health.pop('_device_manager_errors', None)
                    if dm_errors:
                        specs['DeviceManagerErrors'] = dm_errors

                    self.log_message.emit("  Advanced health checks complete\n")
                except Exception as e:
                    logging.warning(f"Advanced health checks failed: {e}")

                self.finished.emit(specs)
            finally:
                import gc
                del specs
                gc.collect()
                pythoncom.CoUninitialize()
        except Exception as e:
            logging.error(f"Spec collection failed: {e}", exc_info=True)
            self.error.emit(str(e))


class UploadWorker(QThread):
    """Resolve ticket and upload diagnostic note to RepairDesk."""
    progress = Signal(str, str)    # (message, tag) for activity log
    finished = Signal(bool, str)   # (success, message_or_id)
    confirm_customer = Signal(dict)  # emitted with customer info before upload

    def __init__(self, api, ticket_id, note_html, skip_confirmation=False, parent=None):
        super().__init__(parent)
        self.api = api
        self.ticket_id = ticket_id
        self.note_html = note_html
        self._confirmed = True if skip_confirmation else None

    def set_confirmed(self, confirmed):
        """Called from GUI thread after customer confirmation dialog."""
        self._confirmed = confirmed

    def run(self):
        try:
            self.progress.emit("Resolving ticket ID...", "")
            ticket_info = self.api.get_ticket_customer(self.ticket_id)
            resolved_id = ticket_info['id']
            customer = ticket_info.get('customer_name', 'Unknown')
            device = ticket_info.get('device', '')
            self.progress.emit(
                f"Found ticket T-{self.ticket_id}: {customer}"
                + (f" — {device}" if device else ""), "success")

            # Emit customer info and wait for GUI confirmation unless this
            # ticket was already confirmed earlier in the flow.
            if self._confirmed is not True:
                self._confirmed = None
                self.confirm_customer.emit(ticket_info)
                # Spin-wait for GUI thread to respond (max 60s)
                import time
                deadline = time.monotonic() + 60
                while self._confirmed is None and time.monotonic() < deadline:
                    time.sleep(0.1)

            if not self._confirmed:
                self.finished.emit(False, "Upload cancelled by tech")
                return

            self.progress.emit("Uploading to RepairDesk...", "")
            result = self.api.add_diagnostic_note(resolved_id, self.note_html)

            if result.get('success'):
                self.finished.emit(True, str(resolved_id))
            else:
                self.finished.emit(False, result.get('message', 'Unknown error'))
        except Exception as e:
            logging.error(f"Upload failed: {e}", exc_info=True)
            self.finished.emit(False, str(e))


class GpuMonitorWorker(QThread):
    """Poll GPU metrics periodically."""
    metrics_updated = Signal(dict)

    def __init__(self, gpu_name, parent=None):
        super().__init__(parent)
        self.gpu_name = gpu_name
        self._running = True

    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                from system_specs import _get_gpu_detailed_metrics
                while self._running:
                    try:
                        metrics = _get_gpu_detailed_metrics(self.gpu_name)
                        if metrics:
                            self.metrics_updated.emit(metrics)
                    except Exception:
                        pass
                    # Sleep in small increments so stop() is responsive
                    for _ in range(100):
                        if not self._running:
                            break
                        time.sleep(0.1)
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            logging.debug(f"GPU monitor error: {e}")

    def stop(self):
        self._running = False
        self.wait(3000)


class UpdateCheckWorker(QThread):
    """Check GitHub Releases for a newer packaged installer."""
    finished = Signal(dict)
    error = Signal(str)

    def run(self):
        try:
            from updater import check_for_updates
            self.finished.emit(check_for_updates())
        except Exception as e:
            logging.error(f"Update check failed: {e}", exc_info=True)
            self.error.emit(str(e))


class UpdateDownloadWorker(QThread):
    """Download the latest Windows installer in the background."""
    progress = Signal(int, str)   # (percent, message)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, update_info, parent=None):
        super().__init__(parent)
        self.update_info = update_info

    def run(self):
        try:
            from updater import download_update
            result = download_update(
                self.update_info,
                progress_callback=lambda pct, msg: self.progress.emit(pct, msg),
            )
            self.finished.emit(result)
        except Exception as e:
            logging.error(f"Update download failed: {e}", exc_info=True)
            self.error.emit(str(e))
