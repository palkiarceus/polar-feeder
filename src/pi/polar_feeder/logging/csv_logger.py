"""
CSV Logging Module

Provides session-based CSV logging for Polar Feeder telemetry and events.
Combines both telemetry (sensor readings) and event (command/state change) logs into a single
CSV file per session, differentiated by event_type column.

The logger is designed to:
- Create one CSV file per feeder operation session
- Track both continuous telemetry (sensor readings) and discrete events (commands, state changes)
- Use ISO 8601 timestamps in UTC for all records
- Maintain data integrity with immediate flushing after each write
- Support optional test IDs for associating logs with specific test runs

Fields recorded:
- timestamp_utc: ISO 8601 UTC timestamp for precise chronological ordering
- session_id: Unique identifier for this feeder operation session
- test_id: Optional ID linking to a specific test run (empty for production)
- event_type: Either 'event' (discrete) or 'telemetry' (continuous)
- state: Current FSM state (IDLE, LURE, RETRACT_WAIT, COOLDOWN)
- enable_flag: Boolean flag (0/1) indicating if feeder is enabled
- stillness_raw: Raw stillness sensor reading (0-1 scale, empty for events)
- stillness_filtered: Filtered/smoothed stillness reading (empty for events)
- radar_enabled: Boolean flag (0/1) for radar threat detection status
- radar_zone: Zone/region identifier from radar (e.g., 'ZONE_A', empty if not applicable)
- command: BLE command issued (empty for telemetry)
- result: Result of command execution (empty for telemetry)
- fault_code: Error code if command failed (empty if no fault)
- notes: Optional human-readable annotation

Usage:
    logger = CsvSessionLogger(log_path=Path('logs/session.csv'), session_id='abc123')
    logger.open()
    logger.log_telemetry(state='LURE', enable_flag=1, stillness_raw=0.5, stillness_filtered=0.45)
    logger.log_event(state='LURE', enable_flag=1, command='EXTEND', result='SUCCESS')
    logger.close()
"""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def iso_now() -> str:
    """
    Generate current UTC timestamp in ISO 8601 format with millisecond precision.
    
    Used for all CSV log timestamps to ensure chronological ordering and consistency
    across different systems/timezones. ISO 8601 format is sortable and machine-readable.
    
    Returns:
        str: Current time as ISO 8601 string with milliseconds (e.g., '2026-03-26T12:34:56.789+00:00')
    
    Example:
        >>> ts = iso_now()
        >>> ts  # '2026-03-26T12:34:56.789+00:00'
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class CsvSessionLogger:
    """
    Session-based CSV logger for telemetry and event data.
    
    Creates and maintains a single CSV file for one feeder operation session, recording both
    continuous telemetry (sensor readings at regular intervals) and discrete events (commands,
    state transitions, faults). Records are timestamped with UTC ISO 8601 timestamps.
    
    Thread Safety:
        Not thread-safe. If called from multiple threads, synchronization must be done at caller level.
    
    Design:
        - One file handle per session for efficiency
        - Immediate flush after each write to prevent data loss
        - Lazy initialization of CSV writer until open() called
        - Graceful fallback directory selection via pick_log_dir()
    
    Attributes:
        log_path (Path): Absolute path to output CSV file
        session_id (str): Unique identifier for this session (e.g., '729c074b')
        test_id (str): Optional test run identifier (default: empty string for production)
        _writer (csv.DictWriter | None): CSV writer instance (None until open() called)
        _fh (file object | None): File handle (None until open() called)
    
    Example:
        logger = CsvSessionLogger(
            log_path=Path('logs/session_20260326.csv'),
            session_id='729c074b',
            test_id='test_001'
        )
        logger.open()
        logger.log_telemetry(state='IDLE', enable_flag=0, stillness_raw=0.2, stillness_filtered=0.18)
        logger.log_event(state='LURE', enable_flag=1, command='EXTEND', result='SUCCESS')
        logger.close()
    """
    # Public configuration attributes
    log_path: Path  # Path object pointing to output CSV file location
    session_id: str  # Unique session identifier (e.g., timestamp-based like '729c074b')
    test_id: str = ""  # Optional test run identifier for grouping related logs

    # Private state - only initialized when open() is called
    _writer: Optional[csv.DictWriter] = None  # CSV DictWriter for writing rows (None until opened)
    _fh: Optional[Any] = None  # File handle (None until opened)

    def open(self) -> None:
        """
        Open the CSV file for logging and initialize the CSV writer.
        
        If the file doesn't exist, creates parent directories and writes the header row.
        If the file exists, appends to it (useful for resuming sessions).
        
        Must be called before any log_event() or log_telemetry() calls.
        Uses append mode ('a') to support resuming from crashes.
        
        Raises:
            OSError: If unable to create parent directories or write to file
        
        Side Effects:
            - Creates parent directories if they don't exist
            - Opens file in append+newline mode for CSV compatibility
            - Writes header row if file is new
            - Sets _fh and _writer internal state
        """
        # Create parent directories if they don't exist (supports nested log paths)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if this is a new file to decide whether to write header
        is_new = not self.log_path.exists()
        
        # Open file in append mode so we can resume logging to existing files
        # newline="" is required by Python csv module for proper line handling
        self._fh = self.log_path.open("a", newline="", encoding="utf-8")

        # Define all fields that will appear in CSV
        # Order matters for readability; time-based fields first, then FSM state, then data
        fieldnames = [
            "timestamp_utc",      # ISO 8601 UTC timestamp
            "session_id",         # Session identifier for grouping logs
            "test_id",            # Optional test run ID
            "event_type",         # 'event' or 'telemetry'
            "state",              # FSM state (IDLE, LURE, RETRACT_WAIT, COOLDOWN)
            "enable_flag",        # Feeder enabled (0/1)
            "stillness_raw",      # Raw sensor reading (empty for events)
            "stillness_filtered", # Processed sensor reading (empty for events)
            "radar_enabled",      # Threat detection active (0/1)
            "radar_zone",         # Radar zone identifier
            "command",            # BLE command issued (empty for telemetry)
            "result",             # Command result (empty for telemetry)
            "fault_code",         # Error code if any (empty for telemetry)
            "notes",              # Human-readable annotation
        ]
        # Create CSV writer with these field names
        self._writer = csv.DictWriter(self._fh, fieldnames=fieldnames)
        
        # If creating new file, write header row
        if is_new:
            self._writer.writeheader()
            self._fh.flush()  # Ensure header is written immediately

    def close(self) -> None:
        """
        Close the CSV file and clean up resources.
        
        Flushes any buffered data to disk before closing. Safe to call even if
        logger was never opened (idempotent).
        
        Side Effects:
            - Flushes file buffer to ensure all data written to disk
            - Closes file handle
            - Clears internal _fh and _writer references
        """
        if self._fh:
            self._fh.flush()  # Ensure all buffered data is written to disk
            self._fh.close()  # Close the file handle
        # Clear references to allow garbage collection
        self._fh = None
        self._writer = None

    def _write(self, row: Dict[str, Any]) -> None:
        """
        Internal helper to write a single row to CSV.
        
        Private method used by log_event() and log_telemetry() to write formatted rows.
        Validates that logger is opened and flushes immediately after each write
        to prevent data loss during crashes.
        
        Args:
            row (Dict[str, Any]): Dictionary mapping fieldnames to values
        
        Raises:
            RuntimeError: If logger not opened (open() not called or close() was called)
        
        Side Effects:
            - Writes row to CSV file
            - Flushes file buffer immediately for crash safety
        """
        # Check that logger is properly initialized
        if not self._writer or not self._fh:
            raise RuntimeError("Logger not opened")  # Must call open() first
        
        # Write the row to CSV (DictWriter handles field ordering)
        self._writer.writerow(row)
        
        # Flush immediately after each write for crash recovery
        # (CSV has all data on disk even if process crashes)
        self._fh.flush()

    def log_event(
        self,
        *,
        state: str,
        enable_flag: int,
        command: str = "",
        result: str = "",
        fault_code: str = "",
        notes: str = "",
        radar_enabled: bool = False,
        radar_zone: str = "",
    ) -> None:
        """
        Log a discrete event (command, state change, fault).
        
        Records significant events like BLE commands, state transitions, or faults.
        Automatically captures current UTC timestamp. Sensor readings (stillness) are
        left blank for events (use log_telemetry() for sensor data).
        
        Args:
            state (str): Current FSM state (IDLE, LURE, RETRACT_WAIT, COOLDOWN)
            enable_flag (int): Feeder enabled flag (0 or 1)
            command (str): BLE command issued (e.g., 'EXTEND', 'RETRACT', empty if state change)
            result (str): Result of command (e.g., 'SUCCESS', 'TIMEOUT', empty if not applicable)
            fault_code (str): Error code if fault occurred (empty for non-fault events)
            notes (str): Optional human-readable note (e.g., 'Threat detected', 'Manual override')
            radar_enabled (bool): Whether radar is currently active (default: False)
            radar_zone (str): Radar detection zone (e.g., 'ZONE_A', empty if not applicable)
        
        Example:
            logger.log_event(
                state='LURE',
                enable_flag=1,
                command='EXTEND',
                result='SUCCESS',
                notes='Arm extended successfully'
            )
        """
        # Build event row with all sensor fields empty (events don't have sensor data)
        self._write({
            "timestamp_utc": iso_now(),  # Current UTC time for chronological ordering
            "session_id": self.session_id,  # Session ID from logger initialization
            "test_id": self.test_id,  # Test ID from logger (may be empty for production)
            "event_type": "event",  # Mark as discrete event (vs 'telemetry')
            "state": state,  # Current FSM state
            "enable_flag": enable_flag,  # Feeder enabled status
            "stillness_raw": "",  # Empty for events (not sensor data)
            "stillness_filtered": "",  # Empty for events (not sensor data)
            "radar_enabled": int(bool(radar_enabled)),  # Convert bool to 0/1 for CSV
            "radar_zone": radar_zone,  # Radar zone identifier
            "command": command,  # BLE command (empty if not command-related)
            "result": result,  # Command result (empty if not command-related)
            "fault_code": fault_code,  # Error code (empty if no fault)
            "notes": notes,  # Optional annotation
        })

    def log_telemetry(
        self,
        *,
        state: str,
        enable_flag: int,
        stillness_raw: float,
        stillness_filtered: float,
        radar_enabled: bool = False,
        radar_zone: str = "",
        notes: str = "",
    ) -> None:
        """
        Log continuous telemetry (sensor readings).
        
        Records sensor readings at regular intervals (typically every 1-5 seconds during
        active operation). Captures the current FSM state and sensor values for later analysis.
        Command-related fields are left blank for telemetry (use log_event() for commands).
        
        Args:
            state (str): Current FSM state (IDLE, LURE, RETRACT_WAIT, COOLDOWN)
            enable_flag (int): Feeder enabled flag (0 or 1)
            stillness_raw (float): Raw stillness sensor reading (0.0-1.0 scale)
            stillness_filtered (float): Filtered/smoothed stillness reading (0.0-1.0 scale)
            radar_enabled (bool): Whether radar is currently armed (default: False)
            radar_zone (str): Radar zone if threat detected (e.g., 'ZONE_A', empty if no threat)
            notes (str): Optional annotation (default: empty)
        
        Example:
            logger.log_telemetry(
                state='LURE',
                enable_flag=1,
                stillness_raw=0.342,
                stillness_filtered=0.318,
                radar_enabled=True,
                radar_zone='ZONE_A'
            )
        """
        # Build telemetry row with command fields empty (telemetry doesn't have commands)
        self._write({
            "timestamp_utc": iso_now(),  # Current UTC time
            "session_id": self.session_id,  # Session ID from logger
            "test_id": self.test_id,  # Test ID (may be empty)
            "event_type": "telemetry",  # Mark as continuous telemetry (vs 'event')
            "state": state,  # Current FSM state
            "enable_flag": enable_flag,  # Feeder enabled status
            "stillness_raw": f"{stillness_raw:.3f}",  # Format to 3 decimal places for consistency
            "stillness_filtered": f"{stillness_filtered:.3f}",  # Format to 3 decimal places
            "radar_enabled": int(bool(radar_enabled)),  # Convert bool to 0/1 for CSV
            "radar_zone": radar_zone,  # Radar zone (empty if no threat)
            "command": "",  # Empty for telemetry (not command-related)
            "result": "",  # Empty for telemetry
            "fault_code": "",  # Empty for telemetry
            "notes": notes,  # Optional annotation
        })


def pick_log_dir(preferred: str) -> Path:
    """
    Select a writable directory for log files with fallback strategy.
    
    Attempts to use the preferred directory (from configuration). If that directory
    is not writable or can't be created, falls back to './logs' in current working
    directory. This provides flexibility across different deployment environments
    (Raspberry Pi, Windows dev machine, restricted filesystem paths).
    
    The function tests writability by attempting to create a temporary file,
    ensuring the chosen directory can actually receive log data.
    
    Args:
        preferred (str): Preferred log directory path from configuration
    
    Returns:
        Path: Absolute or relative path to a confirmed-writable directory
    
    Behavior:
        1. Try to create preferred directory (with parents if needed)
        2. Test writability by creating/deleting temporary file
        3. If preferred works, return it
        4. If preferred fails (any exception), use './logs' fallback
        5. Create fallback directory if it doesn't exist
        6. Return writable fallback path
    
    Example:
        >>> log_dir = pick_log_dir('/var/log/polar-feeder')
        >>> log_dir  # Path('/var/log/polar-feeder') if writable, else Path('logs')
    """
    # Convert string to Path object
    pref = Path(preferred)
    
    # Try to use preferred directory
    try:
        # Create preferred directory with all parent directories
        pref.mkdir(parents=True, exist_ok=True)
        
        # Test writability by creating a temporary file
        testfile = pref / ".write_test"
        testfile.write_text("ok", encoding="utf-8")  # Write test data
        testfile.unlink(missing_ok=True)  # Delete test file
        
        # If we got here, directory is writable
        return pref
    
    except Exception:
        # Preferred directory failed (permissions, disk full, invalid path, etc.)
        # Fall back to ./logs in current working directory
        fallback = Path("logs")
        fallback.mkdir(parents=True, exist_ok=True)  # Create fallback if needed
        return fallback
