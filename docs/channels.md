# Channel specifications

Every entry in `TaskSpec.channels` is a typed dataclass.  The
analog-input, analog-output, digital, and counter/timer channel kinds
are all shipped; multi-sensor specs (RTD / thermistor / strain /
bridge / IEPE) ship too but require an intelligent multi-sensor module
to run on real hardware (see [below](#multi-sensor-inputs)).

Design reference: [design.md §8.2–§8.6][8_2].

## ChannelSpec base

All channel specs share these fields:

| Field              | Default       | Notes                                                |
|--------------------|---------------|------------------------------------------------------|
| `physical_channel` | (required)    | Zero-based channel index on the subsystem.            |
| `name`             | `None`        | Display name; falls back to `f"ch{physical_channel}"`. |
| `unit`             | `None`        | Display unit (informational).                         |
| `metadata`         | `{}`          | Free-form per-channel metadata.                       |

## AnalogInputBase

Common AI knobs shared by every analog-input subclass:

| Field           | Default                       | Notes                                |
|-----------------|-------------------------------|--------------------------------------|
| `channel_type`  | `ChannelType.SINGLE_ENDED`    | SE / differential / pseudo-differential. |
| `gain`          | `1.0`                         | PGA setting.                          |
| `filter`        | `None`                        | Optional analog filter.               |
| `encoding`      | `None`                        | Optional sample-code encoding.        |
| `coupling`      | `None`                        | Optional AC/DC coupling.              |

## AnalogInputVoltage

```python
from dtollib import AnalogInputVoltage

spec = AnalogInputVoltage(
    physical_channel=0,
    name="heat_flux",
    min_val=-10.0,  # volts
    max_val=10.0,
    gain=1.0,
)
```

| Field      | Default | Notes                                  |
|------------|---------|----------------------------------------|
| `min_val`  | `-10.0` | Lower input voltage range (`olDaSetChannelRange`). |
| `max_val`  | `10.0`  | Upper input voltage range.             |

Validation: `min_val < max_val`.

## ThermocoupleInput

```python
from dtollib import ThermocoupleInput, ThermocoupleType, CjcSource

spec = ThermocoupleInput(
    physical_channel=0,
    name="surface_tc_K",
    thermocouple_type=ThermocoupleType.K,
    min_val_degc=-50.0,
    max_val_degc=200.0,
    cjc_source=CjcSource.INTERNAL,
)
```

| Field               | Default             | Notes                                                       |
|---------------------|---------------------|-------------------------------------------------------------|
| `thermocouple_type` | (required)          | NIST letter designation (J/K/T/E/R/S/B/N).                   |
| `min_val_degc`      | (required)          | Lower temperature limit (°C).                                |
| `max_val_degc`      | (required)          | Upper temperature limit (°C).                                |
| `cjc_source`        | `CjcSource.INTERNAL`| Cold-junction-compensation source.                           |
| `units`             | `TemperatureUnit.DEG_C` | Reporting unit. Linearisation emits °C; `DEG_F` / `KELVIN` are accepted but the read path currently reports °C. |

Validation:

- `min_val_degc < max_val_degc`.
- The `[min_val_degc, max_val_degc]` range must fit inside the NIST
  operating envelope for the given TC type (see `dtollib.utils.get_thermocouple_range`).
- The runtime capability check at `session.prepare()` accepts a TC
  channel on a subsystem that either linearises in firmware
  (`OLSSC_RETURNS_FLOATS=True`) **or** exposes a thermocouple
  front-end with a CJC channel (`supports_thermocouples=True`). The
  DT9805 / DT9806 are the latter: the wrapper reads the differential
  thermo-emf plus the CJC sensor (channel 0 at 10 mV/°C) and applies
  the NIST ITS-90 polynomials in software via
  `dtollib.utils.convert_volts_to_temperature`. Configure fails fast
  if no ITS-90 polynomial is implemented for the requested TC type.

## Sensor sentinel handling

DT9805 / DT9806 thermocouple inputs can report three sentinel
conditions in place of a temperature.  The wrapper preserves them on
the `sensor_status` overlay of the `DaqReading` and NaN-fills the
corresponding `values` cell — never coerced into a plausible
temperature.  See [design.md §13.1][13_1] for the rationale.

```python
import math

reading = await session.poll()
for name, status in reading.sensor_status.items():
    if status.value == "sensor_open":
        print(f"{name} TC wire is disconnected")
        assert math.isnan(float(reading.values[name]))
```

## Output channels (DT9806)

The DT9806 adds D/A (AO), digital-input (DIN), and digital-output (DOUT)
subsystems. Output channels carry safety metadata; writes go through
[`DtolSession.write`](safety.md) and are validated **before** any SDK call.

### AnalogOutputVoltage

```python
from dtollib import AnalogOutputVoltage

spec = AnalogOutputVoltage(
    physical_channel=0,
    name="heater_command",
    min_val=-10.0,     # device electrical range, volts
    max_val=10.0,
    safe_min=0.0,      # operator safe band (subset of device range)
    safe_max=5.0,
    requires_confirm=True,
)
```

| Field              | Default | Notes                                                        |
|--------------------|---------|--------------------------------------------------------------|
| `min_val`          | `-10.0` | Lower device output range, volts. Out-of-range writes are always rejected. |
| `max_val`          | `10.0`  | Upper device output range, volts.                            |
| `safe_min`         | `None`  | Lower safe-band bound. `None` = no lower gate.               |
| `safe_max`         | `None`  | Upper safe-band bound. `None` = no upper gate.               |
| `requires_confirm` | `True`  | Every write needs `confirm=True` regardless of the band.     |
| `gain`             | `1.0`   | Output-gain-list entry.                                      |

Validation: `min_val < max_val`; the safe band (when set) must lie inside
`[min_val, max_val]`; `safe_min < safe_max`.

### Digital I/O is port-shaped

DT-Open Layers models digital I/O as **ports**, not individual lines. Each
direction (DIN, DOUT) exposes one or more *ports*; a port is the SDK "channel",
and its bits are the lines. The DT9805/06 have exactly **one 8-bit port** per
direction (8 relays = 8 bits of channel 0). So you declare a
`DigitalOutputPort` / `DigitalInputPort` whose `physical_channel` is the **port
index**, and address individual lines with `DigitalLine(bit=N)` views.

### DigitalOutputPort

```python
from dtollib import DigitalOutputPort, DigitalLine

spec = DigitalOutputPort(
    physical_channel=0,
    name="dout",
    safe_value=0b0000_0000,            # full-port byte held when not driven
    lines=(
        DigitalLine(bit=0, name="relay0"),
        DigitalLine(bit=3, name="armed", requires_confirm=True),
    ),
)
```

| Field              | Default | Notes                                                            |
|--------------------|---------|------------------------------------------------------------------|
| `width`            | `None`  | Port width in bits. `None` → read from the subsystem resolution. |
| `lines`            | `()`    | Optional named `DigitalLine` bit-views.                          |
| `safe_value`       | `None`  | Full-port byte to hold when not driven; seeds the shadow register. `None` = 0. |
| `requires_confirm` | `True`  | Port-level confirm gate; a line may override it.                 |

`DigitalLine` fields: `bit` (required), `name` (defaults to `"<port>.line<bit>"`),
`safe_value`, and `requires_confirm` (`None` inherits the port).

### DigitalInputPort

```python
from dtollib import DigitalInputPort, DigitalLine

spec = DigitalInputPort(
    physical_channel=0,
    name="din",
    lines=(DigitalLine(bit=0, name="door_switch"),),
)
```

Read-only; `poll()` surfaces the raw port byte under the port name plus one bool
per declared line.

### Writing to outputs

```python
async with await open_device(spec, backend=backend, autostart=False) as session:
    # analog output
    await session.write({"heater_command": 3.5}, confirm=True)
    # whole digital port (int byte)
    await session.write({"dout": 0b0000_1001}, confirm=True)
    # individual lines — packed into one port write; untouched lines preserved
    await session.write({"relay0": True}, confirm=True)
    await session.write({"armed": True}, confirm=True)  # per-line confirm gate
```

Per-line writes merge into a per-port **shadow register**, so driving one line
leaves the others as last written. A whole-port byte write in the same call sets
the base; per-line keys then refine specific bits.

See [safety.md](safety.md) for the full `confirm` gate model.

> **Continuous AO waveform output** (`play()`, `WrapMode.SINGLE`/`MULTIPLE`)
> is implemented and unit-tested against the fake backend, but the
> DT9805 / DT9806 D/A is **single-value only** (no FIFO, `OLSSC_SUP_CONTINUOUS=0`),
> so `play()` raises `DtolCapabilityError` on these boards and points you at
> `session.write()`. See [waveform-output.md](waveform-output.md).

## Multi-sensor inputs {#multi-sensor-inputs}

These specs require an intelligent multi-sensor DT module.

> **Requires an intelligent multi-sensor DT module** (DT9828 / DT9829 /
> DT9837). **Not supported on the DT9805 / DT9806** — those A/D subsystems
> report `supports_multisensor = False` and reject every multi-sensor
> setter with ECODE 36. The `TaskBuilder` raises `DtolCapabilityError` at
> configure time for any of these specs on an unsupported subsystem (it
> never reaches the SDK). Real-sensor verification is deferred until such a
> module is on the bench; the configure path is fully exercised against
> `FakeDtolBackend` today.

All multi-sensor specs share `AnalogInputBase`:

| Spec | `IOType` | Key knobs |
|------|----------|-----------|
| `RtdInput` | `RTD` | `rtd_type` (`RtdType`), `r0`, custom Callendar–Van Dusen `a`/`b`/`c` |
| `ThermistorInput` | `THERMISTOR` | Steinhart–Hart `a`/`b`/`c` |
| `ResistanceInput` | `RESISTANCE` | `excitation_source`, `excitation_current_a` |
| `CurrentInput` | `CURRENT` | `min_val`/`max_val` (amps) |
| `IepeInput` | `ACCELEROMETER` | AC coupling (forced), `excitation_current_a` |
| `StrainInput` | `STRAIN_GAGE` | `configuration` (`StrainGageConfiguration`), `gage_factor`, `gage_resistance_ohms`, `excitation_voltage` |
| `BridgeInput` | `BRIDGE` | `configuration` (`BridgeConfiguration`), `nominal_resistance_ohms`, `sensitivity_mv_per_v`, `excitation_voltage` |

### Checking support before you configure

```python
from dtollib import find_subsystems, RtdInput, DtolCapabilityError

# The clean way — query the capability first:
caps = await session.capabilities()           # or backend.query_capabilities(hdass)
if not caps.supports_multisensor:
    print("This board can't do RTD/strain/bridge — needs a DT9828/9829/9837.")

# Or just let configure fail loud on the wrong board:
try:
    async with await open_device(spec_with_rtd, backend=backend) as session:
        ...
except DtolCapabilityError as exc:
    print(exc)   # names the sensor kind + the missing capability
```

### TEDS and strain conversion

Smart transducers (IEEE-1451.4) carry their calibration on-sensor or in a
sidecar file. See [teds.md](teds.md) for the read helpers, and
`strain_from_volts` / `bridge_value_from_volts` for turning a measured
bridge voltage into strain (ε) or the transducer's rated quantity via the
SDK conversions.

[8_2]: design.md#82-channelspec-base
[13_1]: design.md#131-single-value-dataflowsingle_value
