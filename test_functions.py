#!/usr/bin/env python3
"""
Test script for Polar Feeder functions without hardware dependencies.

This script tests the software components that don't require Raspberry Pi hardware:
- Configuration loading and validation
- CSV logging functionality
- Finite State Machine logic (with mocked actuator)
- Basic actuator interface (with mocked RF transmission)

Hardware-dependent components are mocked to allow testing on any platform.
"""

import sys
import os
import time
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def test_config_loader():
    """Test configuration loading and validation."""
    print("Testing config loader...")

    from polar_feeder.config.loader import load_config

    # Test with example config
    config_path = Path(__file__).parent / 'config' / 'config.example.json'
    if not config_path.exists():
        config_path = Path(__file__).parent / 'src' / 'pi' / 'polar_feeder' / 'config' / 'config.example.json'

    if not config_path.exists():
        print("ERROR: Could not find config.example.json")
        return False

    try:
        cfg = load_config(str(config_path))
        print(f"✓ Config loaded successfully: stillness={cfg.stillness.trigger_threshold}, logging={cfg.logging.enabled}")

        # Test validation
        assert 0 <= cfg.stillness.trigger_threshold <= 1, "Stillness threshold out of range"
        assert cfg.logging.telemetry_hz > 0, "Telemetry Hz must be positive"
        print("✓ Config validation passed")
        return True
    except Exception as e:
        print(f"✗ Config loader failed: {e}")
        return False

def test_csv_logger():
    """Test CSV logging functionality."""
    print("\nTesting CSV logger...")

    from polar_feeder.logging.csv_logger import CsvSessionLogger, pick_log_dir

    try:
        # Test log directory selection
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = pick_log_dir(temp_dir)
            print(f"✓ Log directory selected: {log_dir}")

            # Test logger creation and basic operations
            session_id = "test_session_123"
            log_path = log_dir / f"session_{session_id}.csv"

            logger = CsvSessionLogger(log_path=log_path, session_id=session_id, test_id="unit_test")
            logger.open()

            # Test event logging
            logger.log_event(
                state="TEST",
                enable_flag=1,
                command="START",
                result="SUCCESS",
                notes="Unit test event"
            )

            # Test telemetry logging
            logger.log_telemetry(
                state="TEST",
                enable_flag=1,
                stillness_raw=0.5,
                stillness_filtered=0.45
            )

            logger.close()

            # Verify file was created and has content
            assert log_path.exists(), "Log file was not created"
            content = log_path.read_text()
            assert "timestamp_utc" in content, "CSV header missing"
            assert "test_session_123" in content, "Session ID missing"
            assert "unit_test" in content, "Test ID missing"

            print(f"✓ CSV logging successful, file created: {log_path}")
            print(f"  File size: {len(content)} characters")

        return True
    except Exception as e:
        print(f"✗ CSV logger test failed: {e}")
        return False

def test_feeder_fsm():
    """Test Finite State Machine with mocked actuator."""
    print("\nTesting Feeder FSM...")

    try:
        from polar_feeder.feeder_fsm import FeederFSM, State

        # Create mock actuator
        mock_actuator = Mock()
        mock_actuator.extend = Mock()
        mock_actuator.retract = Mock()

        # Create FSM
        fsm = FeederFSM(actuator=mock_actuator, retract_delay_ms=500, cooldown_s=1.0)

        # Create FSM with feeding distance
        fsm = FeederFSM(actuator=mock_actuator, retract_delay_ms=500, cooldown_s=1.0, feeding_distance_m=0.5)

        # Test initial state
        assert fsm.state == State.IDLE, f"Initial state should be IDLE, got {fsm.state}"
        print("✓ FSM initialized in IDLE state")

        # Test enable -> extend sequence
        start_time = time.monotonic()
        fsm.tick(enable=True, threat=False, now=start_time)
        assert fsm.state == State.LURE, f"Should transition to LURE when enabled, got {fsm.state}"
        mock_actuator.extend.assert_called_once()
        print("✓ FSM transitions IDLE -> LURE on enable")

        # Test feeding distance -> FEEDING state
        fsm.tick(enable=True, threat=False, radar_distance_m=0.3, now=start_time + 0.1)  # Bear close!
        assert fsm.state == State.FEEDING, f"Should transition to FEEDING when bear is close, got {fsm.state}"
        print("✓ FSM transitions LURE -> FEEDING when bear reaches feeding distance")

        # Test manual retraction from FEEDING
        success = fsm.manual_retract(now=start_time + 0.2)
        assert success, "Manual retract should succeed from FEEDING state"
        assert fsm.state == State.IDLE, f"Should return to IDLE after manual retract, got {fsm.state}"
        assert mock_actuator.retract.call_count == 1, "Should have called retract once"
        print("✓ FSM manual retraction from FEEDING state works")

        # Reset and test threat detection -> retract sequence
        fsm = FeederFSM(actuator=mock_actuator, retract_delay_ms=500, cooldown_s=1.0, feeding_distance_m=0.5)
        fsm.tick(enable=True, threat=False, now=start_time)
        fsm.tick(enable=True, threat=True, now=start_time + 0.1)
        assert fsm.state == State.RETRACT_WAIT, f"Should transition to RETRACT_WAIT on threat, got {fsm.state}"
        print("✓ FSM transitions LURE -> RETRACT_WAIT on threat")

        # Reset and test vision motion fusion
        fsm = FeederFSM(actuator=mock_actuator, retract_delay_ms=500, cooldown_s=1.0, motion_threshold=5.0, feeding_distance_m=0.5)
        fsm.tick(enable=True, threat=False, now=start_time)
        fsm.tick(enable=True, threat=False, motion_magnitude=6.0, now=start_time + 0.1)
        assert fsm.state == State.RETRACT_WAIT, "Should transition to RETRACT_WAIT on vision motion threat"
        print("✓ FSM transitions LURE -> RETRACT_WAIT on vision motion")

        # Test retract delay timing
        fsm.tick(enable=True, threat=False, now=start_time + 0.4)  # Before delay
        assert fsm.state == State.RETRACT_WAIT, "Should still be in RETRACT_WAIT before delay"

        fsm.tick(enable=True, threat=False, now=start_time + 0.6)  # After delay
        assert fsm.state == State.COOLDOWN, f"Should transition to COOLDOWN after delay, got {fsm.state}"
        mock_actuator.retract.assert_called_once()
        print("✓ FSM handles retract delay timing correctly")

        # Test cooldown -> idle
        fsm.tick(enable=True, threat=False, now=start_time + 1.6)  # After cooldown
        assert fsm.state == State.IDLE, f"Should return to IDLE after cooldown, got {fsm.state}"
        print("✓ FSM transitions COOLDOWN -> IDLE after cooldown")

        # Test disable safety
        fsm.tick(enable=False, threat=False, now=start_time + 2.0)
        assert fsm.state == State.IDLE, "Should stay in IDLE when disabled"
        # Should have called retract again for safety
        assert mock_actuator.retract.call_count == 2, "Should retract on disable for safety"
        print("✓ FSM safety: retracts immediately when disabled")

        return True
    except Exception as e:
        print(f"✗ FSM test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_vision_fusion():
    """Test vision detection parsing and fusion behavior."""
    print("\nTesting vision and sensor fusion...")

    try:
        from polar_feeder.vision import VisionTracker, SensorFusion

        tracker = VisionTracker()

        # Test CSV format parsing
        d1 = tracker.parse_line("1,100.0,50,200,30,180")
        assert d1 is not None
        tracker.update(d1)

        d2 = tracker.parse_line("1,100.1,55,205,35,185")
        motion = tracker.compute_motion(d2)
        assert motion > 0.0, f"Expected positive motion, got {motion}"

        # Test YOLO output.txt format parsing
        yolo_output = """Detection number: 2
Time: 100.2
Xmin = 32
Xmax = 182
Ymin = 52
Ymax = 202"""
        
        d3 = tracker.parse_yolo_output(yolo_output)
        assert d3 is not None, "YOLO parser failed"
        assert d3.detection_id == 2, f"Expected id 2, got {d3.detection_id}"
        assert d3.timestamp == 100.2, f"Expected timestamp 100.2, got {d3.timestamp}"
        assert d3.xmin == 32.0, f"Expected xmin 32.0, got {d3.xmin}"
        assert d3.ymax == 202.0, f"Expected ymax 202.0, got {d3.ymax}"
        print("✓ YOLO output.txt format parsing works correctly")

        fusion = SensorFusion(motion_threshold=5.0)
        fusion.update_radar(100.0)
        fusion.update_vision(100.1)
        assert fusion.in_sync(0.5)
        assert fusion.fused_threat(False, motion)

        print("✓ Vision parsing/motion and fusion behavior OK")
        return True
    except Exception as e:
        print(f"✗ Vision fusion test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_actuator_interface():
    """Test actuator interface with mocked RF transmission."""
    print("\nTesting actuator interface...")

    try:
        # Mock the transmitting functions
        with patch('polar_feeder.transmittingfunc.transmit1') as mock_tx1, \
             patch('polar_feeder.transmittingfunc.transmit2') as mock_tx2, \
             patch('polar_feeder.transmittingfunc.transmitwithdelay') as mock_tx_delay:

            from polar_feeder.actuator import Actuator

            # Test actuator creation
            act = Actuator(retract_delay_s=1.0)
            print("✓ Actuator created successfully")

            # Test open/close (should be no-ops)
            act.open()
            act.close()
            print("✓ Actuator open/close work (no-ops as expected)")

            # Test extend
            act.extend()
            mock_tx1.assert_called_once()
            print("✓ Actuator extend calls transmit1")

            # Test retract
            act.retract()
            mock_tx2.assert_called_once()
            print("✓ Actuator retract calls transmit2")

            # Test extend_then_retract
            act.extend_then_retract(delay_s=0.5)
            mock_tx_delay.assert_called_once_with(0.5)
            print("✓ Actuator extend_then_retract calls transmitwithdelay")

        return True
    except Exception as e:
        print(f"✗ Actuator test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_demo_mode():
    """Test the demo mode functionality."""
    print("\nTesting demo mode simulation...")

    try:
        from polar_feeder.logging.csv_logger import CsvSessionLogger
        import random

        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            session_id = "demo_test_123"
            log_path = log_dir / f"session_{session_id}.csv"

            logger = CsvSessionLogger(log_path=log_path, session_id=session_id)
            logger.open()

            # Simulate demo mode: log session start
            logger.log_event(
                state="ENABLED",
                enable_flag=1,
                command="enable_flag",
                result="0->1",
                notes="Demo session start"
            )

            # Simulate telemetry logging
            still_f = 0.0
            alpha = 0.2
            for i in range(5):
                still_raw = random.random()
                still_f = (1 - alpha) * still_f + alpha * still_raw

                logger.log_telemetry(
                    state="ENABLED",
                    enable_flag=1,
                    stillness_raw=still_raw,
                    stillness_filtered=still_f
                )

            # Simulate session end
            logger.log_event(
                state="IDLE",
                enable_flag=0,
                command="enable_flag",
                result="1->0",
                notes="Demo session end"
            )

            logger.close()

            # Verify the log file
            content = log_path.read_text()
            lines = content.strip().split('\n')
            assert len(lines) >= 7, f"Expected at least 7 lines (header + 2 events + 5 telemetry), got {len(lines)}"

            # Check for expected content
            assert "event" in content and "telemetry" in content, "Missing event/telemetry types"
            assert "demo_test_123" in content, "Session ID missing"
            assert "Demo session start" in content, "Start event missing"
            assert "Demo session end" in content, "End event missing"

            print(f"✓ Demo mode simulation successful: {len(lines)} lines logged")
            print(f"  Sample telemetry entries: {sum(1 for line in lines if 'telemetry' in line)}")

        return True
    except Exception as e:
        print(f"✗ Demo mode test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("=" * 60)
    print("POLAR FEEDER UNIT TESTS")
    print("=" * 60)
    print("Testing software components without hardware dependencies")
    print()

    tests = [
        test_config_loader,
        test_csv_logger,
        test_feeder_fsm,
        test_actuator_interface,
        test_demo_mode,
    ]

    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"✗ {test_func.__name__} crashed: {e}")
            results.append(False)

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    for i, (test_func, result) in enumerate(zip(tests, results)):
        status = "PASS" if result else "FAIL"
        print(f"{status}: {test_func.__name__}")

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! The software components are working correctly.")
        print("Note: Hardware-dependent features (GPIO, BLE, radar) require Raspberry Pi.")
    else:
        print("❌ Some tests failed. Check the output above for details.")

    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())