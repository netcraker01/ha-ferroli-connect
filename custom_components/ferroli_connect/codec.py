"""Ferroli boiler packet codec based on the shipped Topband SDK classes.

Frame and TLV layout follows MessagePacket, Message, BoilerID,
BoilerRelayState, and WarmingWaterTargetTemperature in the APK export.
"""
from __future__ import annotations

import time
import math

PACKET_CONTROL = 2
PACKET_QUERY = 1
BOILER_ID_FLAG = 8228  # Message.BOILER_ID
BOILER_ID = 1431677610  # BoilerID default (0x5555554a)
RELAY_FLAG = 8209
HEATING_ACTIVE_FLAG = 8224  # 0x2020; observed 0=active, 1=inactive
HEATING_TARGET_FLAG = 8203  # Read-only telemetry flag (0x200B)
HEATING_CURRENT_FLAG = 0x2000
HEATING_DHW_CURRENT_FLAG = 0x2002
HEATING_DHW_TARGET_FLAG = 0x2003  # Read-only telemetry flag
HEATING_MODULATION_FLAG = 0x2023  # Boiler heating modulation percentage
ACS_MIN_FLAG = 0x200A
ACS_MAX_FLAG = 0x2009
THERMOSTAT_ID_FLAG = 0x0017  # ThermostatID2.java: super((short) 23), decimal.
THERMOSTAT_MANUAL_TEMP_FLAG = 0x1004  # 0x1010 is stored by the cloud but ignored by the thermostat
THERMOSTAT_MODE_FLAG = 0x100F


def parse_thermostat_points(payload) -> dict[str, float | bool]:
    """Parse thermostat points and only derive a source-backed active target."""
    if not isinstance(payload, dict):
        return {}
    def temperature(point: str):
        raw = payload.get(point)
        if (not isinstance(raw, str) or len(raw) != 4 or
                any(character not in "0123456789abcdefABCDEF" for character in raw)):
            return None
        value = int(raw, 16) / 10
        return value if value <= 100 else None

    def byte_value(point: str):
        raw = payload.get(point)
        if (not isinstance(raw, str) or len(raw) != 2 or
                any(character not in "0123456789abcdefABCDEF" for character in raw)):
            return None
        return int(raw, 16)

    parsed = {}
    current = temperature("1000")
    base_target = temperature("1001")
    if current is not None:
        parsed["room_temperature"] = current
    if base_target is not None:
        parsed["room_base_target_temperature"] = base_target

    saved_override = temperature("1017")
    if saved_override is not None:
        parsed["room_override_target_temperature"] = saved_override
    override_active = byte_value("1016")

    # Mode 1 is manual; 1004 is the manual setpoint and 1001 the one the thermostat
    # is running on (live-verified 2026-11-28: writing 1004 moves 1001, 1010 does not).
    # In auto mode 0 only an active override (1016 == 1) with 1017 set
    # establishes an effective target. Program schedule bands are not
    # derivable without the thermostat's version/time-program data.
    mode = byte_value("100F")
    if mode in (0, 1, 4):
        parsed["room_mode"] = mode
    manual_target = temperature("1004")
    if manual_target is not None:
        parsed["room_manual_target_temperature"] = manual_target
    if mode == 1:
        effective = temperature("1001")
        if effective is not None:
            parsed["room_target_temperature"] = effective
    elif mode == 0 and override_active == 1:
        effective = temperature("1017")
        if effective is not None:
            parsed["room_target_temperature"] = effective
    return parsed


def thermostat_id(sdid: str) -> bytes:
    if (not isinstance(sdid, str) or len(sdid) != 8 or
            any(c not in "0123456789abcdefABCDEF" for c in sdid)):
        raise ValueError("Thermostat child ID must be exactly 8 hexadecimal characters")
    return bytes.fromhex(sdid)


def thermostat_temperature_command(sdid: str, temperature: float, **header) -> bytes:
    try:
        value = float(temperature)
    except (TypeError, ValueError):
        raise ValueError("Temperature must be a finite value from 5 to 35 °C") from None
    if not math.isfinite(value) or not 5 <= value <= 35:
        raise ValueError("Temperature must be a finite 0.1 °C step from 5 to 35 °C")
    deci = round(value * 10)
    if abs(value * 10 - deci) > 1e-7:
        raise ValueError("Temperature must be a finite 0.1 °C step from 5 to 35 °C")
    # APK ThermostatActivity chains the point before thermostat(therid); the SDK
    # appends TLVs in call order (MessagePacket.java:84-85,154-156).
    return control_packet([(THERMOSTAT_MANUAL_TEMP_FLAG, deci.to_bytes(2, "big")),
                           (THERMOSTAT_ID_FLAG, thermostat_id(sdid))], **header)


def thermostat_mode_command(sdid: str, mode: int, **header) -> bytes:
    """Build the verified manual-heat/off thermostat mode command."""
    if mode not in (1, 4):
        raise ValueError("Only explicit manual (1) and off (4) modes are supported")
    return control_packet([(THERMOSTAT_MODE_FLAG, bytes((mode,))),
                           (THERMOSTAT_ID_FLAG, thermostat_id(sdid))], **header)


def tlv(flag: int, payload: bytes) -> bytes:
    if not 0 <= flag <= 0xFFFF or len(payload) > 0xFFFF:
        raise ValueError("TLV value out of range")
    return flag.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload


def control_packet(messages: list[tuple[int, bytes]], *, msg_id: int = 1,
                   timestamp_ms: int | None = None, timezone_offset_hours: int = 12) -> bytes:
    """Build exact 22-byte header plus TLVs; dynamic fields are injectable for fixtures."""
    if not 0 <= msg_id <= 255 or not 0 <= timezone_offset_hours <= 255:
        raise ValueError("header value out of range")
    stamp = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= stamp <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("timestamp out of range")
    body = b"".join(tlv(flag, data) for flag, data in messages)
    return (PACKET_CONTROL.to_bytes(2, "big") + bytes((19, msg_id, 1)) +
            bytes(8) + stamp.to_bytes(8, "big") + bytes((timezone_offset_hours,)) + body)


def query_packet(*, msg_id: int = 1, timestamp_ms: int | None = None,
                 timezone_offset_hours: int = 12) -> bytes:
    """Build the APK's full type-1 QUERY frame, including its repeated TLV.

    BoilerActivity.initData() supplies these zero/default values in this order.
    MessagePacket.addMessage() and toBytes() preserve both flag-8203 entries
    (target temperature and max setting); do not deduplicate this APK quirk.
    """
    packet = control_packet(
        [(0x2000, b"\x00\x00"),       # warmingWaterRealTemp(0)
         (0x200B, b"\x00\x00"),       # warmingWaterTargetTemp(0)
         (0x2007, b"\x00"),            # warmingMaxTemp(0)
         (0x2008, b"\x00"),            # warmingMinTemp(0)
         (0x200B, b"\x00\x00"),       # warmingMaxSetPoint(0), same APK flag
         (0x2002, b"\x00\x00"),       # bathWaterRealTemp(0)
         (0x2003, b"\x00\x00"),       # bathWaterTargetTemp(0)
         (0x2009, b"\x00"),            # bathMaxTemp(0)
         (0x200A, b"\x00"),            # bathMinTemp(0)
         (0x2022, b"\x01"),            # windPressureSwitch(true)
         (0x2018, b"\x00"),            # winterSummerMode(0)
         (0x201B, b"\x01"),            # waterSupplySwitch(true)
         (0x201A, b"\x00"),            # flameStatus(false)
         (0x2020, b"\x01"),            # warmingUpStatus(true)
         (0x200E, b"\x00"),            # deviceError()
         (0x200D, b"\x00"),            # ErrorInfo()
         (0x201C, b"\x00"),            # fuelGasType()
         (0x201E, b"\x00"),            # deviceFunctionType()
         (0x201D, b"\x00"),            # deviceInstallationType()
         (0x200F, b"\x00"),            # boilerControllType()
         (0x2019, b"\x00"),            # waterPressure(0.0)
         (0x2023, b"\x00"),            # pwmValue(0)
         (0x2027, b"\x00\x00"),       # ReturnWaterTemp(0)
         (0x2028, b"\x00\x00"),       # OutWaterTemp(0)
         (0x200C, b"\x00\x00"),       # outTemp(0.0)
         (0x2025, b"\x00"),            # FlueTemperature(0)
         (0x2026, b"\x00\x00"),       # WaterRate(0.0)
         (0x2004, b"\x00"),            # outTemperaturePayState(0)
         (BOILER_ID_FLAG, BOILER_ID.to_bytes(4, "big")),  # boiler()
         ],
        msg_id=msg_id, timestamp_ms=timestamp_ms,
        timezone_offset_hours=timezone_offset_hours)
    return PACKET_QUERY.to_bytes(2, "big") + packet[2:]


def parse_packet(packet: bytes) -> tuple[int, dict[int, bytes]]:
    """Strictly parse header/TLVs; reject truncation and duplicate flags."""
    if len(packet) < 22:
        raise ValueError("packet shorter than header")
    kind = int.from_bytes(packet[:2], "big")
    values: dict[int, bytes] = {}
    info_length = packet[2]
    if info_length < 19:
        raise ValueError("invalid info section length")
    cursor = 3 + info_length
    if cursor > len(packet):
        raise ValueError("info section exceeds packet")
    while cursor < len(packet):
        if len(packet) - cursor < 4:
            raise ValueError("truncated TLV header")
        flag = int.from_bytes(packet[cursor:cursor + 2], "big")
        size = int.from_bytes(packet[cursor + 2:cursor + 4], "big")
        cursor += 4
        if size > len(packet) - cursor:
            raise ValueError("truncated TLV payload")
        if flag in values:
            raise ValueError("duplicate TLV flag")
        values[flag] = packet[cursor:cursor + size]
        cursor += size
    return kind, values


def relay_command(open_relay: bool, **header) -> bytes:
    # Source: builder.boilerRelayState(2) opens; every other integer closes.
    status = 2 if open_relay else 1
    return control_packet([(BOILER_ID_FLAG, BOILER_ID.to_bytes(4, "big")),
                           (RELAY_FLAG, bytes((status,)))], **header)


def relay_is_open(payload: bytes) -> bool | None:
    if len(payload) != 1:
        return None
    value = payload[0]
    if value == 2:
        return True
    if value in (1, 16):
        return False
    return None


def target_temperature(payload: bytes) -> float | None:
    if len(payload) != 2:
        return None
    return int.from_bytes(payload, "big") / 10


def _boiler_target_command(flag: int, temp_c: float, **header) -> bytes:
    if isinstance(temp_c, bool) or not isinstance(temp_c, (int, float)):
        raise ValueError("temperature must be a finite whole number of Celsius")
    value = float(temp_c)
    if not math.isfinite(value) or not value.is_integer() or not 0 <= value <= 6553.5:
        raise ValueError("temperature must be a finite whole number of Celsius")
    deci_c = int(value * 10)
    # TLV order and payload were live readback-verified on a real boiler 2026-11-09.
    return control_packet([(flag, deci_c.to_bytes(2, "big")),
                           (BOILER_ID_FLAG, BOILER_ID.to_bytes(4, "big"))], **header)


def target_command(temp_c: float, **header) -> bytes:
    return _boiler_target_command(HEATING_TARGET_FLAG, temp_c, **header)


def dhw_target_command(temp_c: float, **header) -> bytes:
    return _boiler_target_command(HEATING_DHW_TARGET_FLAG, temp_c, **header)


def decode_boiler_telemetry(packet: bytes) -> dict:
    """Decode source-mapped BoilerBean TLVs from response or push frames."""
    kind, fields = parse_packet(packet)
    if kind not in (3, 0x1004):
        raise ValueError("telemetry must be a response or push packet")
    boiler_id = fields.get(BOILER_ID_FLAG)
    if boiler_id is not None and boiler_id != BOILER_ID.to_bytes(4, "big"):
        raise ValueError("packet belongs to a different boiler")
    state = {}
    if RELAY_FLAG in fields:
        value = relay_is_open(fields[RELAY_FLAG])
        if value is not None:
            state["relay_open"] = value
    if HEATING_TARGET_FLAG in fields:
        value = target_temperature(fields[HEATING_TARGET_FLAG])
        if value is not None:
            state["target_temperature"] = value
            state["heating_water_target_temperature"] = value
    if HEATING_CURRENT_FLAG in fields and len(fields[HEATING_CURRENT_FLAG]) == 2:
        state["current_temperature"] = int.from_bytes(
            fields[HEATING_CURRENT_FLAG], "big") / 10
    for flag, key in ((HEATING_DHW_CURRENT_FLAG, "dhw_current_temperature"),
                      (HEATING_DHW_TARGET_FLAG, "dhw_target_temperature")):
        raw = fields.get(flag)
        if raw is not None:
            value = target_temperature(raw)
            if value is not None:
                state[key] = value
    raw = fields.get(HEATING_MODULATION_FLAG)
    if raw is not None:
        if len(raw) != 1 or raw[0] > 100:
            raise ValueError("invalid heating modulation percentage")
        state["heating_modulation"] = raw[0]
    if HEATING_ACTIVE_FLAG in fields and len(fields[HEATING_ACTIVE_FLAG]) == 1:
        value = fields[HEATING_ACTIVE_FLAG][0]
        if value in (0, 1):
            state["heating_active"] = value == 0
    for flag, key in ((0x2008, "min_temperature"), (0x2007, "max_temperature")):
        raw = fields.get(flag)
        if raw is not None and len(raw) == 1 and raw[0] > 0:
            state[key] = raw[0]
    for flag, key in ((ACS_MIN_FLAG, "dhw_min_temperature"),
                      (ACS_MAX_FLAG, "dhw_max_temperature")):
        raw = fields.get(flag)
        if raw is not None and len(raw) == 1 and raw[0] > 0:
            state[key] = raw[0]
    if not state:
        raise ValueError("response has no recognized boiler telemetry")
    return state
