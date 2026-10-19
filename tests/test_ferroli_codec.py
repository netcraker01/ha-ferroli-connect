"""Standard-library codec checks with fixed vectors derived from source semantics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "ferroli_connect"))
from codec import (HEATING_TARGET_FLAG, parse_packet, query_packet, relay_command,
                   relay_is_open, target_command, target_temperature,
                   decode_boiler_telemetry, parse_thermostat_points,
                   thermostat_mode_command, thermostat_temperature_command,
                   dhw_target_command)


def test_relay_vectors():
    # header(type=2, info=19, id=1, ver=1, wifi=0, appms=0, tz=12)
    prefix = bytes.fromhex("0002130101") + bytes(16) + bytes.fromhex("0c")
    assert relay_command(True, timestamp_ms=0, timezone_offset_hours=12) == prefix + bytes.fromhex(
        "202400045555aaaa2011000102")
    assert relay_command(False, timestamp_ms=0, timezone_offset_hours=12) == prefix + bytes.fromhex(
        "202400045555aaaa2011000101")


def test_heating_target_vector_and_decode():
    packet = target_command(55, timestamp_ms=0, timezone_offset_hours=12)
    assert packet[-14:] == bytes.fromhex("200b00020226 202400045555aaaa")
    kind, fields = parse_packet(packet)
    assert kind == 2 and target_temperature(fields[HEATING_TARGET_FLAG]) == 55
    assert relay_is_open(bytes((2,))) is True
    assert relay_is_open(bytes((16,))) is False
    assert relay_is_open(bytes((0,))) is None


def test_query_packet_vector():
    # Full BoilerActivity.initData() APK chain; type 1 query includes duplicate
    # 0x200B TLVs for heating target and max setting, both encoded as zero.
    packet = query_packet(msg_id=7, timestamp_ms=0, timezone_offset_hours=12)
    assert packet == bytes.fromhex(
        "0001130701" + "00" * 16 + "0c"
        "200000020000"
        "200b00020000"
        "2007000100"
        "2008000100"
        "200b00020000"
        "200200020000"
        "200300020000"
        "2009000100"
        "200a000100"
        "2022000101"
        "2018000100"
        "201b000101"
        "201a000100"
        "2020000101"
        "200e000100"
        "200d000100"
        "201c000100"
        "201e000100"
        "201d000100"
        "200f000100"
        "2019000100"
        "2023000100"
        "202700020000"
        "202800020000"
        "200c00020000"
        "2025000100"
        "202600020000"
        "2004000100"
        "202400045555aaaa"
    )
    assert int.from_bytes(packet[:2], "big") == 1
    assert packet.count(bytes.fromhex("200b00020000")) == 2


def test_malformed_tlvs_and_ranges():
    for malformed in (b"", bytes.fromhex("0002130101") + bytes(16),
                      bytes.fromhex("0002130101") + bytes(16) + bytes.fromhex("0c20000002ff")):
        try:
            parse_packet(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed packet accepted")
    try:
        target_command(7000, timestamp_ms=0)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range temperature accepted")


def test_info_length_controls_tlv_offset():
    standard = relay_command(True, timestamp_ms=0, timezone_offset_hours=12)
    extended = standard[:2] + bytes((20,)) + standard[3:22] + b"\x00" + standard[22:]
    assert parse_packet(extended)[1][0x2011] == b"\x02"


def test_binary_telemetry_vector():
    response = bytes.fromhex(
        "0003130101" + "00" * 16 + "0c"
        "202400045555aaaa2011000102"
        "200b00020226"
        "2000000201f4"
        "200800011e2007000155"
    )
    assert decode_boiler_telemetry(response) == {
        "relay_open": True, "target_temperature": 55.0,
        "current_temperature": 50, "min_temperature": 30, "max_temperature": 85,
    }
    try:
        decode_boiler_telemetry(response[:1] + b"\x02" + response[2:])
    except ValueError:
        pass
    else:
        raise AssertionError("control packet accepted as telemetry")


def test_heating_active_flag_values_and_validation():
    def response(payload):
        return (bytes.fromhex("0003130101") + bytes(16) + bytes.fromhex("0c") +
                bytes.fromhex("20200001") + payload)

    assert decode_boiler_telemetry(response(b"\x00"))["heating_active"] is False
    assert decode_boiler_telemetry(response(b"\x01"))["heating_active"] is True
    for payload in (b"\x02", b"", b"\x00\x01"):
        try:
            decode_boiler_telemetry(response(payload))
        except ValueError:
            pass
        else:
            raise AssertionError("invalid heating-active flag accepted")


def test_heating_and_dhw_water_temperatures_preserve_tenths():
    response = bytes.fromhex(
        "0003130101" + "00" * 16 + "0c"
        "2000000201fe"  # heating water: 51.0 C
        "200b000200d7"  # heating target: 21.5 C
        "20020002019c"  # DHW water: 41.2 C
        "2003000200b9"  # DHW target: 18.5 C
    )
    state = decode_boiler_telemetry(response)
    assert state["current_temperature"] == 51.0
    assert state["target_temperature"] == 21.5
    assert state["dhw_current_temperature"] == 41.2
    assert state["dhw_target_temperature"] == 18.5
    for flag in (0x2000, 0x2002, 0x2003):
        malformed = (bytes.fromhex("0003130101") + bytes(16) + b"\x0c" +
                     flag.to_bytes(2, "big") + b"\x00\x01\x01")
        try:
            decode_boiler_telemetry(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid-length water TLV accepted")


def test_boiler_target_vectors_whole_degrees_and_live_bounds():
    prefix = bytes.fromhex("0002130101") + bytes(16) + bytes.fromhex("0c")
    assert target_command(55, timestamp_ms=0) == prefix + bytes.fromhex(
        "200b00020226 202400045555aaaa")
    assert dhw_target_command(50, timestamp_ms=0) == prefix + bytes.fromhex(
        "2003000201f4 202400045555aaaa")
    response = bytes.fromhex("0003130101") + bytes(16) + bytes.fromhex(
        "0c200a000114200900013c200800011e2007000155")
    state = decode_boiler_telemetry(response)
    assert state["dhw_min_temperature"] == 20
    assert state["dhw_max_temperature"] == 60
    assert state["min_temperature"] == 30 and state["max_temperature"] == 85
    for value in (20.5, float("nan"), float("inf"), -1, True, "50"):
        try:
            dhw_target_command(value, timestamp_ms=0)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid boiler target accepted")


def test_thermostat_point_hex_vectors_and_invalid_values():
    assert parse_thermostat_points({"1000": "010a", "1001": "00d7"}) == {
        "room_temperature": 26.6, "room_base_target_temperature": 21.5}
    assert parse_thermostat_points({"1000": "0000", "1001": "0064"}) == {
        "room_temperature": 0.0, "room_base_target_temperature": 10.0}
    assert parse_thermostat_points({"1000": "xyz!", "1001": "00"}) == {}
    assert parse_thermostat_points({"1000": "0 01"}) == {}
    assert parse_thermostat_points({"1000": "03e9"}) == {}
    assert parse_thermostat_points(None) == {}
    manual = parse_thermostat_points({"100F": "01", "1010": "00d7", "1001": "0078"})
    assert manual["room_target_temperature"] == 21.5
    assert manual["room_base_target_temperature"] == 12.0
    override = parse_thermostat_points({"100F": "00", "1016": "01", "1017": "00d7"})
    assert override["room_target_temperature"] == 21.5
    standby = parse_thermostat_points({"1000": "010a", "1001": "0078", "100F": "04",
                                       "1010": "00d2", "1016": "00", "1017": "00d7"})
    assert standby["room_override_target_temperature"] == 21.5
    assert "room_target_temperature" not in standby
    assert "room_override_target_temperature" not in parse_thermostat_points(
        {"1017": "bad!"})
    assert "room_target_temperature" not in parse_thermostat_points(
        {"100F": "04", "1016": "01", "1017": "00d7"})
    assert "room_target_temperature" not in parse_thermostat_points(
        {"100F": "00", "1016": "00", "1001": "0078"})
    assert "room_target_temperature" not in parse_thermostat_points(
        {"100F": "01", "1010": "bad!"})


def test_room_thermostat_control_vectors_and_validation():
    prefix = bytes.fromhex("0002130101") + bytes(16) + bytes.fromhex("0c")
    assert thermostat_temperature_command("1234abcd", 21.5, timestamp_ms=0) == prefix + bytes.fromhex(
        "1010000200d7001700041234abcd")  # SDK ThermostatID2 flag is decimal 23.
    assert thermostat_mode_command("1234abcd", 4, timestamp_ms=0) == prefix + bytes.fromhex(
        "100f000104001700041234abcd")
    for bad_id in ("123", "1234567g", "123456789"):
        try:
            thermostat_mode_command(bad_id, 1, timestamp_ms=0)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid thermostat child ID accepted")
    for value in (4.9, 35.1, float("nan"), float("inf"), 21.55):
        try:
            thermostat_temperature_command("1234abcd", value, timestamp_ms=0)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid thermostat target accepted")
    for value in (0, 2):
        try:
            thermostat_mode_command("1234abcd", value, timestamp_ms=0)
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported thermostat mode accepted")


if __name__ == "__main__":
    test_relay_vectors()
    test_heating_target_vector_and_decode()
    test_query_packet_vector()
    test_malformed_tlvs_and_ranges()
    test_info_length_controls_tlv_offset()
    test_binary_telemetry_vector()
    test_heating_active_flag_values_and_validation()
    test_heating_and_dhw_water_temperatures_preserve_tenths()
    test_thermostat_point_hex_vectors_and_invalid_values()
    print("Ferroli codec checks passed")
